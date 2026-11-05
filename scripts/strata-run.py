#!/usr/bin/env python3
"""Start Strata out of the conda package, with pixi owning the dependencies.

The upstream installer (setup.py) assumes a plain ``./setup.sh`` checkout; several of the
things it does there are replaced here:

* **Its command line.** What Strata runs with is ``strata.ini`` in the project root -- every
  key of it is an argument for setup.py, and ``pixi run strata-help`` prints the whole flag
  list. A command line after ``--`` and the ``STRATA_*`` variables override the file, in that
  order. Nothing here has an opinion about a model, a context or a VRAM reserve: the file
  does.
* **Dependencies.** Its step 3, and ``pip_cuda_libs()`` for a ready-made engine, run
  ``python -m pip install <pinned wheels>`` into whichever interpreter is running -- a
  ``.venv`` upstream, the conda prefix here. Installing into a conda prefix is the one
  thing this integration must not do, so ``pip_install`` is replaced with a check that the
  packages the recipe declares are importable. NVIDIA's wheels are not needed at all: the
  CUDA libraries the engine links (cuBLAS, cuRAND, cudart) come from conda.
* **The model files.** setup.py downloads every shard into ``<data-dir>/models``, with its
  own ``.done`` marks -- a second 55-111 GB copy of files ``llama-server -hf`` already
  keeps in the Hub cache. They are fetched into ``~/.cache/huggingface/hub`` instead
  (huggingface_hub, at the revision setup.py pins) and handed over through ``--gguf-dir``
  as a folder of symlinks, so llamacpp and Strata read the same blobs.
* **The MTP draft layer's build inputs.** setup.py keeps the ~5 GB of checkpoint tensors it
  range-reads (its own ``tensors`` folder) and the GGUF packed from them, beside the ``rt/``
  folder the engine actually loads. Neither is read again -- ``rt/experts.bin`` going missing
  is setup.py's only reason to rebuild the layer, and it re-fetches the tensors then -- so
  they are checked against the pinned SHA256s (upstream's #327 check) and deleted once the
  layer is built. ``keep-mtp-inputs`` in strata.ini (or ``STRATA_KEEP_MTP_INPUTS=1``) keeps
  them.
* **The DFlash2 drafter.** setup.py fetches the MTP layer but leaves the alternative drafter
  to the Terminal's ``strata drafting fetch`` -- a subcommand of a CLI this recipe does not
  package -- so ``drafter = dflash2`` prepared a model with no drafter on disk at all and it
  started with "DFlash2 ... no drafter is downloaded" in its log. The pack, its draft
  vocabulary and the smaller Q4_0 selector this engine reads are downloaded here instead
  (``drafting_fetch``), on ``strata-install`` and on a start, once: the pinned SHA-256 is
  checked and an interrupted download resumes.
* **The compile at the first start.** setup.py compiles the engine itself when the one it
  finds does not match the sources it hashes (``ENGINE_SOURCES``), installing NVIDIA's CUDA
  Toolkit with apt and sudo when there is no ``nvcc`` -- the recipe compiles it here, and
  the runtime env has the CUDA libraries but no compiler. ``install_build_tools`` is replaced
  with the message that says so, so a mismatch can never reach a password prompt (see below).

Everything else stays setup.py's: the expert pack, the MTP draft layer, the run config.
What it writes is all inside ``$CONDA_PREFIX`` -- the pack, the MTP layer and the config
under ``$CONDA_PREFIX/strata-data`` and ``$CONDA_PREFIX/opt/strata``, its per-user settings
redirected there with ``XDG_CONFIG_HOME`` too.

    python scripts/strata-run.py               # prepare if needed, then start
    python scripts/strata-run.py --no-start    # prepare only, do not start
    python scripts/strata-run.py --print port         # the port strata.ini says, nothing else
    pixi run start-strata -- --calibrate       # anything unknown is forwarded to setup.py
    pixi run strata-install -- --keep-mtp-inputs

setup.py's start path hands its run config's engine arguments to the engine verbatim, so a
flag read only by its setup path (``--kv``, ``--parallel``, ``--vision``, ...) does nothing on
a start. The part of the command line it only reads when preparing is recorded in
``<data-dir>/strata-prepared.json``, and a change to it in strata.ini re-prepares the model
once instead of being silently ignored. The context is the exception this wrapper can fix
without a re-prepare: it rewrites ``--max-context`` in the run config before every start.
Sampling is not this wrapper's at all: serve/server.py applies the model card's own recommended
set (``MODEL_CARD_SAMPLING``) to a request that names none.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from strata_common import (
    INSTALL_FLAGS,
    PRINT_RESOLVERS,
    WRAPPER_FLAGS,
    app_dir,
    config_flag,
    flag_name,
    flag_value,
    has_flag,
    ini_path,
    load_setup,
    parse_args,
    pin_context,
    prepare_signature,
    prepared_args,
    record_prepared,
    render,
    run_setup,
    signature_diff,
    strata_args,
    without,
)

#: A pinned file URL as setup.py builds it: <endpoint>/<owner>/<repo>/resolve/<sha>/<path>
RESOLVE_URL = re.compile(
    r"^(?P<endpoint>https?://[^/]+)/(?P<repo>[^/]+/[^/]+)/resolve/"
    r"(?P<revision>[0-9a-f]{40})/(?P<path>.+)$"
)
#: The MTP draft layer's build inputs: the 31 raw tensors `tools/mtp_fetch.py` range-reads out
#: of the 360 GB BF16 checkpoint (~5 GB) and the GGUF `tools/mtp_pack.py` made of them. The
#: engine loads neither -- `--mtp` takes the `rt/` folder `tools/mtp_rt.py` writes -- and
#: setup.py's one reason to rebuild them is `rt/experts.bin` going missing, which re-fetches
#: the raw tensors anyway. `verify` makes the raw tensors worth their keep (#327: a mirror
#: that ignores Range returns the shard's start, and nothing else would catch it).
MTP_INPUTS = ("tensors", "mtp-q2_0.gguf")


def applies(line: str) -> bool:
    """Is this requirement line meant for this platform?  requirements.txt carries
    `colorama==4.0.6; sys_platform == "win32"`, which no Linux env will ever install."""
    if ";" not in line:
        return True
    try:
        from packaging.markers import InvalidMarker, Marker
    except ImportError:  # packaging is not installed (it comes in with huggingface_hub)
        return True
    try:
        return bool(Marker(line.split(";", 1)[1].strip()).evaluate())
    except InvalidMarker:  # a marker we cannot read: assume it applies
        return True


def replace_pip(setup: ModuleType) -> None:
    """setup.py's pip step, replaced: conda's packages are the same ones, already there."""

    def pip_install(packages, what):
        wanted = [line for line in packages if applies(line)]
        missing = [
            name for name in map(setup.req_name, wanted) if not setup._installed(name)
        ]
        if missing:
            setup.warn(
                f"{what}: {', '.join(missing)} installed neither by conda nor by pip -- add "
                "them to the run requirements in pixi-recipes/strata/recipe.yaml"
            )
        else:
            setup.ok(f"{what} (conda)")

    setup.pip_install = pip_install


def replace_build_tools(setup: ModuleType) -> None:
    """setup.py's CUDA-toolkit step, replaced: this integration never compiles at runtime.

    ``build_engine`` reaches it the moment the installed engine does not match the sources
    setup.py hashes, and its own answer is to install NVIDIA's toolkit -- ``sudo dpkg -i
    /tmp/cuda-keyring.deb`` and ``sudo apt-get install cuda-toolkit-13-0`` -- after a "Install
    them now? [y]" that blocks on the password when a terminal is on stdin (see
    ``strata.log``). The engine comes from the recipe, so a mismatch is the packaging's
    problem, not something to fix by installing a compiler: ``fail`` names it instead. The
    start path catches the SystemExit and starts the installed engine, exactly as it did for
    a compile that failed.
    """

    def install_build_tools(gpu, yes):
        setup.fail(
            "the installed engine does not match its sources, and compiling it here needs "
            "the CUDA Toolkit this env does not carry",
            "rebuild the recipe (`pixi install -e strata`): the sources the recipe installs "
            "and the ones setup.py's ENGINE_SOURCES hashes have drifted apart",
        )

    setup.install_build_tools = install_build_tools


def own_path_table(setup: ModuleType) -> dict:
    """The families setup.py gives an engine path of their own: ``OWN_PATH_FAMILIES``, empty for
    a setup.py that has none (0.1.43.1 and older).  ``PREVIEW_FAMILIES`` is upstream's own alias
    for the same table, so either name is accepted.
    """
    return (
        getattr(setup, "OWN_PATH_FAMILIES", None)
        or getattr(setup, "PREVIEW_FAMILIES", None)
        or {}
    )


def family_table(setup: ModuleType) -> dict:
    """Every family setup.py can prepare, as one `{name: entry}` table.

    ``FAMILIES`` is Qwen3.8-Flash-Next's releases: the same architecture, weights packed into
    ``<data-dir>/packs/<tag>``. ``OWN_PATH_FAMILIES`` (0.1.44's Qwen3.6-35B-A3B) is the family
    with an engine path of its own: no pack, no expert profile, no separate MTP draft layer --
    the engine reads the GGUF as it is and drafts with the file's own MTP block. setup.py
    routes it to its own ``install_own_path``, whose ``--family`` choices are
    ``list(FAMILIES) + list(PREVIEW_FAMILIES)``, so both tables are read.
    """
    return {**setup.FAMILIES, **own_path_table(setup)}


def resolve_family(setup: ModuleType, family: str) -> str:
    """The family as setup.py spells it, or the choices it would have offered."""
    known = {name.lower(): name for name in family_table(setup)}
    if family.lower() not in known:
        sys.exit(f"family {family!r}: setup.py knows {', '.join(family_table(setup))}")
    family = known[family.lower()]
    if family_table(setup)[family].get("ready"):
        # `nvfp4` (0.2.x): ready-made Strata packs from a third-party repository, not GGUF
        # shards -- there is no <hf> or <file> to walk. This wrapper fetches the model files
        # itself and hands setup.py a folder of symlinks (--gguf-dir), so a family with
        # nothing to fetch has no path here. setup.py's own setup path would prepare it.
        sys.exit(
            f"family {family!r} ships ready-made Strata packs, not GGUF shards: this wrapper "
            "prepares the model files itself, and has no path for a pack it does not build"
        )
    return family


def resolve_model(setup: ModuleType, family: str, model: str) -> str:
    """The quant, checked against the family that ships it.

    A pack family lists its sizes in ``MODELS``, each with the ``families`` that ship it. An
    own-path family is in ``MODELS`` not at all -- it carries its own ``models`` table (its
    ``UD-Q4_K_XL`` is another repository's single file, not Unsloth's Flash-Next file of the
    same name), which is what its own ``install_own_path`` reads.
    """
    fam = family_table(setup)[family]
    table = fam.get("models") or setup.MODELS
    known = {name.lower(): name for name in table}
    if model.lower() not in known:
        # only the family's own sizes: MODELS holds every family's (Q2_0 … Q8_0, the Gyro and NVFP4 files),
        # and a message that lists them all reads as if the family shipped them
        offered = [
            name
            for name, meta in table.items()
            if table is not setup.MODELS
            or family in meta.get("families", ("qwen", "swift"))
        ]
        sys.exit(f"model {model!r}: {family} offers {', '.join(offered)}")
    model = known[model.lower()]
    if table is setup.MODELS:
        families = setup.MODELS[model].get("families", ("qwen", "swift"))
        if family not in families:
            sys.exit(
                f"model {model!r} exists only for {', '.join(families)}, not {family!r}"
            )
    return model


def model_files(setup: ModuleType, family: str, model: str, vision: str) -> list[str]:
    """The files setup.py downloads for this model: every shard, and the image encoder."""
    fam = family_table(setup)[family]
    if family in own_path_table(setup):
        # an own-path family: one GGUF, no shard pattern to walk (`fam["file"]` has no {i})
        shards = [fam["file"].format(q=model)]
    else:
        shards = [
            setup.model_file(fam, model, i)
            for i in range(1, setup.model_shards(fam, model) + 1)
        ]
    urls = [fam["hf"].format(q=model) + name for name in shards]
    if vision not in ("no", "none", "off") and fam.get("vision") is not False:
        urls.append(fam["mmproj_hf"] + fam["mmproj"])
    return urls


def snapshot(setup: ModuleType, repo: str, revision: str, path: str) -> Path:
    """One file of the Hub cache, downloading it there when it is not cached yet."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import HfHubHTTPError

    try:
        root = snapshot_download(repo_id=repo, revision=revision, allow_patterns=[path])
    except HfHubHTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if revision == "main" or status != 404:
            raise
        # What setup.py does with hf_unpinned(): a revision the repository no longer has
        # falls back to its current files.
        setup.warn(
            f"{repo}: revision {revision[:7]} is gone from the repository; taking its current file"
        )
        root = snapshot_download(repo_id=repo, revision="main", allow_patterns=[path])
    return Path(root) / path


def gguf_dir_path(setup: ModuleType, family: str, model: str) -> Path:
    """Where the --gguf-dir folder of this model lives, whether or not it is there yet."""
    prefix = Path(os.environ["CONDA_PREFIX"])
    return (
        prefix / "strata-models" / (family_table(setup)[family]["tag"] + model).lower()
    )


def missing_model_files(
    setup: ModuleType, family: str, model: str, vision: str
) -> list[str]:
    """The files the --gguf-dir folder should hold that are not there.

    setup.py is handed that folder, never the Hub cache, and it records the paths in the run
    config it leaves behind, so the server starts the engine with whatever they say. A pruned
    Hub cache takes the blobs and leaves these symlinks dangling, and nothing repairs them on
    a start -- the vision encoder then dies on the first picture with "failed to open GGUF
    file". Only a preparation rewrites the folder, which is what naming a missing file here
    forces. An empty list is one stat per file.
    """
    where = gguf_dir_path(setup, family, model)
    missing = []
    for url in model_files(setup, family, model, vision):
        match = RESOLVE_URL.match(url)
        name = (match["path"] if match else url).rsplit("/", 1)[-1]
        if not (where / name).exists():
            missing.append(name)
    return missing


def gguf_dir(setup: ModuleType, family: str, model: str, vision: str) -> Path:
    """The folder of symlinks into the cache that setup.py is pointed at with --gguf-dir."""
    from huggingface_hub.constants import HF_HUB_CACHE

    where = gguf_dir_path(setup, family, model)
    where.mkdir(parents=True, exist_ok=True)
    print(f"  model files from {HF_HUB_CACHE}", flush=True)
    for url in model_files(setup, family, model, vision):
        match = RESOLVE_URL.match(url)
        if match is None:
            sys.exit(
                f"cannot read {url}: setup.py builds its model URLs differently now"
            )
        source = snapshot(setup, match["repo"], match["revision"], match["path"])
        link = where / Path(match["path"]).name
        if link.is_symlink() and os.readlink(link) == str(source):
            continue
        link.unlink(missing_ok=True)
        link.symlink_to(source)
        print(f"  {link.name} -> {source}", flush=True)
    return where


def dir_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def verify_mtp(setup: ModuleType, mtp: Path) -> bool:
    """The fetched tensors against the checkpoint's SHA256 (setup.py's own #327 check)."""
    done = subprocess.run(
        [
            sys.executable,
            str(setup.ROOT / "tools" / "mtp_fetch.py"),
            "verify",
            "--out",
            str(mtp),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        tail = (done.stdout + done.stderr).strip().splitlines()[-6:]
        setup.warn("mtp_fetch.py verify: " + " | ".join(line.strip() for line in tail))
    return done.returncode == 0


def fetch_dflash2(
    setup: ModuleType,
    pairs: list[tuple[str, str | None]],
    data: Path,
    config: Path,
    family: str,
) -> None:
    """The DFlash2 drafter, which no task of this project would otherwise download.

    setup.py fetches the MTP draft layer as part of preparing a model and leaves DFlash2 to
    ``strata drafting fetch``, a subcommand of a CLI this recipe does not package: a
    ``drafter = dflash2`` install prepared with no drafter on disk and then served
    "Drafting: off (no drafter is downloaded yet)". The pack, the draft vocabulary the engine
    reads it with and the smaller Q4_0 selector this recipe's engine knows are fetched here
    instead, on both paths (``strata-install`` and a start that had nothing to prepare).
    ``drafting_fetch.ensure`` keeps a pack that is already there, checks the pinned SHA-256
    and resumes an interrupted download, so a second run costs one ``stat``.

    Only an explicit choice of DFlash2 fetches anything -- ``drafter = dflash2`` in
    strata.ini, or ``"drafting": "dflash2"`` in the run config. ``auto`` keeps the MTP as its
    fallback and downloads nothing; it says "the DFlash2 drafter isn't downloaded yet" and
    starts the MTP instead.
    """
    from strata_control import drafting, drafting_fetch

    asked = (flag_value(pairs, "--drafter") or "").lower()
    chosen = ""
    try:
        record = json.loads(config.read_text(encoding="utf-8"))
        chosen = str(record.get("drafting") or "").lower()
    except (OSError, ValueError):
        pass  # no run config yet, or one this wrapper cannot read
    if asked != "dflash2" and chosen != "dflash2":
        return
    if not drafting.supported_model(None, family):
        setup.warn(
            f"DFlash2 is for Qwen3.8-Flash-Next only (qwen, gyro, unsloth), not {family}: "
            "no drafter is fetched"
        )
        return

    # A MiB at a time is how `transfer` reports, so a tenth is what is worth printing.
    state = {"step": "", "tenth": -1}

    def report(step: str, done: int, total: int) -> None:
        tenth = 10 * done // total if total else 10
        if step == state["step"] and (tenth == state["tenth"] or done >= total):
            return
        state["step"], state["tenth"] = step, tenth
        if done < total and total > 1_000_000:  # a byte count, not a packing step
            print(
                f"  {step} {done / 1e9:.2f} / {total / 1e9:.2f} GB "
                f"({100 * done / total:.0f}%)",
                flush=True,
            )
        else:
            print(f"  {step}", flush=True)

    engine = Path(setup.ROOT) / "engine" / setup.EXE
    try:
        drafting_fetch.ensure(data, progress=report)
        if drafting.engine_reads_selector_q4(engine):
            drafting_fetch.convert_selector(data, progress=report)
    except Exception as exc:  # noqa: BLE001 - a download must not lose a prepared model
        setup.warn(f"the DFlash2 drafter could not be fetched: {exc}")
        setup.warn(
            "the model still starts; `pixi run strata-install` fetches it again, and a "
            "partial download resumes"
        )
        return
    setup.ok(
        f"DFlash2 drafter: {drafting.pack_path(data, engine)} "
        f"({drafting.vram_gb(data, engine):.2f} GB of VRAM)"
    )


def trim_mtp_inputs(setup: ModuleType, data: Path) -> None:
    """Reclaim the MTP layer's build inputs, once `rt/` is built and they check out."""
    mtp = data / "mtp"
    rt = mtp / "rt"
    # dense.txt is written last in the MTP layer, as setup.py's own build checks do
    if not (rt / "experts.bin").exists() or not (rt / "dense.txt").exists():
        return  # nothing built (a failed or an older data dir): leave it to setup.py
    inputs = [mtp / name for name in MTP_INPUTS if (mtp / name).exists()]
    if not inputs:
        return
    if (mtp / "tensors").is_dir() and not verify_mtp(setup, mtp):
        setup.warn(
            "keeping the MTP build inputs: setup.py fetches them again on the next run"
        )
        return
    freed = sum(dir_bytes(path) for path in inputs)
    for path in inputs:
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    setup.ok(
        f"MTP build inputs trimmed: {freed / 1e9:.1f} GB (setup.py re-fetches them if it "
        "ever rebuilds the draft layer)"
    )


def main() -> int:
    cli = [a for a in sys.argv[1:] if a != "--"]

    # start-strata.sh and inject-strata-model.sh ask this instead of carrying settings of
    # their own, so a health check polls the port strata.ini is about to listen on
    if "--print" in cli:
        i = cli.index("--print")
        if i + 1 >= len(cli):
            print("--print takes a strata.ini key, e.g. --print port", file=sys.stderr)
            return 2
        key = cli[i + 1]
        rest = [a for j, a in enumerate(cli) if j not in (i, i + 1)]
        pairs = strata_args(rest)
        flag = flag_name(key)
        resolve = PRINT_RESOLVERS.get(flag)
        value = resolve(pairs) if resolve else flag_value(pairs, flag)
        if not value:
            print(f"{ini_path()}: nothing says {key}", file=sys.stderr)
            return 1
        print(value)
        return 0

    pairs = strata_args(cli)
    no_start = has_flag(pairs, "--no-start")
    keep_mtp = has_flag(pairs, "--keep-mtp-inputs")
    pairs = without(pairs, WRAPPER_FLAGS)

    root = app_dir()
    prefix = os.environ.get("CONDA_PREFIX")
    if not prefix:
        sys.exit(
            "CONDA_PREFIX is not set: run this through the pixi tasks "
            "(pixi run start-strata / strata-install)"
        )
    prefix = Path(prefix)
    # setup.py remembers the data folder in ~/.config/strata/settings.json; this env keeps
    # everything it writes under its own prefix
    os.environ.setdefault("XDG_CONFIG_HOME", str(prefix / ".config"))
    setup = load_setup(root)
    replace_pip(setup)
    replace_build_tools(setup)

    family = flag_value(pairs, "--family")
    model = flag_value(pairs, "--model")
    if not family or not model:
        sys.exit(
            f"{ini_path()} sets no family and model. This wrapper prepares the model files "
            "itself, so it has to know which model it is preparing; `pixi run strata-help` "
            "lists what setup.py offers."
        )
    family = resolve_family(setup, family)
    model = resolve_model(setup, family, model)

    # The layout default, not a setting: everything stays inside the env's own prefix.
    # Passed on both paths -- setup.py's own default is `Strata-data` next to the app folder,
    # and data_folder() moves model files between whatever it and the remembered one are.
    data = Path(flag_value(pairs, "--data-dir") or prefix / "strata-data")
    data_dir = ["--data-dir", str(data)]
    fam = family_table(setup)[family]
    own = family in own_path_table(setup)
    tag = (fam["tag"] + model).lower()
    config = root / f"strata-{tag}.json"
    # Only decides whether the image encoder is fetched beside the shards. Without a
    # `vision` in strata.ini the choice is setup.py's, and its recommended answer is the GPU
    # encoder -- so fetching it is the assumption that cannot waste a later start.
    vision = flag_value(pairs, "--vision", "gpu")
    if fam.get("vision") is False and has_flag(pairs, "--vision"):
        # An own-path family whose image encoder is not wired up yet: setup.py accepts the flag
        # and says "images are not available with it yet", so dropping it here is the same
        # answer given before the download instead of after it.
        pairs = without(pairs, {"--vision"})
        vision = "no"
        setup.ok(
            f"images: {fam['title']} has no image encoder yet ({ini_path()}'s vision ignored)"
        )

    signature = prepare_signature(pairs)
    if own and has_flag(pairs, "--vram-reserve-mib"):
        # For a Flash-Next model the reserve is a start-path flag (setup.py rewrites it in the
        # config). `install_own_path` instead translates it once into `--expert-vram`, so here
        # it belongs to the preparation: changing it re-prepares, which re-derives that figure.
        signature = sorted(
            signature
            + [["--vram-reserve-mib", flag_value(pairs, "--vram-reserve-mib") or ""]]
        )
    prepared = prepared_args(data)  # what this data folder was last prepared with
    reason = None
    if not config.is_file():
        reason = f"{config.name} is not installed"
    elif no_start:
        reason = "strata-install prepares whatever strata.ini says"
    elif any(flag in INSTALL_FLAGS for flag, _ in parse_args(cli)):
        reason = "asked for on the command line"
    elif prepared is None:
        reason = "nothing records how this model was prepared"
    elif prepared != signature:
        reason = f"strata.ini changed: {signature_diff(prepared, signature)}"
    elif lost := missing_model_files(setup, family, model, vision):
        # the run config points the engine at this folder, so a file pruned from the Hub cache
        # behind these symlinks breaks the start (the vision encoder, if it is the mmproj)
        reason = f"its model files are gone: {', '.join(lost)}"

    if reason:
        print(f"  preparing the model: {reason}", flush=True)
        farm = gguf_dir(setup, family, model, vision)
        argv = [
            *data_dir,
            *render(without(pairs, {"--no-start"})),
            "--no-start",
            "--gguf-dir",
            str(farm),
        ]
        code = run_setup(setup, argv)
        if code:
            return code
        record_prepared(data, tag, signature)
        if not keep_mtp:
            trim_mtp_inputs(setup, data)

    # The drafter setup.py does not fetch, on both paths (`strata-install` as well as a
    # start): a model prepared by an earlier run has to get its pack too (fetch_dflash2).
    fetch_dflash2(setup, pairs, data, config, family)
    # Several models can be installed at once and setup.py starts the newest run config, so
    # the one strata.ini names has to be the newest by the time it looks.
    if config.is_file():
        config.touch()

    # setup.py's start path ignores --context (it reads the config verbatim), so the context
    # strata.ini asks for is applied to the config itself.  Before the `--no-start` return as
    # well: `pixi run strata-install` leaves a config that already says what strata.ini says,
    # so the next start rewrites nothing.
    ctx = flag_value(pairs, "--context")
    if ctx and pin_context(config, ctx):
        setup.ok(f"context: {ctx} tokens ({config.name})")

    if no_start:
        return 0

    # The VRAM reserve setup.py does read on a start -- but passing it rewrites the config,
    # so a config that already agrees with strata.ini is left alone.
    start = without(pairs, WRAPPER_FLAGS | INSTALL_FLAGS)
    reserve = flag_value(pairs, "--vram-reserve-mib")
    if reserve and config_flag(config, "--vram-reserve-mib") == reserve:
        start = without(start, {"--vram-reserve-mib"})
    if own:
        # An own-path engine reads no `--vram-reserve-mib` at all (its arguments are
        # `--expert-vram`, `--expert-slots`, `--hybrid-rows`, `--cpu-threads`) and logs
        # "ignoring --vram-reserve-mib (not an option of this engine)" on every start that
        # carries one. `install_own_path` already turned the reserve into that `--expert-vram`.
        start = without(start, {"--vram-reserve-mib"})

    # Let the server replace this process: the pid start-strata.sh records is then the
    # server's own, and SIGTERM lands in the code that answers the engine with QUIT --
    # the same reason upstream sets this for docker stop.
    os.environ["STRATA_EXECV"] = "1"
    return run_setup(setup, [*data_dir, *render(start)])


if __name__ == "__main__":
    sys.exit(main())
