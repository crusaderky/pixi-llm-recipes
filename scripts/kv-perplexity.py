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

Config YAML example:
    common: >
        llama-perplexity --kl-divergence-base /tmp/logits.dat
        --ctx-size 512 -f sample-data/wiki.test.raw
        -hf unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL
        -fa on -ngl 99

    k_quants: [f16, q8_0, q5_1, q5_0, q4_1, q4_0, iq4_nl, turbo4, turbo3, turbo2]
    v_quants: [f16, q8_0, q5_1, q5_0, q4_1, q4_0, iq4_nl, turbo4, turbo3, turbo2]

    # Baseline combo that creates logits.dat (no --kl-divergence). Mandatory.
    baseline: f16/f16

    # Optional: add extra combos not in cartesian product.
    include: [f16/q8_0, turbo4/turbo2]

    # Optional: remove combos from the set.
    exclude: [q4_0/q4_0]

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

from pydantic import BaseModel, Field, field_validator
from typing import NamedTuple
import yaml

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

# turboquant's auto-asymmetric feature silently upgrades the K cache on high-GQA
# models, e.g.:
#   W llama_kv_cache: auto-asymmetric: GQA ratio 8:1 (...) — upgrading K from
#   turbo4 to q8_0 to prevent quality degradation. Disable with TURBO_AUTO_ASYMMETRIC=0
# When this fires, the requested -ctk is a lie: the run does not measure what was
# asked for, so the operator must be warned loudly.
AUTO_ASYM_RE = re.compile(r"auto-asymmetric:.*?upgrading K from (\S+) to (\S+)")


def print_auto_asymmetric_warning(label: str, from_q: str, to_q: str) -> None:
    """Print a prominent banner when turboquant silently upgrades the K cache.

    The requested ``-ctk {from_q}`` was overridden to ``{to_q}``, so the run does
    not measure the requested config (and uses a larger cache than its label
    implies).  Tell the operator how to disable the feature.
    """
    bar = "=" * 79
    sys.stdout.write(
        f"\n{bar}\n"
        f"!!! TURBOQUANT AUTO-ASYMMETRIC: {label} is NOT measuring what you asked !!!\n"
        f"{bar}\n"
        f"The K cache was silently upgraded from {from_q} to {to_q} (high-GQA model),\n"
        f"so this run is really -ctk {to_q} (not {from_q}), at a LARGER cache size than\n"
        f"the label implies.  Its KLD will match the {to_q}/<ctv> run, not a true\n"
        f"symmetric {from_q} K cache.\n"
        f"\n"
        f"To measure the TRUE config, disable the feature via the env var:\n"
        f"    TURBO_AUTO_ASYMMETRIC=0 pixi run kv-perplexity -c <your-config.yaml>\n"
        f"{bar}\n\n"
    )
    sys.stdout.flush()


class KVQuant(NamedTuple):
    k: str
    v: str

    def __str__(self):
        return f"{self.k}/{self.v}"


class KVPConfig(BaseModel):
    """Config schema for KV perplexity benchmark."""

    common: str = Field(description="llama-perplexity command prefix")
    baseline: KVQuant = Field(
        description="Baseline combo (k_quant/v_quant) that creates logits.dat"
    )
    k_quants: list[str] = Field(description="Key cache quantizations")
    v_quants: list[str] = Field(
        description="Value cache quantizations (cartesian product with k_quants)"
    )
    include: list[KVQuant] = Field(
        default=[], description="Extra combos beyond cartesian product"
    )
    exclude: list[KVQuant] = Field(
        default=set(), description="Combos to remove from the set"
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

    @field_validator("baseline", mode="before")
    @classmethod
    def parse_baseline(cls, v: object) -> object:
        """Parse "k/v" strings into (k, v) tuples before validation."""
        return cls._parse_kv_quant(v)

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def parse_include_exclude(cls, v: object) -> object:
        """Parse "k/v" strings into (k, v) tuples before validation."""
        if v is None:
            return []
        if isinstance(v, list):
            return [cls._parse_kv_quant(x) for x in v]
        raise ValueError("Expected list of k/v strings; got {v}")

    @staticmethod
    def _parse_kv_quant(q: object) -> KVQuant:
        if isinstance(q, str):
            try:
                k, v = q.split("/", 1)
                return KVQuant(k, v)
            except ValueError:
                pass
        raise ValueError(
            f"Invalid KV quantization: {q!r} (expected format: k_quant/v_quant)"
        )

    @property
    def quants(self) -> list[KVQuant]:
        """Build set of KV combos: cartesian product + include - exclude."""
        # Insertion-ordered set
        quants = {self.baseline: None}
        for k, v in itertools.product(self.k_quants, self.v_quants):
            quants[KVQuant(k, v)] = None
        quants.update(dict.fromkeys(self.include))
        exclude = set(self.exclude)
        quants = {k: None for k in quants if k not in exclude}
        return list(quants)


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
    logfile: pathlib.Path, kq: str, vq: str
) -> tuple[float, str] | None:
    """Search log file for the baseline ETA line.

    The baseline section has ``-ctk <kq> -ctv <vq>`` but no ``--kl-divergence``.
    Returns ``(eta_minutes, raw_line)`` or None if not found.
    """
    text = logfile.read_text()
    marker = f"-ctk {kq} -ctv {vq}"
    lines = text.split("\n")

    capturing = False
    for line in lines:
        if not capturing and marker in line and "--kl-divergence" not in line.split():
            capturing = True
            continue
        if capturing:
            stripped = line.strip()
            if stripped == "------------------------------" or stripped.startswith("---"):
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
    auto_asym_warned = False

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

            if not auto_asym_warned:
                am = AUTO_ASYM_RE.search(line)
                if am:
                    auto_asym_warned = True
                    print_auto_asymmetric_warning(label, am.group(1), am.group(2))

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

    print(f"Baseline: -ctk {cfg.baseline[0]} -ctv {cfg.baseline[1]}")
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

    kq_base, vq_base = cfg.baseline
    kv_args_base = f"-ctk {kq_base} -ctv {vq_base}"
    cmd_base = f"{cfg.common} {kv_args_base}"

    if LOGITS.exists():
        # Determine if baseline is also completed in log
        log_completed = _has_completed_run(
            logfile.read_text(), kv_args_base
        ) if logfile.exists() else False
        log_hint = f", completed in {logfile}" if log_completed else ""
        print(
            f"[SKIP] baseline {kv_args_base} "
            f"({LOGITS} ({logits_size()}) exists{log_hint})"
        )
        baseline_result = find_baseline_eta_in_log(logfile, kq_base, vq_base)
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
    for kq, vq in cfg.quants:
        if (kq, vq) == cfg.baseline:
            continue

        kv_args = f"-ctk {kq} -ctv {vq}"
        cmd = f"{cfg.common} {kv_args} --kl-divergence"

        # Skip if already present in log (any outcome: completed, aborted, or interrupted)
        if logfile.exists() and kv_args in logfile.read_text():
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
