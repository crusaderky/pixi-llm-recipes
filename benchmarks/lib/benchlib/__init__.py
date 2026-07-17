"""benchlib — shared helpers for the benchmark panel (docs/benchmarks/00–09).

Runners live in benchmarks/<benchmark>/ and run inside *isolated* harness venvs
(doc 05 §Harness isolation), so benchlib is imported by path rather than
installed. Each runner starts with:

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
    from benchlib import ledger, report, pins, timing, endpoint, cutoff, sandbox

benchlib depends only on the standard library; the one third-party import
(``openai``) is lazy and confined to :mod:`benchlib.endpoint`, so importing
:mod:`benchlib.ledger`/``pins``/``timing``/``report`` never requires it.

All shared *state* (pins, ledger, cutoff table) continues to live under
``docs/benchmarks/`` exactly where docs 00–09 reference it; only the executable
harness code and the exec sandbox live under ``benchmarks/``.
"""

import pathlib

# benchmarks/lib/benchlib/__init__.py -> parents[3] is the repo root.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
BENCH_ROOT = REPO_ROOT / "benchmarks"
DOCS_BENCH = REPO_ROOT / "docs" / "benchmarks"

PINS_DIR = DOCS_BENCH / "pins"
RESULTS_DIR = DOCS_BENCH / "results"
LEDGER_PATH = RESULTS_DIR / "runs.jsonl"
CUTOFF_LOOKUP = DOCS_BENCH / "lookup_cutoff.py"
CUTOFF_TABLE = DOCS_BENCH / "model-cutoffs.toml"

SANDBOX_SH = BENCH_ROOT / "scripts" / "bench-sandbox.sh"

__all__ = [
    "REPO_ROOT",
    "BENCH_ROOT",
    "DOCS_BENCH",
    "PINS_DIR",
    "RESULTS_DIR",
    "LEDGER_PATH",
    "CUTOFF_LOOKUP",
    "CUTOFF_TABLE",
    "SANDBOX_SH",
]
