"""Shared Strata command-line primitives.

Used by ``strata-run.py`` (which runs Strata's own ``setup.py``), ``strata-help.py`` (which
prints every flag ``setup.py`` accepts) and ``llama-benchy-strata.py`` (which benchmarks
whatever the server is serving), and read by ``start-strata.sh`` and
``inject-strata-model.sh`` through ``strata-run.py --print <key>``.

``strata.ini`` in the project root is where this project says which model Strata serves and
how it is sized: every key in it becomes an argument for ``setup.py``, exactly as
``pixi run start-strata -- --<key> <value>`` would pass it.  Precedence, highest first: the
command line, the ``STRATA_*`` environment, ``strata.ini``.  The file is required -- the
settings live there and not in this code, so there is nothing here to keep in step with it.

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
INI_SECTION = "strata"
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

#: setup.py's switches that mean "install or reconfigure" instead of "start what is already
#: installed".  They are one-shot commands, so they belong on a command line and not in
#: strata.ini, where they would re-run on every start
COMMAND_FLAGS = frozenset(
    {"--setup", "--no-start", "--update", "--check", "--calibrate"}
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


def flag_name(key: str) -> str:
    """A strata.ini key as the flag it names: dashes or underscores, the `--` optional."""
    key = key.strip().replace("_", "-")
    return key if key.startswith("-") else f"--{key}"


def read_ini(path: Path | None = None) -> list[Pair]:
    """strata.ini as `(flag, value)` pairs, in the order it was written.

    `key = value`; a key with no value is a valueless flag, and a value of `off`/`no`/
    `false` drops one.  Inline comments need the whitespace before them, so a value that
    contains a `#` (an api key) keeps it.
    """
    path = path or ini_path()
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
    if not parser.has_section(INI_SECTION):
        sys.exit(f"{path}: no [{INI_SECTION}] section")

    pairs: list[Pair] = []
    for key, value in parser[INI_SECTION].items():
        flag = flag_name(key)
        value = None if value is None else value.strip()
        if flag in COMMAND_FLAGS:
            sys.exit(
                f"{path}: {flag} is a one-shot command, not a setting -- it would run on "
                f"every start. Pass it on the command line instead: "
                f"pixi run strata-install -- --{flag.lstrip('-')}"
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


def strata_args(cli: Sequence[str] = ()) -> list[Pair]:
    """The command line Strata gets: strata.ini, then the STRATA_* overrides, then the
    caller's own arguments -- so a command line beats the file and the file beats the
    environment."""
    return dedupe(read_ini() + env_args() + parse_args(cli))


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
    strata.ini visible, and scripts/strata-run.py re-prepares when it moves.
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


def prepared_args(data: Path) -> list[list[str]] | None:
    """What this wrapper last prepared this data folder with, or None when nothing records
    it (a model prepared before this check existed, or a data folder moved in)."""
    try:
        record = json.loads((data / PREPARED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    args = record.get("args") if isinstance(record, dict) else None
    return args if isinstance(args, list) else None


def record_prepared(data: Path, tag: str, signature: Sequence[Sequence[str]]) -> None:
    data.mkdir(parents=True, exist_ok=True)
    path = data / PREPARED_NAME
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
    candidates = [
        os.environ.get("STRATA_ROOT"),
        Path(prefix) / "opt" / "strata" if prefix else None,
        Path(prefix).parent / "strata" / "opt" / "strata" if prefix else None,
        repo_root() / ".pixi" / "envs" / "strata" / "opt" / "strata",
    ]
    return [path for path in candidates if path and (path / "setup.py").is_file()]


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
    order, which is also the one its start path serves when several models are installed."""
    return sorted(
        (path for path in root.glob("strata-*.json") if read_config(path)),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def installed_ports() -> list[str]:
    """The ports the installed run configs were prepared with, newest first."""
    out: list[str] = []
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
    found = installed_ports()
    return found[0] if found else None


def installed_context() -> str | None:
    """The context the newest installed run config serves, newest first."""
    for root in app_dirs():
        for path in model_configs(root):
            ctx = config_flag(path, "--max-context")
            if ctx:
                return ctx
    return None


def resolve_context(pairs: Sequence[Pair]) -> str | None:
    """The context this run serves: strata.ini / STRATA_CONTEXT / --context, else what the
    installed model was prepared with.  None when nothing says."""
    return flag_value(pairs, "--context") or installed_context()


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
    tmp = config.with_name(config.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    os.replace(tmp, config)
    return True


# ---------------------------------------------------------------------------
#  setup.py as a module
# ---------------------------------------------------------------------------
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
