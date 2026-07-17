#!/usr/bin/env python3
"""Minimal validator for docs/benchmarks/results/runs.jsonl ledger entries.
Usage: python check_ledger.py runs.jsonl
       python check_ledger.py --self-test   # validate a bundled example
Exits non-zero on the first malformed line. No deps beyond stdlib."""

import json
import sys

# One fully-formed entry exercising every required field incl. timing.
# Used by --self-test so the doc-00 acceptance criterion ("a dummy ledger entry
# validates against the schema") is runnable without a real runs.jsonl.
EXAMPLE = {
    "run_id": "2026-06-17T10-00_scicode_L2",
    "benchmark": "scicode",
    "arm": "L2-local-canonical",
    "harness": {"name": "inspect_ai", "version": "1.0"},
    "model": {
        "deployment": "local",
        "endpoint": "http://localhost:8080/v1",
        "precision": "IQ4_XS+q8_0kv",
        "preset": "Qwen3.6-35B-A3B",
        "config": "n-cpu-moe=20; ctx-size=131072; froggeric template off; reasoning-budget uncapped",
    },
    "toggles": {
        "T1_froggeric": False,
        "T2_reasoning_budget": False,
        "T3_kv": "q8_0",
        "parallel_slots": 4,
    },
    "pin": {"file": "pins/scicode-subset.txt", "sha256": "deadbeef", "n_items": 30},
    "repeats": 1,
    "score": {"metric": "subproblem_pass@1", "value": 0.42, "raw": "scicode-L2/out"},
    "timing": {
        "n": 130,
        "mean_s": 33.0,
        "median_s": 28.0,
        "min_s": 5.0,
        "max_s": 180.0,
    },
    "wall_clock_h": 1.2,
    "tokens": {"in": 1_200_000, "out": 340_000},
    "notes": "calibration probe: mean 33s/subproblem at --parallel 4; within budget.",
}

REQUIRED = {
    "run_id": str,
    "benchmark": str,
    "arm": str,
    "harness": dict,
    "model": dict,
    "toggles": dict,
    "pin": dict,
    "repeats": int,
    "score": dict,
    "timing": dict,
    "wall_clock_h": (int, float),
    "tokens": dict,
    "notes": str,
}
BENCHMARKS = {"scicode", "tb2", "livecodebench", "ifbench", "evalplus", "cruxeval"}
ARMS = {
    "L1-remote-canonical",
    "L2-local-canonical",
    "L3-local-pi-bare",
    "L4-local-pi-tools",
    "L5-local-pi-ext",
    "L6-local-pi-advisor",
}


def fail(ln, msg):
    print(f"line {ln}: {msg}", file=sys.stderr)
    sys.exit(1)


def validate_obj(o, ln):
    for k, t in REQUIRED.items():
        if k not in o:
            fail(ln, f"missing field {k!r}")
        if not isinstance(o[k], t):
            fail(ln, f"field {k!r} should be {t}, got {type(o[k]).__name__}")
    if o["benchmark"] not in BENCHMARKS:
        fail(ln, f"benchmark must be one of {BENCHMARKS}")
    if o["arm"] not in ARMS:
        fail(ln, f"arm must be one of {ARMS}")
    for k in ("deployment", "endpoint", "precision", "preset", "config"):
        if k not in o["model"]:
            fail(ln, f"model missing {k!r}")
        if not isinstance(o["model"][k], str):
            fail(ln, f"model.{k} should be a string, got {type(o['model'][k]).__name__}")
    for k in ("metric", "value"):
        if k not in o["score"]:
            fail(ln, f"score missing {k!r}")
    for k in ("n", "mean_s", "median_s", "min_s", "max_s"):
        if k not in o["timing"]:
            fail(ln, f"timing missing {k!r}")
        if not isinstance(o["timing"][k], (int, float)):
            fail(
                ln,
                f"timing.{k} should be a number, got {type(o['timing'][k]).__name__}",
            )


def main(path):
    n = 0
    for ln, raw in enumerate(open(path), 1):
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        n += 1
        try:
            o = json.loads(raw)
        except json.JSONDecodeError as e:
            fail(ln, f"invalid JSON: {e}")
        validate_obj(o, ln)
    print(f"OK: {n} ledger entry/entries valid")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        validate_obj(EXAMPLE, 1)
        print("OK: self-test example valid")
        sys.exit(0)
    main(sys.argv[1] if len(sys.argv) > 1 else "runs.jsonl")
