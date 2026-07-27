#!/usr/bin/env python3
"""Run llama-perplexity with KLD over cartesian product of K/V quants.

Reads K/V quant lists and the common prefix from a YAML config file,
builds a cartesian product, and runs each combo against a baseline.
Streams subprocess output to log file in real-time, one line at a time.

Watches for the perplexity ETA line (e.g. "perplexity: 16.34 seconds per
pass - ETA 5.43 minutes").  Aborts non-baseline runs whose ETA exceeds
max_eta_factor * baseline ETA.

Usage:
    pixi r kv-perplexity -c kv-perplexity-config.yaml [-o perplexity.log] [--dry-run]

A combo is a {cache-type-k, cache-type-v, kv-tail-tokens} triple; kv-tail-tokens
defaults to 0 (and is then never emitted, keeping the command line compatible
with mainline llama.cpp).  The full combo set is the cartesian product
k_quants x v_quants x kv-tail-tokens, plus `include`, minus `exclude`, minus
combos that make no sense: asymmetric KVarN (symmetric-only), a non-zero tail on
a fully-exact f16/f16 cache, and a value cache more precise than the key (v > k).

Config YAML example:
    common: >
        llama-perplexity --kl-divergence-base /tmp/logits.dat
        --ctx-size 512 -f sample-data/wiki.test.raw
        -hf unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL
        -fa on -ngl 99

    k_quants: [f16, q8_0, q5_1, q5_0, q4_1, q4_0, iq4_nl]
    v_quants: [f16, q8_0, q5_1, q5_0, q4_1, q4_0, iq4_nl]
    kv-tail-tokens: [0, 1024, 2048]

    # Baseline combo that creates logits.dat (no --kl-divergence). Mandatory.
    baseline: {cache-type-k: f16, cache-type-v: f16}

    # Optional: add extra combos not in cartesian product.
    include:
      - {cache-type-k: f16, cache-type-v: q8_0}
      - {cache-type-k: kvarn4, cache-type-v: kvarn4, kv-tail-tokens: 1024}

    # Optional: remove combos from the set.
    exclude:
      - {cache-type-k: q4_0, cache-type-v: q4_0}

    # Abort non-baseline runs whose ETA > baseline_eta * max_eta_factor.
    # Default: 4.0.  Set to 0 to disable abort.
    max_eta_factor: 4.0
"""

import argparse
import itertools
import pathlib
import re
import subprocess
import sys

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

LOGITS = pathlib.Path("/tmp/logits.dat")

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


def kv_cli_args(k: str, v: str, tail: int = 0) -> str:
    """Build the -ctk/-ctv (+ --kv-tail-tokens) CLI fragment identifying a combo.

    ``--kv-tail-tokens 0`` is never emitted, so the command line stays compatible
    with mainline llama.cpp, which does not know the flag.
    """
    args = f"-ctk {k} -ctv {v}"
    if tail:
        args += f" --kv-tail-tokens {tail}"
    return args


def is_kvarn(quant: str) -> bool:
    return quant.startswith("kvarn")


# Cache types ordered from most to least precise (bytes-per-value descending).
# Used to drop combos where the value cache is more precise than the key cache
# (v > k), which is not a useful trade-off. Types absent from this list are
# never dropped by the v > k rule.
QUANT_PRECISION = [
    "f16",  # 2.0
    "q8_0",  # 1.0625
    "kvarn8",  # 1.046875
    "q6_1",  # 0.875
    "q6_0",  # 0.8125
    "kvarn6",  # 0.796875
    "q5_1",  # 0.75
    "q5_0",  # 0.6875
    "kvarn5",  # 0.671875
    "q4_1",  # 0.625
    "q4_0",  # 0.5625
    "iq4_nl",  # 0.5625
    "kvarn4",  # 0.546875
    "q3_1",  # 0.5
    "q3_0",  # 0.4375
    "kvarn3",  # 0.421875
    "q2_1",  # 0.375
    "kvarn2",  # 0.296875
    "q2_0",  # 0.28125
]
_PRECISION_RANK = {q: i for i, q in enumerate(QUANT_PRECISION)}


# Extract (ctk, ctv, tail) from a command line. --kv-tail-tokens 0 is never
# written (see kv_cli_args), so its absence means tail == 0.
_COMBO_RE = re.compile(r"-ctk\s+(\S+)\s+-ctv\s+(\S+)")
_TAIL_RE = re.compile(r"--kv-tail-tokens\s+(\d+)")


def combos_in_log(text: str) -> set[tuple[str, str, int]]:
    """Every (ctk, ctv, tail) combo already present in the log (any outcome)."""
    found: set[tuple[str, str, int]] = set()
    for line in text.splitlines():
        m = _COMBO_RE.search(line)
        if not m:
            continue
        tm = _TAIL_RE.search(line)
        found.add((m.group(1), m.group(2), int(tm.group(1)) if tm else 0))
    return found


class KVCombo(BaseModel):
    """One (cache-type-k, cache-type-v, kv-tail-tokens) benchmark combo."""

    # extra="forbid" rejects unknown keys; frozen makes it hashable (dict key /
    # set member); populate_by_name lets us build combos by field name in code
    # while the YAML uses the hyphenated aliases.
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    k: str = Field(alias="cache-type-k")
    v: str = Field(alias="cache-type-v")
    tail: int = Field(default=0, alias="kv-tail-tokens", ge=0)

    def __str__(self) -> str:
        s = f"{self.k}/{self.v}"
        if self.tail:
            s += f" t{self.tail}"
        return s

    @property
    def kvarn_asymmetric(self) -> bool:
        """KVarN is symmetric-only: a kvarn on one side needs the same on the other."""
        return (is_kvarn(self.k) or is_kvarn(self.v)) and self.k != self.v

    @property
    def redundant_f16_tail(self) -> bool:
        """An exact tail is pointless on an already-exact f16/f16 cache."""
        return self.tail > 0 and self.k == "f16" and self.v == "f16"

    @property
    def value_more_precise_than_key(self) -> bool:
        """v > k: value cache more precise than key cache (not a useful trade-off).
        Types missing from QUANT_PRECISION are never flagged."""
        rk = _PRECISION_RANK.get(self.k)
        rv = _PRECISION_RANK.get(self.v)
        if rk is None or rv is None:
            return False
        return rv < rk  # lower rank == more precise


class KVPConfig(BaseModel):
    """Config schema for KV perplexity benchmark."""

    common: str = Field(description="llama-perplexity command prefix")
    baseline: KVCombo = Field(
        description="Baseline combo that creates logits.dat (no --kl-divergence)"
    )
    k_quants: list[str] = Field(description="Key cache quantizations")
    v_quants: list[str] = Field(
        description="Value cache quantizations (cartesian product with k_quants)"
    )
    kv_tail_tokens: list[int] = Field(
        default_factory=lambda: [0],
        alias="kv-tail-tokens",
        description="Exact KV-cache tail sizes (cartesian product; 0 = no tail)",
    )
    include: list[KVCombo] = Field(
        default_factory=list, description="Extra combos beyond cartesian product"
    )
    exclude: list[KVCombo] = Field(
        default_factory=list, description="Combos to remove from the set"
    )
    max_eta_factor: float = Field(
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

    @field_validator("kv_tail_tokens", mode="before")
    @classmethod
    def default_tail(cls, v: object) -> object:
        return v or [0]

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def none_to_empty(cls, v: object) -> object:
        return v or []

    @property
    def quants(self) -> list[KVCombo]:
        """Build the ordered combo set: cartesian product + include - exclude,
        dropping combos that make no sense:

        * asymmetric KVarN (KVarN is symmetric-only),
        * a non-zero tail on a fully-exact f16/f16 cache,
        * a value cache more precise than the key cache (v > k).
        """
        # Insertion-ordered set
        combos = {self.baseline: None}
        for k, v, tail in itertools.product(
            self.k_quants, self.v_quants, self.kv_tail_tokens
        ):
            combos[KVCombo(k=k, v=v, tail=tail)] = None
        combos.update(dict.fromkeys(self.include))
        exclude = set(self.exclude)
        return [
            c
            for c in combos
            if c not in exclude
            and not c.kvarn_asymmetric
            and not c.redundant_f16_tail
            and not c.value_more_precise_than_key
        ]


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


def _has_completed_run(text: str, marker: str) -> bool:
    """Check if *text* contains at least one completed (non-aborted) section
    whose command line includes *marker*.

    A section starts at a command line and ends at either
    ``--- ABORTED ... ---`` (incomplete) or ``------------------------------``
    (completed).
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if marker in line:
            for j in range(i + 1, min(len(lines), i + 1000)):
                stripped = lines[j].strip()
                if stripped == "------------------------------":
                    return True
                if stripped.startswith("--- ABORTED"):
                    break  # this occurrence was aborted, keep looking
    return False


def find_baseline_eta_in_log(
    logfile: pathlib.Path, marker: str
) -> tuple[float, str] | None:
    """Search log file for the baseline ETA line.

    The baseline section matches *marker* (its ``-ctk/-ctv [--kv-tail-tokens]``
    fragment) but has no ``--kl-divergence``.
    Returns ``(eta_minutes, raw_line)`` or None if not found.
    """
    text = logfile.read_text()
    lines = text.split("\n")

    capturing = False
    for line in lines:
        if not capturing and marker in line and "--kl-divergence" not in line.split():
            capturing = True
            continue
        if capturing:
            stripped = line.strip()
            if stripped == "------------------------------" or stripped.startswith(
                "---"
            ):
                break
            eta = parse_eta_minutes(line)
            if eta is not None:
                return eta, line
    return None


def run_llama_perplexity(
    cmd: str,
    logfile: pathlib.Path,
    baseline_eta: float | None = None,
    max_eta_factor: float = 4.0,
    label: str = "",
) -> float | None:
    """Run *cmd*, streaming stdout to *logfile* in real-time.

    Watches for the perplexity ETA line.  If *baseline_eta* is provided
    and the run's ETA exceeds ``baseline_eta * max_eta_factor``, the
    process is aborted.

    Returns ETA in minutes (or None if no ETA line seen).
    Returns None for aborted runs.
    """
    eta_minutes: float | None = None
    aborted = False

    with open(logfile, "a") as f:
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
                if baseline_eta is not None and max_eta_factor > 0:
                    if eta > baseline_eta * max_eta_factor:
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
            "Run llama-perplexity with KLD over cartesian product "
            "of K/V quants.  Streams output in real-time; aborts "
            "runs exceeding max_eta_factor * baseline ETA."
        )
    )
    parser.add_argument(
        "-c",
        "--config",
        type=pathlib.Path,
        default=pathlib.Path("kv-perplexity.yaml"),
        help="Path to YAML config file (default: kv-perplexity.yaml)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("kv-perplexity.log"),
        help="Output log file (default: kv-perplexity.log)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without running llama-perplexity",
    )
    return parser.parse_args()


def logits_size() -> str:
    """Human-readable size of LOGITS, or empty string if absent."""
    if LOGITS.exists():
        size = LOGITS.stat().st_size
        return f"{size / 2**30:.1f} GiB"
    return ""


def main() -> None:
    args = parse_args()
    with open(args.config) as f:
        cfg = KVPConfig.model_validate(yaml.safe_load(f))

    logfile: pathlib.Path = args.output

    kv_args_base = kv_cli_args(cfg.baseline.k, cfg.baseline.v, cfg.baseline.tail)

    print(f"Baseline: {kv_args_base}")
    print(f"Max ETA factor: {cfg.max_eta_factor}")
    print(f"Output log: {logfile}")
    if logfile.exists():
        print(f"  {logfile} exists, appending")
    else:
        with open(logfile, "w") as f:
            f.write("llama-perplexity --version\n")
            f.flush()
            subprocess.run(
                ["llama-perplexity", "--version"],
                stdout=f,
                stderr=subprocess.STDOUT,
                check=True,
            )
            f.write("------------------------------\n")

    # --- Determine baseline ETA ---
    baseline_eta: float | None = None

    cmd_base = f"{cfg.common} {kv_args_base}"

    if LOGITS.exists():
        # Determine if baseline is also completed in log
        log_completed = (
            _has_completed_run(logfile.read_text(), kv_args_base)
            if logfile.exists()
            else False
        )
        log_hint = f", completed in {logfile}" if log_completed else ""
        print(
            f"[SKIP] baseline {kv_args_base} "
            f"({LOGITS} ({logits_size()}) exists{log_hint})"
        )
        baseline_result = find_baseline_eta_in_log(logfile, kv_args_base)
        if baseline_result is not None:
            baseline_eta, eta_line = baseline_result
            sys.stdout.write(f"  {eta_line}\n")
            sys.stdout.flush()
        else:
            print("  Baseline ETA not found in log -- cannot apply max_eta_factor")
    else:
        if args.dry_run:
            print(f"[DRY RUN] baseline {kv_args_base}")
        else:
            print(f"[RUN] baseline {kv_args_base}")
            baseline_eta = run_llama_perplexity(
                cmd_base, logfile, label=f"baseline {kv_args_base}"
            )
            if baseline_eta is not None:
                print(f"  Baseline ETA: {baseline_eta:.2f} minutes")
            else:
                print("  No ETA line seen for baseline run")

    # --- Run non-baseline combos ---
    # Combos already in the log (any outcome) are skipped. Read once: each combo
    # in cfg.quants is unique and visited only here.
    present = combos_in_log(logfile.read_text()) if logfile.exists() else set()

    for combo in cfg.quants:
        if combo == cfg.baseline:
            continue

        kv_args = kv_cli_args(combo.k, combo.v, combo.tail)
        cmd = f"{cfg.common} {kv_args} --kl-divergence"

        if (combo.k, combo.v, combo.tail) in present:
            print(f"[SKIP] {kv_args} (already in {logfile})")
            continue

        if args.dry_run:
            print(f"[DRY RUN] {kv_args}")
            continue

        print(f"[RUN] {kv_args}")
        run_llama_perplexity(
            cmd,
            logfile,
            baseline_eta=baseline_eta,
            max_eta_factor=cfg.max_eta_factor,
            label=kv_args,
        )

    # Cleanup: print logits size on exit
    if LOGITS.exists():
        print(f"\nLogits file: {LOGITS} ({logits_size()})")


if __name__ == "__main__":
    main()
