#!/usr/bin/env python3
"""Start Strata out of the conda package, with pixi owning the dependencies.

The upstream installer (setup.py) assumes a plain ``./setup.sh`` checkout; two of the
things it does there are replaced here:

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
  layer is built. ``--keep-mtp-inputs`` (or ``STRATA_KEEP_MTP_INPUTS=1``) keeps them.

Everything else stays setup.py's: the expert pack, the MTP draft layer, the run config.
What it writes is all inside ``$CONDA_PREFIX`` -- the pack, the MTP layer and the config
under ``$CONDA_PREFIX/strata-data`` and ``$CONDA_PREFIX/opt/strata``, its per-user settings
redirected there with ``XDG_CONFIG_HOME`` too.

    python scripts/strata-run.py               # install if needed, then start
    python scripts/strata-run.py --no-start    # prepare the model files, do not start
    pixi run start-strata -- --model IQ2_XS    # anything unknown is forwarded to setup.py
    pixi run strata-install -- --keep-mtp-inputs

The context is pinned (``STRATA_CONTEXT``, default 262144 = 256K): setup.py's start path
ignores ``--context``, so a config that drifted is rewritten here rather than re-running the
setup path -- that path without ``--gguf-dir`` would want the shards downloaded again.

So is the VRAM the engine leaves to everything else (``STRATA_VRAM_RESERVE_MIB``, default
``DEFAULT_VRAM_RESERVE_MIB``): the engine's own 700 MiB is small enough that a deployment on
a card that also drives the display ends with the expert cache -- and the image encoder, on
``--vision gpu`` -- holding the rest of it, and the desktop's own buffers evicted.  That is
what takes the X server or the compositor down mid-reply.  A config still carrying 700 is
rewritten at the next start.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

DEFAULT_FAMILY = "coder"
DEFAULT_MODEL = "IQ1_M"
#: The image encoder on the GPU (Strata's `--vision gpu`): the Coder keeps the visual
#: experts, and the encoder is compiled into the package.
DEFAULT_VISION = "gpu"
#: The context every run gets, pinned: 256K is Qwen3.8-Flash-Next's trained window (262,144
#: positions), so no rope scaling is involved and there is nothing to trade off. The deployment
#: does not retune it per start; `--context` on the command line still wins over the pin.
DEFAULT_CONTEXT = "262144"
#: VRAM the engine keeps free for other programs, in MiB.  The engine's own default is 700,
#: which a card that also drives the display does not survive: once the expert cache (plus
#: the image encoder, on ``--vision gpu``) holds the rest of the card, the driver evicts the
#: desktop's buffers and the X server / Wayland session goes with them (upstream #560, #516
#: -- setup.py's own advice there is 3072 for an AMD card on a Linux desktop).  The cost is
#: ~1.3 GB of expert cache, a few percent of decode speed.
DEFAULT_VRAM_RESERVE_MIB = "2000"

#: A pinned file URL as setup.py builds it: <endpoint>/<owner>/<repo>/resolve/<sha>/<path>
RESOLVE_URL = re.compile(
    r"^(?P<endpoint>https?://[^/]+)/(?P<repo>[^/]+/[^/]+)/resolve/"
    r"(?P<revision>[0-9a-f]{40})/(?P<path>.+)$"
)
#: setup.py's own switches that mean "install or reconfigure", not "start"
INSTALL_FLAGS = ("--setup", "--family", "--model")

#: The MTP draft layer's build inputs: the 31 raw tensors `tools/mtp_fetch.py` range-reads out
#: of the 360 GB BF16 checkpoint (~5 GB) and the GGUF `tools/mtp_pack.py` made of them. The
#: engine loads neither -- `--mtp` takes the `rt/` folder `tools/mtp_rt.py` writes -- and
#: setup.py's one reason to rebuild them is `rt/experts.bin` going missing, which re-fetches
#: the raw tensors anyway. `verify` makes the raw tensors worth their keep (#327: a mirror
#: that ignores Range returns the shard's start, and nothing else would catch it).
MTP_INPUTS = ("tensors", "mtp-q2_0.gguf")


def app_dir() -> Path:
    """The installed package: $CONDA_PREFIX/opt/strata ($STRATA_ROOT overrides it)."""
    override = os.environ.get("STRATA_ROOT")
    if override:
        return Path(override)
    prefix = os.environ.get("CONDA_PREFIX")
    if not prefix:
        sys.exit(
            "CONDA_PREFIX is not set: run this through the pixi tasks "
            "(pixi run start-strata / strata-install)"
        )
    return Path(prefix) / "opt" / "strata"


def load_setup(root: Path) -> ModuleType:
    """Strata's setup.py as a module, so its tables drive this wrapper and not a copy."""
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("strata_setup", root / "setup.py")
    if spec is None or spec.loader is None:
        sys.exit(f"cannot load {root / 'setup.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["strata_setup"] = module
    spec.loader.exec_module(module)
    return module


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
        sys.exit(f"--family {family!r}: setup.py knows {', '.join(setup.FAMILIES)}")
    return known[family.lower()]


def resolve_model(setup: ModuleType, family: str, model: str) -> str:
    """The quant, checked against the family that ships it (`MODELS[...]['families']`)."""
    known = {name.lower(): name for name in setup.MODELS}
    if model.lower() not in known:
        sys.exit(f"--model {model!r}: setup.py knows {', '.join(setup.MODELS)}")
    model = known[model.lower()]
    families = setup.MODELS[model].get("families", ("qwen", "swift"))
    if family not in families:
        sys.exit(
            f"--model {model!r} exists only for {', '.join(families)}, not {family!r}"
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


def run_setup(setup: ModuleType, argv: list[str]) -> int:
    """One setup.py invocation, in this process (the pip replacement has to stay in)."""
    sys.argv = [str(setup.ROOT / "setup.py"), *argv]
    try:
        return int(setup.main() or 0)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)


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


def flag_value(forwarded: list[str], flag: str, default: str) -> str:
    """`--flag value` or `--flag=value` as the user gave it, else the default."""
    for i, arg in enumerate(forwarded):
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
        if arg == flag and i + 1 < len(forwarded):
            return forwarded[i + 1]
    return default


def pin_context(config: Path, ctx: str) -> bool:
    """Rewrite ``--max-context`` in a run config; True when it had to change.

    setup.py's start path takes the engine's arguments from the config verbatim and ignores
    ``--context``, so the pin is applied to the file itself. Written the way setup.py writes it
    (``json.dumps(cfg, indent=1)``, whole-file, moved over the old one) so a later setup run
    sees the same bytes it would have written.
    """
    if not config.is_file():
        return False
    try:
        cfg = json.loads(config.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(cfg, dict):
        return False
    args = cfg.get("args", [])
    if "--max-context" in args:
        i = args.index("--max-context") + 1
        if i < len(args) and args[i] == str(ctx):
            return False
        if i < len(args):
            args[i] = str(ctx)
        else:
            args.append(str(ctx))
    else:
        args += ["--max-context", str(ctx)]
    cfg["args"] = args
    tmp = config.with_name(config.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, config)
    return True


def config_flag(config: Path, flag: str) -> str | None:
    """`--flag`'s value in a run config's engine arguments, or None when it is not there.

    setup.py's start path hands ``cfg["args"]`` to the engine verbatim, so that list is what
    the engine will really run with -- whatever a command line said at some earlier start.
    """
    try:
        cfg = json.loads(config.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    args = cfg.get("args", []) if isinstance(cfg, dict) else []
    if not isinstance(args, list):
        return None
    return flag_value(args, flag, None)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)  # the useful --help is setup.py's
    parser.add_argument(
        "--family", default=os.environ.get("STRATA_FAMILY", DEFAULT_FAMILY)
    )
    parser.add_argument(
        "--model", default=os.environ.get("STRATA_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument(
        "--vision", default=os.environ.get("STRATA_VISION", DEFAULT_VISION)
    )
    parser.add_argument("--data-dir", default=os.environ.get("STRATA_DATA"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("STRATA_PORT", "8082"))
    )
    parser.add_argument(
        "--no-start", action="store_true", help="prepare the files, do not start"
    )
    parser.add_argument(
        "--keep-mtp-inputs",
        action="store_true",
        default=bool(os.environ.get("STRATA_KEEP_MTP_INPUTS")),
        help="keep the MTP draft layer's ~5.8 GB of build inputs (the raw checkpoint "
        "tensors and the packed GGUF) instead of trimming them once rt/ is built",
    )
    args, forwarded = parser.parse_known_args([a for a in sys.argv[1:] if a != "--"])

    family = flag_value(forwarded, "--family", args.family)
    model = flag_value(forwarded, "--model", args.model)
    vision = flag_value(forwarded, "--vision", args.vision)

    root = app_dir()
    prefix = Path(os.environ["CONDA_PREFIX"])
    # setup.py remembers the data folder in ~/.config/strata/settings.json; this env keeps
    # everything it writes under its own prefix
    os.environ.setdefault("XDG_CONFIG_HOME", str(prefix / ".config"))
    setup = load_setup(root)
    replace_pip(setup)
    family = resolve_family(setup, family)
    model = resolve_model(setup, family, model)

    data = args.data_dir or str(prefix / "strata-data")
    common = ["--data-dir", data, "--port", str(args.port), "--no-browser", "--yes"]
    tag = (setup.FAMILIES[family]["tag"] + model).lower()
    config = root / f"strata-{tag}.json"
    ctx = flag_value(forwarded, "--context", DEFAULT_CONTEXT)
    reserve = flag_value(
        forwarded,
        "--vram-reserve-mib",
        os.environ.get("STRATA_VRAM_RESERVE_MIB", DEFAULT_VRAM_RESERVE_MIB),
    )
    install = not config.is_file() or any(
        arg.split("=")[0] in INSTALL_FLAGS for arg in forwarded
    )
    if install:
        farm = gguf_dir(setup, family, model, vision)
        argv = [*common, "--no-start", "--gguf-dir", str(farm)]
        for flag, value in (
            ("--family", family),
            ("--model", model),
            ("--vision", vision),
            ("--context", ctx),
            ("--vram-reserve-mib", reserve),
        ):
            if flag not in {arg.split("=")[0] for arg in forwarded}:
                argv += [flag, value]
        code = run_setup(setup, [*argv, *forwarded])
        if code:
            return code
        if not args.keep_mtp_inputs:
            trim_mtp_inputs(setup, Path(data))
    elif args.no_start:
        setup.ok(f"{config.name} is installed: nothing to prepare")
    if args.no_start:
        return 0
    # The pinned context, applied to a config that setup.py wrote earlier (or hand-edited):
    # its start path would otherwise serve the stale --max-context.
    if pin_context(config, ctx):
        setup.ok(f"context: {ctx} tokens (pinned in {config.name})")
    # The reserve, the same way -- but setup.py reads the engine's arguments from the config
    # verbatim, so passing the flag is what rewrites them (and keeps the value for this
    # model).  Only a config that disagrees with the pin is passed one, so the steady state
    # does not rewrite the config on every start.
    if "--vram-reserve-mib" not in {a.split("=")[0] for a in forwarded} and (
        config_flag(config, "--vram-reserve-mib") != reserve
    ):
        forwarded = [*forwarded, "--vram-reserve-mib", reserve]
    # Let the server replace this process: the pid start-strata.sh records is then the
    # server's own, and SIGTERM lands in the code that answers the engine with QUIT --
    # the same reason upstream sets this for docker stop.
    os.environ["STRATA_EXECV"] = "1"
    return run_setup(setup, [*common, *forwarded])


if __name__ == "__main__":
    sys.exit(main())
