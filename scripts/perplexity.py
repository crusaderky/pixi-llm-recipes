#!/usr/bin/env python3
"""Run llama-perplexity with KLD over a cross-product of arbitrary CLI options.

Reads a YAML config: a `common:` command prefix, a `baseline:` that creates the
logits dump, and a `cross-product:` -- a list of lists of option dicts, expanded
into every union of one dict per list.  Because the dicts hold *arbitrary*
llama-perplexity options, one sweep can vary the KV-cache quantization
(`cache-type-k`/`cache-type-v`/`kv-tail-tokens`), the *model* quantization
(`hf-repo`), or both at once, and the report labels the runs accordingly.

The baseline is run twice: first without --kl-divergence to generate the
logits file, then with --kl-divergence against its own logits to measure the
true KLD / top-1 noise floor and the reference speed (no dump I/O).
Streams subprocess output to the log file in real-time, one line at a time.

Watches for the perplexity ETA line (e.g. "perplexity: 16.34 seconds per
pass - ETA 5.43 minutes").  Aborts non-baseline runs whose ETA exceeds
max_eta_factor * baseline ETA.

Every run is preceded in the log by `#`-prefixed provenance: the
`llama-perplexity --version` string and the size / mtime / header hash of each
model shard.  A sweep is only meaningful while the binary stays fixed and each
model's weights stay fixed, and both can change under a multi-day sweep (a
rebuild between runs, or `--hf-repo` re-resolving a re-uploaded quant).  Such a
change shows up as an unexplained KLD offset against the stale
--kl-divergence-base dump -- including on the baseline rerun, whose KLD should
be ~0 -- so it must be visible in the log.  The recorded shard sizes are also
what the report sums into the weight half of its VRAM cost axis.

Usage:
    pixi r perplexity -c perplexity.yaml [-o perplexity.log] [--dry-run]

Config YAML example:
    common: >
        llama-perplexity --kl-divergence-base /tmp/logits.dat
        --ctx-size 512 -f sample-data/wiki.test.raw
        -fa on -ngl 99

    # Every union of one dict per list, i.e. 2 x 2 = 4 runs here.
    cross-product:
      - - {hf-repo: unsloth/gemma-4-E2B-it-GGUF:UD-Q4_K_XL}
        - {hf-repo: unsloth/gemma-4-E2B-it-GGUF:UD-Q8_K_XL}
      - - {cache-type-k: q8_0, cache-type-v: q8_0}
        - {cache-type-k: kvarn4, cache-type-v: kvarn4, kv-tail-tokens: 1024}

    # Baseline combo that creates the logits dump (no --kl-divergence). Mandatory.
    baseline: {cache-type-k: f16, cache-type-v: f16}

    # Optional: extra combos beyond the cross-product.
    include:
      - {cache-type-k: f16, cache-type-v: q8_0}

    # Optional: drop every combo that matches all the keys listed here.
    exclude:
      - {cache-type-k: q4_0}

    # Abort non-baseline runs whose ETA > baseline_eta * max_eta_factor.
    max_eta_factor: 4.0

Option keys are llama.cpp flag names without the leading dashes, emitted as
`--<key> <value>`, so use the *long* spelling (`hf-repo`, not `hf`) -- the short
ones are accepted and rewritten, since llama.cpp's dash count is not derivable
from the flag name.  A `label:` key is the one exception: it is not passed to
llama-perplexity but written to the log as a `# LABEL: ...` comment, which
overrides the report's own label for that run.

Keys must not collide across the lists of one cross-product (`label:` excepted:
those join with `|` in list order -- so label either every list of a product or
none of them, or the runs of an unlabelled list end up sharing a label with
their siblings).  Combos that make no sense are dropped
automatically: asymmetric KVarN (symmetric-only), a non-zero tail on an exact
f16/f16 cache, and a value cache more precise than the key cache.  `include`
entries are exempt from those three -- an explicit request is intentional -- but
not from `exclude`.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import itertools
import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any

import yaml
from kv_cache_common import BPW
from perplexity_common import (
    KLD_KEYS,
    canon,
    cmd_signature,
    iter_runs,
    parse_cmd_args,
    quant_matches,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Bytes hashed from the head of each model shard for the per-run provenance
# line.  Hashing whole files is impractical (weights run to tens of GiB) and
# unnecessary: the GGUF header and all metadata live at the start of shard 1,
# which is where a silently swapped model shows up -- a re-uploaded quant is a
# fresh file, and even a metadata-only patch (rope/context hparams) rewrites
# only those first few KiB.
HASH_BYTES = 1 << 20

# Regex to extract ETA from perplexity/kl_divergence progress lines.
# Baseline runs emit "perplexity:", non-baseline emit "kl_divergence:".
# Captures everything after "ETA " for flexible duration parsing.
# Matches lines like:
#   srv   58.206.857.490 I perplexity:  16.34 seconds per pass - ETA 5.43 minutes
#   srv   18.58.652.966 I kl_divergence: 1131.01 seconds per pass - ETA 6 hours 17.00 minutes
#   perplexity:  16.34 seconds per pass - ETA 5.43 minutes
ETA_RE = re.compile(
    r"(?:perplexity|kl_divergence):\s+[\d.]+\s+seconds per pass\s+-\s+ETA\s+(.+)"
)

# Parse individual duration components inside an ETA string.
# Handles "6 hours 17.00 minutes", "5.43 minutes", "30 seconds".
_ETA_PART_RE = re.compile(r"([\d.]+)\s*(hours?|minutes?|seconds?)")

# The one option key that never reaches llama-perplexity.
LABEL_KEY = "label"

# Options that every run of a sweep must agree on, because all of them are
# compared against a single --kl-divergence-base dump: the corpus, the chunk
# size and the chunk count fix that dump's contents, so varying one of them
# makes the comparison meaningless (llama-perplexity rejects the mismatch
# anyway).  They belong in `common:`.
INVARIANT_KEYS = frozenset({"file", "ctx-size", "chunks", "kl-divergence-base"})


# A llama.cpp option name, dashes stripped: no spaces, no `=`, no surprises.
_OPTION_NAME_RE = re.compile(r"^[A-Za-z0-9][\w.-]*$")


class ConfigError(ValueError):
    """The YAML config is wrong. Reported to the user as a message, not a
    traceback (see ``main``), because every instance is a "fix your YAML"."""


def is_kvarn(quant: str) -> bool:
    return quant.startswith("kvarn")


# Cache types ordered from most to least precise (bits-per-value descending),
# derived from the shared bpw table so the two can never drift apart. Used to
# drop combos where the value cache is more precise than the key cache (v > k),
# which is not a useful trade-off. Types absent from BPW are never dropped by
# the v > k rule. Equal-bpw types (e.g. q4_0 / iq4_nl) are ordered by their BPW
# insertion order, so one of the two arbitrarily counts as "more precise".
QUANT_PRECISION = sorted(BPW, key=BPW.__getitem__, reverse=True)
_PRECISION_RANK = {q: i for i, q in enumerate(QUANT_PRECISION)}

# A combo option value: a string to pass after the flag, or True for a flag that
# takes none (False means "omit the flag entirely").
Value = str | bool


def _fmt_value(v: Any) -> Value:
    """Normalise a YAML option value.

    Booleans stay booleans -- a bare flag, or no flag at all -- and everything
    else becomes the string the CLI will carry, which is also what makes an
    `exclude:` entry of `1024` match a logged `1024`.
    """
    if isinstance(v, bool):
        return v
    if v is None:
        return True
    return str(v)


class Combo:
    """One sweep point: the CLI options that distinguish it from the others.

    Keys are canonical (dash-free, long-spelling) llama.cpp flag names; values
    are strings, or True for a flag that takes none.  ``label`` is held apart:
    it is logged as a comment rather than passed to llama-perplexity, and two
    combos differing only by label are the same run.
    """

    __slots__ = ("args", "label")

    def __init__(self, args: dict[str, Value], label: str = "") -> None:
        self.args = args
        self.label = label

    @classmethod
    def from_yaml(cls, d: dict[str, Any], where: str) -> Combo:
        args: dict[str, Value] = {}
        label = ""
        for key, value in d.items():
            if not _OPTION_NAME_RE.match(canon(str(key))):
                # `{a: 1, b = 2}` is a YAML flow mapping whose second entry is
                # the *key* "b = 2" with no value, which would otherwise sail
                # through as a flag and reach llama-perplexity as three tokens.
                raise ConfigError(
                    f"{where}: {key!r} is not an option name"
                    + (
                        " -- write `key: value`, not `key = value`"
                        if "=" in str(key)
                        else ""
                    )
                )
            if canon(key) == LABEL_KEY:
                label = str(value)
            elif canon(key) in KLD_KEYS:
                raise ConfigError(
                    f"{where}: --{canon(key)} is managed by this script and "
                    "must not appear in a combo"
                )
            else:
                args[canon(key)] = _fmt_value(value)
        return cls(args, label)

    @property
    def key(self) -> frozenset[tuple[str, Value]]:
        """Run identity: the options, order-insensitive, label excluded."""
        return frozenset(self.args.items())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Combo) and self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)

    def __str__(self) -> str:
        return self.label or " ".join(
            k if v is True else f"{k}={v}" for k, v in self.args.items()
        )

    def cli(self) -> str:
        """The ``--key value`` fragment this combo appends to ``common:``."""
        parts = []
        for key, value in self.args.items():
            if value is False:
                continue
            parts.append(f"--{key}")
            if value is not True:
                parts.append(shlex.quote(value))
        return " ".join(parts)

    def matches(self, pattern: Combo) -> bool:
        """Whether every option of *pattern* is present here with the same value.

        This is what makes `exclude: [{cache-type-k: q4_0}]` drop every combo
        with that key cache, whatever its value cache, tail or model.
        """
        return all(self.args.get(k) == v for k, v in pattern.args.items())

    # --- combos that make no sense -----------------------------------------

    def _cache_types(self, base: dict[str, Value]) -> tuple[str, str, int]:
        """Effective (K type, V type, tail): ``common:`` overlaid with the combo,
        so a cache type pinned in ``common:`` is still seen, and llama.cpp's own
        defaults fill in the rest."""
        eff: dict[str, Value] = {**base, **self.args}
        k, v, tail = (
            eff.get("cache-type-k", "f16"),
            eff.get("cache-type-v", "f16"),
            eff.get("kv-tail-tokens", 0),
        )
        return (
            "f16" if k is True else str(k),
            "f16" if v is True else str(v),
            0 if tail is True else int(tail),
        )

    def nonsense(self, base: dict[str, Value]) -> str:
        """Why this combo is not worth running, or "" if it is.

        * asymmetric KVarN -- KVarN is symmetric-only,
        * a non-zero tail on a fully-exact f16/f16 cache,
        * a value cache more precise than the key cache (v > k); cache types
          absent from the bpw table are never flagged by this one.
        """
        k, v, tail = self._cache_types(base)
        if (is_kvarn(k) or is_kvarn(v)) and k != v:
            return "asymmetric KVarN"
        if tail > 0 and k == "f16" and v == "f16":
            return "exact tail on an f16/f16 cache"
        rk, rv = _PRECISION_RANK.get(k), _PRECISION_RANK.get(v)
        if rk is not None and rv is not None and rv < rk:  # lower rank = finer
            return f"V cache ({v}) finer than K cache ({k})"
        return ""


class PerplexityConfig(BaseModel):
    """Config schema for the perplexity sweep."""

    # extra="forbid" rejects unknown top-level keys; the option dicts inside
    # baseline / cross-product / include / exclude are deliberately free-form.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    common: str = Field(description="llama-perplexity command prefix")
    baseline: dict[str, Any] = Field(
        description=(
            "Baseline combo that creates the logits dump (no --kl-divergence), "
            "then is rerun with --kl-divergence against its own logits"
        )
    )
    cross_product: list[list[dict[str, Any]]] = Field(
        default_factory=list,
        alias="cross-product",
        description=(
            "Lists of option dicts, expanded into every union of one dict per "
            "list. Keys must not collide across lists (label: excepted)."
        ),
    )
    include: list[dict[str, Any]] = Field(
        default_factory=list, description="Extra combos beyond the cross-product"
    )
    exclude: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Drop every combo matching all the keys of an entry",
    )
    max_eta_factor: float = Field(
        default=4.0,
        description=(
            "Abort non-baseline runs whose ETA > baseline_eta * max_eta_factor. "
            "Set to 0 to disable."
        ),
        ge=0,
    )

    @field_validator("common", mode="after")
    @classmethod
    def parse_common(cls, v: str) -> str:
        return v.strip()

    @field_validator("cross_product", "include", "exclude", mode="before")
    @classmethod
    def none_to_empty(cls, v: object) -> object:
        return v or []

    @property
    def common_args(self) -> dict[str, Value]:
        """Canonical options of the ``common:`` prefix."""
        return parse_cmd_args(self.common)

    @property
    def logits(self) -> pathlib.Path:
        """The ``--kl-divergence-base`` dump every run is measured against."""
        # Absent from `common:` (None), or passed with no file after it (True).
        path = self.common_args.get("kl-divergence-base")
        if not isinstance(path, str):
            raise ConfigError(
                "common: must pass --kl-divergence-base <file>; without it the "
                "baseline has nowhere to write the logits that every other run "
                "is compared against"
            )
        return pathlib.Path(path)

    @property
    def baseline_combo(self) -> Combo:
        return Combo.from_yaml(self.baseline, "baseline")

    def _cross_product(self) -> list[Combo]:
        """Expand ``cross-product:`` into one combo per union of its lists."""
        combos = []
        for groups in itertools.product(*self.cross_product):
            args: dict[str, Value] = {}
            labels = []
            for i, group in enumerate(groups):
                combo = Combo.from_yaml(group, f"cross-product list {i}")
                if combo.label:
                    labels.append(combo.label)
                for key, value in combo.args.items():
                    if key in args:
                        raise ConfigError(
                            f"cross-product: key collision on --{key} between "
                            f"lists of the same product ({args[key]!r} vs "
                            f"{value!r}); an option may only be varied by one "
                            "list"
                        )
                    args[key] = value
            combos.append(Combo(args, "|".join(labels)))
        return combos

    def combos(self) -> list[Combo]:
        """The ordered run list: baseline, then cross-product, then `include`;
        minus `exclude`, minus (cross-product only) combos that make no sense.

        The baseline comes first and is exempt from both filters: it creates the
        logits dump, so the sweep is nothing without it.
        """
        base = self.common_args
        exclude = [Combo.from_yaml(e, "exclude") for e in self.exclude]
        # Insertion-ordered, keyed by run identity: a combo reached twice (by
        # the cross-product and by `include`, say) is run once, keeping the
        # first spelling but adopting an explicit label from either.
        out: dict[frozenset[tuple[str, Value]], Combo] = {}

        def add(combo: Combo) -> None:
            if (prev := out.get(combo.key)) is None:
                out[combo.key] = combo
            elif combo.label and not prev.label:
                prev.label = combo.label

        def keep(combo: Combo, check_nonsense: bool) -> bool:
            for pattern in exclude:
                if combo.matches(pattern):
                    print(f"[DROP] {combo} (excluded by {pattern})")
                    return False
            if check_nonsense and (why := combo.nonsense(base)):
                print(f"[DROP] {combo} ({why})")
                return False
            return True

        add(self.baseline_combo)
        for combo in self._cross_product():
            if keep(combo, True):
                add(combo)
        for entry in self.include:
            combo = Combo.from_yaml(entry, "include")
            if keep(combo, False):
                add(combo)
        return list(out.values())

    def validate_invariants(self) -> None:
        """Reject options that must not vary between the runs of one sweep."""
        mentioned: dict[str, str] = {}
        for where, dicts in (
            ("baseline", [self.baseline]),
            ("cross-product", [d for group in self.cross_product for d in group]),
            ("include", self.include),
        ):
            for d in dicts:
                for key in d:
                    if canon(key) in INVARIANT_KEYS:
                        mentioned[canon(key)] = where
        if mentioned:
            listed = ", ".join(f"--{k} (in {v})" for k, v in sorted(mentioned.items()))
            raise ConfigError(
                "these options must be identical for every run and so belong in "
                f"`common:`, not in a combo: {listed}. Every run is compared "
                "against one --kl-divergence-base dump, whose contents are fixed "
                "by the corpus, the context size and the chunk count."
            )


def parse_eta_minutes(line: str) -> float | None:
    """Parse ETA from a perplexity/kl_divergence progress line, return minutes.

    Extracts lines matching::

        perplexity:  <float> seconds per pass - ETA <float> <unit>
        kl_divergence:  <float> seconds per pass - ETA <float> hours <float> minutes

    Handles compound ETA like ``6 hours 17.00 minutes``
    and simple ``5.43 minutes`` / ``30 seconds``.

    Returns ETA in minutes, or None if line does not match.
    """
    m = ETA_RE.search(line)
    if not m:
        return None

    eta_str = m.group(1).rstrip(".")  # drop trailing period
    total = 0.0
    found = False
    for value_str, unit in _ETA_PART_RE.findall(eta_str):
        found = True
        v = float(value_str)
        if unit.startswith("hour"):
            total += v * 60
        elif unit.startswith("minute"):
            total += v
        elif unit.startswith("second"):
            total += v / 60
    return total if found else None


def find_logits_run(
    logfile: pathlib.Path, signature: frozenset[tuple[str, Value]]
) -> tuple[float | None, bool] | None:
    """The logits-dump run matching *signature*, if the log has one.

    Returns ``(ETA in minutes or None, completed)``, or None when the log holds
    no such run -- which is how a dump left behind by a *different* sweep is
    caught.  The dump run is the one *without* ``--kl-divergence``; its ETA is
    the abort threshold until the baseline rerun supersedes it.
    """
    if not logfile.exists():
        return None
    for run in iter_runs(logfile.read_text()):
        if run.has_kld or run.signature != signature:
            continue
        eta = next(
            (e for line in run.lines if (e := parse_eta_minutes(line)) is not None),
            None,
        )
        return eta, not run.aborted
    return None


def llama_cache_dir() -> pathlib.Path:
    """Hugging Face hub cache directory that llama.cpp downloads ``-hf`` into.

    Mirrors ``get_cache_directory()`` in llama.cpp's ``common/hf-cache.cpp``,
    including the env var precedence.
    """
    for var, suffix in (
        ("LLAMA_CACHE", ""),
        ("HF_HUB_CACHE", ""),
        ("HUGGINGFACE_HUB_CACHE", ""),
        ("HF_HOME", "hub"),
        ("XDG_CACHE_HOME", "huggingface/hub"),
    ):
        if base := os.environ.get(var):
            return pathlib.Path(base) / suffix
    return pathlib.Path.home() / ".cache" / "huggingface" / "hub"


# Split-GGUF shard suffix, e.g. model-00002-of-00003.gguf.
_SHARD_RE = re.compile(r"-\d{5}-of-\d{5}\.gguf$")


def shard_siblings(path: pathlib.Path) -> list[pathlib.Path]:
    """All shards of a split GGUF, or just *path* if it is not split."""
    m = _SHARD_RE.search(path.name)
    if not m:
        return [path]
    return sorted(path.parent.glob(f"{path.name[: m.start()]}-*-of-*.gguf"))


def resolve_model_files(cmd: str) -> list[pathlib.Path]:
    """Model files that *cmd* will load.

    ``--model`` is taken verbatim (plus its sibling shards); for ``--hf-repo``
    the Hugging Face hub cache is globbed for the repo's shards.  Every match is
    returned rather than just the newest: two snapshots of one repo in the cache
    means the weights moved under the sweep, which is what provenance is for.
    """
    args = parse_cmd_args(cmd)

    if isinstance(path := args.get("model"), str):
        return shard_siblings(pathlib.Path(path))

    spec = args.get("hf-repo")
    if not isinstance(spec, str):
        return []
    repo, _, quant = spec.partition(":")
    # HF hub layout: models--<org>--<repo>/snapshots/<commit>/[<quant>/]*.gguf,
    # symlinked into blobs/.  The commit in the path is itself provenance.
    repo_dir = llama_cache_dir() / f"models--{repo.replace('/', '--')}"
    files = sorted(repo_dir.glob("snapshots/*/**/*.gguf"))
    # A filter that matches nothing means the requested weights are not in the
    # cache yet -- this run is about to download them -- so the honest answer is
    # nothing, and the post-run provenance will describe what arrived.  Falling
    # back to the unfiltered set here instead would attribute every *other*
    # quant of the repo to this run, which the report then sums into its weight
    # figure.
    if isinstance(hf_file := args.get("hf-file"), str):
        return [f for f in files if f.name == hf_file]
    if quant:
        # The quant tag is a subdirectory for split models, part of the file
        # name for single-file ones.
        return [f for f in files if quant_matches(str(f), quant)]
    return files


def file_provenance(path: pathlib.Path) -> str:
    """Size, mtime and header hash of one model file."""
    try:
        st = path.stat()
        with path.open("rb") as f:
            digest = hashlib.sha256(f.read(HASH_BYTES)).hexdigest()
    except OSError as e:
        return f"{path} <unreadable: {e.strerror}>"
    mtime = datetime.datetime.fromtimestamp(st.st_mtime, datetime.UTC)
    # The byte count is in the label so the hash can be reproduced by hand:
    # head -c <n> <path> | sha256sum
    return (
        f"{path} size={st.st_size} mtime={mtime.isoformat(timespec='seconds')} "
        f"sha256/{HASH_BYTES}B={digest}"
    )


def binary_version() -> str:
    """``llama-perplexity --version`` collapsed onto one line."""
    try:
        proc = subprocess.run(
            ["llama-perplexity", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        # Let the run itself report a missing binary; don't die before logging.
        return f"<unknown: {e.strerror}>"
    # Drop backend chatter (e.g. CUDA init errors on a GPU-less host).
    lines = [
        line.strip()
        for line in (proc.stdout + proc.stderr).splitlines()
        if "version:" in line or "built with" in line
    ]
    return " | ".join(lines) or "<unknown>"


def provenance_lines(cmd: str) -> list[str]:
    """``#``-prefixed binary and model provenance for one run of *cmd*."""
    lines = [f"# llama-perplexity: {binary_version()}"]
    try:
        files = resolve_model_files(cmd)
    except (ValueError, OSError) as e:
        # Provenance must never abort a multi-day sweep.
        return lines + [f"# model: <unresolved: {e}>"]
    if not files:
        return lines + ["# model: <unresolved>"]
    return lines + [f"# model: {file_provenance(f)}" for f in files]


def run_llama_perplexity(
    cmd: str,
    logfile: pathlib.Path,
    baseline_eta: float | None = None,
    max_eta_factor: float = 4.0,
    label: str = "",
    log_label: str = "",
) -> float | None:
    """Run *cmd*, streaming stdout to *logfile* in real-time.

    Watches for the perplexity ETA line.  If *baseline_eta* is provided
    and the run's ETA exceeds ``baseline_eta * max_eta_factor``, the
    process is aborted.

    *label* names the run in the progress and abort messages; *log_label* -- set
    only when the config asked for one -- is written above the command line as a
    ``# LABEL:`` comment, from which the report takes the run's display name.

    Returns ETA in minutes (or None if no ETA line seen).
    Returns None for aborted runs.
    """
    eta_minutes: float | None = None
    aborted = False

    with open(logfile, "a") as f:
        provenance = provenance_lines(cmd)
        f.writelines(line + "\n" for line in provenance)
        if log_label:
            f.write(f"# LABEL: {log_label}\n")
        f.write(cmd + "\n")
        f.flush()

        process = subprocess.Popen(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )
        assert process.stdout is not None

        for line in iter(process.stdout.readline, ""):
            f.write(line)
            f.flush()

            eta = parse_eta_minutes(line)
            if eta is not None:
                eta_minutes = eta
                # Print ETA line to stdout (line already has trailing \n from readline)
                sys.stdout.write(f"  {line}")
                sys.stdout.flush()

                # Abort check for non-baseline runs
                if (
                    baseline_eta is not None
                    and max_eta_factor > 0
                    and eta > baseline_eta * max_eta_factor
                ):
                    aborted = True
                    msg = (
                        f"  [ABORT] {label} ETA {eta:.2f}m "
                        f"> {max_eta_factor:.1f}x baseline {baseline_eta:.2f}m\n"
                    )
                    sys.stdout.write(msg)
                    sys.stdout.flush()
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    break

        if not aborted:
            process.wait()

        # The run that downloads the model cannot be described before it starts,
        # so re-log provenance if it resolved differently afterwards. Silent
        # when nothing moved, which is every run of a healthy sweep.
        if (after := provenance_lines(cmd)) != provenance:
            f.writelines(line + "\n" for line in after)

        if aborted:
            f.write(
                f"--- ABORTED (ETA {eta_minutes:.2f}m > "
                f"{max_eta_factor}x baseline {baseline_eta:.2f}m) ---\n"
            )
        else:
            f.write("------------------------------\n")

    return eta_minutes if not aborted else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run llama-perplexity with KLD over a cross-product of arbitrary "
            "CLI options (KV-cache quants, model quants, anything else).  "
            "Streams output in real-time; aborts runs exceeding "
            "max_eta_factor * baseline ETA."
        )
    )
    parser.add_argument(
        "-c",
        "--config",
        type=pathlib.Path,
        default=pathlib.Path("perplexity.yaml"),
        help="Path to YAML config file (default: perplexity.yaml)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("perplexity.log"),
        help="Output log file (default: perplexity.log)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without running llama-perplexity",
    )
    return parser.parse_args()


def file_size(path: pathlib.Path) -> str:
    """Human-readable size of *path*, or empty string if absent."""
    if path.exists():
        return f"{path.stat().st_size / 2**30:.1f} GiB"
    return ""


def main() -> None:
    args = parse_args()
    logfile: pathlib.Path = args.output
    # Every way the config can be wrong lands here, and a traceback would only
    # bury the message: these are all "fix your YAML" errors.
    try:
        with open(args.config) as f:
            cfg = PerplexityConfig.model_validate(yaml.safe_load(f))
        cfg.validate_invariants()
        logits = cfg.logits
        baseline = cfg.baseline_combo
        combos = cfg.combos()
    except (ValidationError, ValueError) as e:
        sys.exit(f"ERROR in {args.config}: {e}")

    print(f"Baseline: {baseline}")
    print(f"Logits dump: {logits}")
    print(f"Max ETA factor: {cfg.max_eta_factor}")
    print(f"Combos: {len(combos)}")
    print(f"Output log: {logfile}")
    if logfile.exists():
        print(f"  {logfile} exists, appending")

    # --- Determine baseline ETA ---
    baseline_eta: float | None = None
    cmd_base = f"{cfg.common} {baseline.cli()}".strip()

    if logits.exists():
        found = find_logits_run(logfile, cmd_signature(cmd_base))
        hint = ", completed in log" if found and found[1] else ""
        print(
            f"[SKIP] baseline {baseline} ({logits} ({file_size(logits)}) exists{hint})"
        )
        if found is None:
            # A dump left behind by another sweep describes another model, and
            # every KLD measured against it is then silently meaningless.
            print(
                f"  WARNING: {logfile} holds no logits run matching this "
                f"baseline. If {logits} was written by a different sweep, "
                "delete it and start over.",
                file=sys.stderr,
            )
        elif (baseline_eta := found[0]) is None:
            print("  Baseline ETA not found in log -- cannot apply max_eta_factor")
        else:
            print(f"  Baseline ETA: {baseline_eta:.2f} minutes")
    elif args.dry_run:
        print(f"[DRY RUN] baseline {baseline}\n  {cmd_base}")
    else:
        print(f"[RUN] baseline {baseline}")
        baseline_eta = run_llama_perplexity(
            cmd_base,
            logfile,
            label=f"baseline {baseline}",
            log_label=baseline.label,
        )
        if baseline_eta is not None:
            print(f"  Baseline ETA: {baseline_eta:.2f} minutes")
        else:
            print("  No ETA line seen for baseline run")

    # --- Run KLD combos (baseline rerun first, then the rest) ---
    # Combos with a --kl-divergence run already in the log (any outcome) are
    # skipped. Read once: each combo is unique and visited only here.
    present = {
        run.signature
        for run in (iter_runs(logfile.read_text()) if logfile.exists() else ())
        if run.has_kld
    }

    for combo in combos:
        cmd = f"{cfg.common} {combo.cli()} --kl-divergence".strip()

        if cmd_signature(cmd) in present:
            print(f"[SKIP] {combo} (already in {logfile})")
            continue

        if args.dry_run:
            print(f"[DRY RUN] {combo}\n  {cmd}")
            continue

        if combo == baseline:
            # Baseline rerun against its own logits: measures the true KLD /
            # top-1 noise floor and the reference speed without the
            # logits-dump I/O. Never aborted; its ETA replaces the
            # logits-writing baseline ETA as the abort threshold.
            print(f"[RUN] {combo} (baseline rerun)")
            rerun_eta = run_llama_perplexity(
                cmd,
                logfile,
                label=f"baseline-rerun {combo}",
                log_label=combo.label,
            )
            if rerun_eta is not None:
                baseline_eta = rerun_eta
                print(f"  Baseline ETA (rerun): {baseline_eta:.2f} minutes")
            continue

        print(f"[RUN] {combo}")
        run_llama_perplexity(
            cmd,
            logfile,
            baseline_eta=baseline_eta,
            max_eta_factor=cfg.max_eta_factor,
            label=str(combo),
            log_label=combo.label,
        )

    if logits.exists():
        print(f"\nLogits file: {logits} ({file_size(logits)})")


if __name__ == "__main__":
    main()
