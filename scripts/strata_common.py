"""Shared Strata command-line primitives.

Used by ``strata-run.py`` (which runs Strata's own ``setup.py``), ``strata-help.py`` (which
prints every flag ``setup.py`` accepts) and ``llama-benchy-strata.py`` (which benchmarks
whatever the server is serving), and read by ``start-strata.sh`` and
``inject-strata-model.sh`` through ``strata-run.py --print <key>``.

``strata.ini`` in the project root holds one section per model profile. Every key of the
active section becomes an argument for ``setup.py``, exactly as
``pixi run start-strata QFN -- --<key> <value>`` would pass it. A profile is named by its
section or by one of its ``aliases``; ``STRATA_PROFILE`` selects it, and with no selection
the first section wins. Precedence, highest first: the command line, the ``STRATA_*``
environment, the profile's section in ``strata.ini``. The file is required -- the settings
live there and not in this code, so there is nothing here to keep in step with it.

The scripts that import this live in the same directory and are invoked as
``python scripts/<name>.py``, so ``sys.path[0]`` is ``scripts/`` and a plain
``import strata_common`` resolves.  (Their own file names are hyphenated and therefore not
importable, which is why this module carries the shared code.)
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

Pair = tuple[str, str | None]

INI_NAME = "strata.ini"
INI_SECTION = (
    "strata"  # the old single-profile section, still read when nothing else is selected
)
PROFILE_ENV = "STRATA_PROFILE"
#: a profile's description, not a setup.py flag
PROFILE_META_KEYS = frozenset({"aliases"})
#: what this wrapper records next to the model's data: the part of the command line
#: setup.py only reads when it prepares a model (see ``prepare_signature``)
PREPARED_NAME = "strata-prepared.json"

#: setup.py's switches that take no value (its ``store_true`` arguments).  In strata.ini
#: such a key is a flag on its own (`no-browser =`), and `no-browser = off` drops it.
VALUELESS_FLAGS = frozenset(
    {
        "--browser",
        "--no-browser",
        "--yes",
        "--setup",
        "--no-start",
        "--update",
        "--build",
        "--check",
        "--calibrate",
        "--no-remote-expert-opt",
    }
)
#: a value that means "not this flag at all", for a key of ``VALUELESS_FLAGS``
OFF_VALUES = frozenset({"false", "off", "no", "none", "0"})

#: this wrapper's own switches: they decide what ``strata-run.py`` does, and setup.py has no
#: flag of that name, so they are never forwarded
WRAPPER_FLAGS = frozenset({"--keep-mtp-inputs"})

#: setup.py's switches that mean "install or reconfigure, then stop" instead of "start what
#: is already installed".  They are one-shot commands, so they belong on a command line and not
#: in strata.ini, where they would re-run on every start (--inspect prints and exits,
#: --rollback-engine swaps the engine)
COMMAND_FLAGS = frozenset(
    {
        "--setup",
        "--no-start",
        "--update",
        "--check",
        "--calibrate",
        "--inspect",
        "--rollback-engine",
    }
)

#: setup.py's start path reads these from the command line and applies them to an installed
#: model.  Every other flag of its is read only when the model is prepared -- which is why
#: changing one in strata.ini makes this wrapper re-prepare (``prepare_signature``).
START_FLAGS = frozenset(
    {
        "--data-dir",
        "--port",
        "--gpu",
        "--gpus",
        "--gpu-roles",
        "--layer-split",
        "--host",
        "--api-key",
        "--draft-vocab",
        "--vram-reserve-mib",
        "--browser",
        "--no-browser",
        "--yes",
    }
)

#: setup.py's switches that send it down its setup path: they may name the model on a
#: command line, but a start must not carry them (the model is already chosen by then)
INSTALL_FLAGS = frozenset({"--setup", "--family", "--model"})

#: the environment variables that stand for a strata.ini key.  Everything setup.py accepts
#: that is not one of these has only the file.
ENV_FLAGS = {
    "STRATA_FAMILY": "--family",
    "STRATA_MODEL": "--model",
    "STRATA_VISION": "--vision",
    "STRATA_CONTEXT": "--context",
    "STRATA_VRAM_RESERVE_MIB": "--vram-reserve-mib",
    "STRATA_PORT": "--port",
    "STRATA_DATA": "--data-dir",
    "STRATA_KEEP_MTP_INPUTS": "--keep-mtp-inputs",
}

#: this wrapper sets --gguf-dir itself: the folder of symlinks into the shared Hub cache is
#: the whole point of running Strata through it
GGUF_DIR_FLAG = "--gguf-dir"


# ---------------------------------------------------------------------------
#  strata.ini
# ---------------------------------------------------------------------------
def repo_root() -> Path:
    """The project root: this module lives in its `scripts/` folder."""
    return Path(__file__).resolve().parent.parent


def ini_path() -> Path:
    """strata.ini in the project root ($STRATA_INI overrides it)."""
    return Path(os.environ.get("STRATA_INI") or repo_root() / INI_NAME)


def selected_profile() -> str | None:
    """The profile the tasks were called with, if any (they export STRATA_PROFILE)."""
    return os.environ.get(PROFILE_ENV) or None


def _ini_parser(path: Path) -> configparser.ConfigParser:
    if not path.is_file():
        sys.exit(
            f"{path} is missing, and it is where Strata's settings live: restore it from "
            "git or point STRATA_INI at one. `pixi run strata-help` prints every flag it "
            "accepts."
        )
    parser = configparser.ConfigParser(
        allow_no_value=True,
        interpolation=None,  # a value is a command-line argument: no % substitution
        inline_comment_prefixes=("#", ";"),
    )
    parser.optionxform = str  # keys are flags: --vram-reserve-mib stays as written
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        sys.exit(f"{path}: {exc}")
    return parser


def parse_aliases(value: str | None) -> list[str]:
    """`aliases = [QFN, flash]` as the two names it declares."""
    return [
        a.strip() for a in (value or "").strip().strip("[]").split(",") if a.strip()
    ]


def profiles(path: Path | None = None) -> list[tuple[str, list[str]]]:
    """Every profile in strata.ini: its section name and its aliases."""
    parser = _ini_parser(path or ini_path())
    return [
        (name, parse_aliases(parser[name].get("aliases"))) for name in parser.sections()
    ]


def select_profile(name: str | None, path: Path | None = None) -> str:
    """The section a profile name or alias picks. With no name, the old [strata] section
    still wins; otherwise it is the first section written in the file."""
    parser = _ini_parser(path or ini_path())
    found = profiles(path or ini_path())
    if not found:
        sys.exit(f"{path or ini_path()}: no model profile sections")
    if name:
        for section, aliases in found:
            if name.lower() in [section.lower(), *(a.lower() for a in aliases)]:
                return section
        known = ", ".join(
            f"{section}" + (f" ({', '.join(aliases)})" if aliases else "")
            for section, aliases in found
        )
        sys.exit(f"{path or ini_path()}: no profile {name!r}; it knows {known}")
    if parser.has_section(INI_SECTION):
        return INI_SECTION
    return found[0][0]


def flag_name(key: str) -> str:
    """A strata.ini key as the flag it names: dashes or underscores, the `--` optional."""
    key = key.strip().replace("_", "-")
    return key if key.startswith("-") else f"--{key}"


def read_ini(path: Path | None = None, profile: str | None = None) -> list[Pair]:
    """One profile of strata.ini as `(flag, value)` pairs, in the order it was written.

    `key = value`; a key with no value is a valueless flag, and a value of `off`/`no`/
    `false` drops one.  Inline comments need the whitespace before them, so a value that
    contains a `#` (an api key) keeps it.
    """
    path = path or ini_path()
    section = select_profile(profile, path)
    parser = _ini_parser(path)
    pairs: list[Pair] = []
    for key, value in parser[section].items():
        flag = flag_name(key)
        if flag in {flag_name(name) for name in PROFILE_META_KEYS}:
            continue
        value = None if value is None else value.strip()
        if flag in COMMAND_FLAGS:
            sys.exit(
                f"{path}: {flag} is a one-shot command, not a setting -- it would run on "
                f"every start. Pass it on the command line instead: "
                f"pixi run strata-install <profile> -- --{flag.lstrip('-')}"
            )
        if flag == GGUF_DIR_FLAG:
            sys.exit(
                f"{path}: {GGUF_DIR_FLAG} is set by scripts/strata-run.py itself: it is the "
                "folder of symlinks into ~/.cache/huggingface/hub, the same blobs "
                "llama-server -hf downloads."
            )
        if flag in VALUELESS_FLAGS:
            if (value or "").lower() in OFF_VALUES:
                continue
            value = None
        elif value == "":
            sys.exit(
                f"{path}: {key} needs a value; only {', '.join(sorted(VALUELESS_FLAGS))} "
                "may be written without one"
            )
        pairs.append((flag, value))
    return pairs


def env_args() -> list[Pair]:
    """The STRATA_* variables, as arguments: they override strata.ini and lose to the
    command line.  A variable that is unset, or set empty, says nothing."""
    out: list[Pair] = []
    for name, flag in ENV_FLAGS.items():
        value = os.environ.get(name)
        if not value:
            continue
        if flag in VALUELESS_FLAGS:
            if value.strip().lower() in OFF_VALUES:
                continue
            out.append((flag, None))
        else:
            out.append((flag, value.strip()))
    return out


def parse_args(argv: Sequence[str]) -> list[Pair]:
    """An argument list as `(flag, value)` pairs.  `--flag value` and `--flag=value` both
    read as one flag with a value; a flag followed by another flag (or the end) is one
    without a value.  A token is a flag iff it starts with `--`: setup.py takes no
    single-dash flag and no positional argument."""
    pairs: list[Pair] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("--"):
            i += 1
            continue
        flag, sep, inline = token.partition("=")
        if sep:
            pairs.append((flag, inline))
            i += 1
        elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            pairs.append((flag, argv[i + 1]))
            i += 2
        else:
            pairs.append((flag, None))
            i += 1
    return pairs


def dedupe(pairs: Sequence[Pair]) -> list[Pair]:
    """One entry per flag: the position of its first appearance, the value of its last.
    That is what setup.py's argparse does with a repeated flag, so the last source written
    into the list is the one that wins."""
    order: list[str] = []
    values: dict[str, str | None] = {}
    for flag, value in pairs:
        if flag not in values:
            order.append(flag)
        values[flag] = value
    return [(flag, values[flag]) for flag in order]


def strata_args(cli: Sequence[str] = (), profile: str | None = None) -> list[Pair]:
    """The command line Strata gets: the selected profile, then the STRATA_* overrides,
    then the caller's own arguments -- so a command line beats the file and the file beats
    the environment."""
    return dedupe(
        read_ini(profile=profile or selected_profile()) + env_args() + parse_args(cli)
    )


def render(pairs: Sequence[Pair]) -> list[str]:
    """`(flag, value)` pairs back as an argument list, in the `--flag=value` spelling that
    cannot be misread as the next flag."""
    return [flag if value is None else f"{flag}={value}" for flag, value in pairs]


def flag_value(
    pairs: Sequence[Pair], flag: str, default: str | None = None
) -> str | None:
    """A flag's value, or `default` when it is not there.  The last one wins, as in argparse."""
    for pair_flag, value in reversed(list(pairs)):
        if pair_flag == flag:
            return default if value is None else value
    return default


def has_flag(pairs: Sequence[Pair], flag: str) -> bool:
    return any(pair_flag == flag for pair_flag, _ in pairs)


def without(pairs: Sequence[Pair], flags: frozenset[str] | set[str]) -> list[Pair]:
    return [pair for pair in pairs if pair[0] not in flags]


# ---------------------------------------------------------------------------
#  what setup.py only reads when it prepares a model
# ---------------------------------------------------------------------------
def prepare_signature(pairs: Sequence[Pair]) -> list[list[str]]:
    """The part of the command line setup.py reads only when it prepares a model.

    setup.py's start path hands its `cfg["args"]` to the engine verbatim, so a `--kv` or a
    `--parallel` that only ever appears on a start changes nothing: the run config already
    says what the engine runs with.  Recording this next to the data makes a change in
    strata.ini visible, and scripts/strata-run.py re-prepares when it moves.  (The wrapper
    can apply `context` and the drafter without that: see `pin_context` / `pin_drafting`.)
    """
    skip = START_FLAGS | WRAPPER_FLAGS | COMMAND_FLAGS | INSTALL_FLAGS
    return sorted(
        [flag, value if value is not None else ""]
        for flag, value in dedupe(pairs)
        if flag not in skip
    )


def signature_diff(old: Sequence[Sequence[str]], new: Sequence[Sequence[str]]) -> str:
    """What moved between two preparation signatures, as one line."""
    before = {pair[0]: pair[1] for pair in old}
    after = {pair[0]: pair[1] for pair in new}
    moved = [
        f"{flag} {before.get(flag) or '(off)'} -> {after.get(flag) or '(off)'}"
        for flag in sorted(set(before) | set(after))
        if before.get(flag, "") != after.get(flag, "")
    ]
    return ", ".join(moved)


def prepared_path(data: Path, tag: str) -> Path:
    """One model's preparation record. Several models share a data dir, so the record is
    per model: the single `strata-prepared.json` of the first profile is only a fallback."""
    return data / "prepared" / f"{tag}.json"


def prepared_args(data: Path, tag: str) -> list[list[str]] | None:
    """What this wrapper last prepared this model with, or None when nothing records it
    (a model prepared before per-model records existed, or a data folder moved in)."""
    for path in (prepared_path(data, tag), data / PREPARED_NAME):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or (tag and record.get("tag") != tag):
            continue
        args = record.get("args")
        return args if isinstance(args, list) else None
    return None


def record_prepared(data: Path, tag: str, signature: Sequence[Sequence[str]]) -> None:
    path = prepared_path(data, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps({"tag": tag, "args": list(signature)}, indent=1), encoding="utf-8"
    )
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
#  the installed app tree and its run configs
# ---------------------------------------------------------------------------
def app_dirs() -> list[Path]:
    """Every installed Strata app tree this machine could be talking to: $STRATA_ROOT, the
    strata env itself, a sibling env of it (the layout of an `agents` env, which ships no
    strata package of its own), or this repo's own .pixi/envs/strata."""
    prefix = os.environ.get("CONDA_PREFIX")
    root = os.environ.get("STRATA_ROOT")
    candidates = [
        Path(root) if root else None,
        Path(prefix) / "opt" / "strata" if prefix else None,
        Path(prefix).parent / "strata" / "opt" / "strata" if prefix else None,
        repo_root() / ".pixi" / "envs" / "strata" / "opt" / "strata",
    ]
    # 0.2.16 moved setup.py one level down into terminal/; both layouts are one app tree.
    return [
        path
        for path in candidates
        if path
        and (
            (path / "setup.py").is_file() or (path / "terminal" / "setup.py").is_file()
        )
    ]


def app_dir() -> Path:
    """The one to run: $CONDA_PREFIX/opt/strata under the pixi tasks."""
    found = app_dirs()
    if found:
        return found[0]
    sys.exit(
        "no Strata installation found: run this through the pixi tasks "
        "(pixi run start-strata / strata-install / strata-help), or set STRATA_ROOT"
    )


def read_config(path: Path) -> dict | None:
    """A run config as setup.py writes it, or None when it is not one.  The filter is
    setup.py's own `model_config()`: only a file that describes a model counts."""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) and cfg.get("exe") and cfg.get("args") else None


def model_configs(root: Path) -> list[Path]:
    """The run configs of one app tree, newest first -- setup.py's `installed_configs()`
    order, which is also the one its start path serves when several models are installed.
    0.2.16 writes them in terminal/, older trees at the app root."""
    folder = root / "terminal" if (root / "terminal").is_dir() else root
    return sorted(
        (path for path in folder.glob("strata-*.json") if read_config(path)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def family_table(setup: ModuleType) -> dict:
    """Every family setup.py can prepare, as one `{name: entry}` table.

    The pack families share one engine; `OWN_PATH_FAMILIES` (Qwen3.6), `HY4_FAMILIES` and
    `INKLING_FAMILIES` bring their own files and engines. `PREVIEW_FAMILIES` is upstream's
    older name for the first of those.
    """
    return {
        **setup.FAMILIES,
        **(getattr(setup, "OWN_PATH_FAMILIES", None) or {}),
        **(getattr(setup, "PREVIEW_FAMILIES", None) or {}),
        **(getattr(setup, "HY4_FAMILIES", None) or {}),
        **(getattr(setup, "INKLING_FAMILIES", None) or {}),
    }


def model_tag(setup: ModuleType, pairs: Sequence[Pair]) -> str | None:
    """The suffix setup.py gives this model's run config (`strata-<tag>.json`)."""
    family = flag_value(pairs, "--family")
    model = flag_value(pairs, "--model")
    if not family or not model:
        return None
    fam = family_table(setup).get(family) or {}
    return (fam.get("tag", family + "-") + model).lower()


def config_path(root: Path, pairs: Sequence[Pair], setup: ModuleType) -> Path | None:
    """The run config this profile names, whether or not it has been prepared yet."""
    tag = model_tag(setup, pairs)
    if not tag:
        return None
    folder = root / "terminal" if (root / "terminal").is_dir() else root
    return folder / f"strata-{tag}.json"


def active_config(pairs: Sequence[Pair]) -> Path | None:
    """The selected profile's run config, when it is installed."""
    root = app_dir()
    path = config_path(root, pairs, load_setup(root))
    return path if path and path.is_file() else None


def installed_ports(pairs: Sequence[Pair] | None = None) -> list[str]:
    """The ports the installed run configs were prepared with, newest first. The selected
    profile's config comes first when there is one."""
    out: list[str] = []
    if pairs is not None:
        config = active_config(pairs)
        cfg = read_config(config) if config else None
        port = cfg.get("port") if cfg else None
        if port:
            out.append(str(port))
    for root in app_dirs():
        for path in model_configs(root):
            port = (read_config(path) or {}).get("port")
            if port and str(port) not in out:
                out.append(str(port))
    return out


def resolve_port(pairs: Sequence[Pair]) -> str | None:
    """The port this run listens on: strata.ini / STRATA_PORT / --port, else the port the
    installed model was prepared with.  None when nothing says."""
    port = flag_value(pairs, "--port")
    if port:
        return port
    found = installed_ports(pairs)
    return found[0] if found else None


def installed_context(pairs: Sequence[Pair] | None = None) -> str | None:
    """The context the selected profile's installed config serves, else the newest one's."""
    if pairs is not None:
        config = active_config(pairs)
        if config:
            ctx = config_flag(config, "--max-context")
            if ctx:
                return ctx
    for root in app_dirs():
        for path in model_configs(root):
            ctx = config_flag(path, "--max-context")
            if ctx:
                return ctx
    return None


def resolve_context(pairs: Sequence[Pair]) -> str | None:
    """The context this run serves: strata.ini / STRATA_CONTEXT / --context, else what the
    installed model was prepared with.  None when nothing says."""
    return flag_value(pairs, "--context") or installed_context(pairs)


def resolve_model_name(pairs: Sequence[Pair]) -> str | None:
    """The model name pi should use for this profile, from its installed run config."""
    config = active_config(pairs)
    cfg = read_config(config) if config else None
    name = cfg.get("model_name") if cfg else None
    return str(name) if name else None


def write_config(config: Path, cfg: dict) -> None:
    """A run config back to disk the way setup.py writes it (``json.dumps(cfg, indent=1)``,
    whole file, moved over the old one), so a later setup run sees the same bytes it would
    have written."""
    tmp = config.with_name(config.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, config)


def config_flag(config: Path, flag: str) -> str | None:
    """`--flag`'s value in a run config's engine arguments, or None when it is not there.

    setup.py's start path hands `cfg["args"]` to the engine verbatim, so that list is what
    the engine will really run with -- whatever a command line said at some earlier start.
    """
    cfg = read_config(config)
    args = cfg.get("args", []) if cfg else []
    if not isinstance(args, list):
        return None
    return flag_value(parse_args([str(a) for a in args]), flag)


#: the keys `strata-run.py --print <key>` answers for, and where each one comes from
PRINT_RESOLVERS = {
    "--port": resolve_port,
    "--context": resolve_context,
    "--model-name": resolve_model_name,
}


def pin_context(config: Path, ctx: str) -> bool:
    """Rewrite ``--max-context`` in a run config; True when it had to change.

    setup.py's start path takes the engine's arguments from the config verbatim and ignores
    ``--context``, so the context strata.ini asks for is applied to the file itself.  Written
    the way setup.py writes it (``json.dumps(cfg, indent=1)``, whole-file, moved over the old
    one) so a later setup run sees the same bytes it would have written.
    """
    cfg = read_config(config)
    if cfg is None:
        return False
    args = cfg.get("args", [])
    if not isinstance(args, list):
        args = []
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
    write_config(config, cfg)
    return True


def _replace_engine_arg(config: Path, flag: str, value: str) -> bool:
    """Set one engine flag in a run config's args; True when it had to change."""
    cfg = read_config(config)
    if cfg is None:
        return False
    args = cfg.get("args", [])
    if not isinstance(args, list):
        args = []
    if flag in args:
        i = args.index(flag) + 1
        if i < len(args) and args[i] == str(value):
            return False
        if i < len(args):
            args[i] = str(value)
        else:
            args.append(str(value))
    else:
        args += [flag, str(value)]
    cfg["args"] = args
    write_config(config, cfg)
    return True


def pin_kv(config: Path, kv: str) -> bool:
    """Rewrite ``--kv`` in a run config; True when it had to change.

    Inkling's engine takes ``--kv auto|fp16|fp8|k8v8|k8v4``; its setup path never writes the
    flag, so the wrapper owns it just as it owns ``--max-context`` for every model.
    """
    return _replace_engine_arg(config, "--kv", kv)


def pin_kv_offload(config: Path, mode: str) -> bool:
    """Rewrite ``--kv-offload`` in an Inkling run config; True when it had to change.

    Like ``--kv``, the flag belongs to the engine and setup.py does not accept it.
    ``on`` maps all global KV to host RAM, which is the only way a full 128K context fits
    this 24 GiB card: the automatic growth path runs out of VRAM at ~90K tokens.
    """
    return _replace_engine_arg(config, "--kv-offload", mode)


def pin_drafting(config: Path, choice: str) -> bool:
    """Rewrite a run config's ``drafting`` key; True when it had to change.

    ``drafting`` is serve/server.py's own key (``auto`` | ``mtp`` | ``dflash2`` |
    ``dspark`` | ``off``);
    setup.py never writes it -- its ``--drafter`` only decides whether the MTP layer is
    fetched -- so a config setup.py prepared runs ``auto``, which is DFlash2 while the start
    serves one request and the MTP as soon as ``parallel`` gives it more than one slot
    (``blocked_by``: Auto never names DFlash2, and only a named choice keeps it beside the
    slots).  A ``drafter = dflash2`` in strata.ini therefore has to be written down, or the
    second request slot silently swaps the drafter for the MTP.

    Written the way setup.py writes the file (``write_config``), and only when a drafter was
    asked for at all, so the config the app or ``strata launch --drafting`` shaped stays as
    it is when strata.ini has nothing to say about it.
    """
    cfg = read_config(config)
    if cfg is None:
        return False
    if str(cfg.get("drafting") or "") == choice:
        return False
    cfg["drafting"] = choice
    write_config(config, cfg)
    return True


# ---------------------------------------------------------------------------
#  setup.py as a module
# ---------------------------------------------------------------------------
def setup_file(root: Path) -> Path:
    """This app tree's setup.py, at its root or one level down in terminal/ (0.2.16)."""
    nested = root / "terminal" / "setup.py"
    return nested if nested.is_file() else root / "setup.py"


def load_setup(root: Path) -> ModuleType:
    """Strata's setup.py as a module, so its tables drive this wrapper and not a copy."""
    path = setup_file(root)
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("strata_setup", path)
    if spec is None or spec.loader is None:
        sys.exit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["strata_setup"] = module
    spec.loader.exec_module(module)
    return module


def run_setup(setup: ModuleType, argv: list[str]) -> int:
    """One setup.py invocation, in this process (so any replacement this wrapper installed
    into the module is still in place)."""
    sys.argv = [str(setup.ROOT / "setup.py"), *argv]
    try:
        return int(setup.main() or 0)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)


def setup_flags(setup: ModuleType) -> list[str]:
    """Every flag setup.py's own argparse knows, read off the parser its ``main()`` builds.

    setup.py builds that parser inside ``main()`` and there is no other way to see it, so
    ``parse_args`` is intercepted for one call: it records the parser and stops there. This
    is what keeps a flag list (strata-help.py's, or the prepare-only one below) from drifting
    from the version of Strata this project packages.
    """
    import argparse

    captured: dict[str, argparse.ArgumentParser] = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, args=None, namespace=None):
        captured["parser"] = self
        raise SystemExit(0)

    argv = list(sys.argv)
    argparse.ArgumentParser.parse_args = spy
    try:
        run_setup(setup, [])
    finally:
        argparse.ArgumentParser.parse_args = real
        sys.argv = argv
    parser = captured.get("parser")
    if parser is None:
        sys.exit("cannot read setup.py's argument parser: it is built differently now")
    return sorted(
        action.option_strings[0]
        for action in parser._actions
        # an action whose help is SUPPRESS is hidden from --help on purpose (--skip-build
        # is setup_intel.py's plumbing), so it is not a parameter to advertise either
        if action.option_strings
        and action.dest != "help"
        and action.help is not argparse.SUPPRESS
    )


def prepare_flags(setup: ModuleType) -> list[str]:
    """The flags setup.py reads only when it prepares a model: everything it accepts that is
    neither one of its start-path flags nor a one-shot command.  Changing one of these in
    strata.ini is what makes scripts/strata-run.py re-prepare (``prepare_signature``)."""
    return sorted(
        set(setup_flags(setup)) - START_FLAGS - COMMAND_FLAGS - {GGUF_DIR_FLAG}
    )
