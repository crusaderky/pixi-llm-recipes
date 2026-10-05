#!/usr/bin/env python3
"""Start Strata out of the conda package, with pixi owning the dependencies.

The upstream installer (setup.py) assumes a plain ``./setup.sh`` checkout; three of the
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
once instead of being silently ignored. ``--context`` is the exception this wrapper can fix
without a re-prepare: it rewrites ``--max-context`` in the run config before every start.
"""

from __future__ import annotations

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


def resolve_family(setup: ModuleType, family: str) -> str:
    """The family as setup.py spells it, or the choices it would have offered."""
    known = {name.lower(): name for name in setup.FAMILIES}
    if family.lower() not in known:
        sys.exit(f"family {family!r}: setup.py knows {', '.join(setup.FAMILIES)}")
    return known[family.lower()]


def resolve_model(setup: ModuleType, family: str, model: str) -> str:
    """The quant, checked against the family that ships it (`MODELS[...]['families']`)."""
    known = {name.lower(): name for name in setup.MODELS}
    if model.lower() not in known:
        sys.exit(f"model {model!r}: setup.py knows {', '.join(setup.MODELS)}")
    model = known[model.lower()]
    families = setup.MODELS[model].get("families", ("qwen", "swift"))
    if family not in families:
        sys.exit(
            f"model {model!r} exists only for {', '.join(families)}, not {family!r}"
        )
    return model


def model_files(setup: ModuleType, family: str, model: str, vision: str) -> list[str]:
    """The files setup.py downloads for this model: every shard, and the image encoder."""
    fam = setup.FAMILIES[family]
    shards = [
        setup.model_file(fam, model, i)
        for i in range(1, setup.model_shards(fam, model) + 1)
    ]
    urls = [fam["hf"].format(q=model) + name for name in shards]
    if vision not in ("no", "none", "off"):
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


def gguf_dir(setup: ModuleType, family: str, model: str, vision: str) -> Path:
    """The folder of symlinks into the cache that setup.py is pointed at with --gguf-dir."""
    from huggingface_hub.constants import HF_HUB_CACHE

    prefix = Path(os.environ["CONDA_PREFIX"])
    where = prefix / "strata-models" / (setup.FAMILIES[family]["tag"] + model).lower()
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
    tag = (setup.FAMILIES[family]["tag"] + model).lower()
    config = root / f"strata-{tag}.json"
    # Only decides whether the image encoder is fetched beside the shards. Without a
    # `vision` in strata.ini the choice is setup.py's, and its recommended answer is the GPU
    # encoder -- so fetching it is the assumption that cannot waste a later start.
    vision = flag_value(pairs, "--vision", "gpu")

    signature = prepare_signature(pairs)
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
    if no_start:
        return 0

    # Several models can be installed at once and setup.py starts the newest run config, so
    # the one strata.ini names has to be the newest by the time it looks.
    if config.is_file():
        config.touch()

    # setup.py's start path ignores --context (it reads the config verbatim), so the context
    # strata.ini asks for is applied to the config itself.
    ctx = flag_value(pairs, "--context")
    if ctx and pin_context(config, ctx):
        setup.ok(f"context: {ctx} tokens ({config.name})")

    # The VRAM reserve setup.py does read on a start -- but passing it rewrites the config,
    # so a config that already agrees with strata.ini is left alone.
    start = without(pairs, WRAPPER_FLAGS | INSTALL_FLAGS)
    reserve = flag_value(pairs, "--vram-reserve-mib")
    if reserve and config_flag(config, "--vram-reserve-mib") == reserve:
        start = without(start, {"--vram-reserve-mib"})

    # Let the server replace this process: the pid start-strata.sh records is then the
    # server's own, and SIGTERM lands in the code that answers the engine with QUIT --
    # the same reason upstream sets this for docker stop.
    os.environ["STRATA_EXECV"] = "1"
    return run_setup(setup, [*data_dir, *render(start)])


if __name__ == "__main__":
    sys.exit(main())
