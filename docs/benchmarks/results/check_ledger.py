#!/usr/bin/env python3
"""Minimal validator for docs/benchmarks/results/runs.jsonl ledger entries.
Usage: python check_ledger.py runs.jsonl
Exits non-zero on the first malformed line. No deps beyond stdlib."""
import json, sys

REQUIRED = {
    "run_id": str, "benchmark": str, "arm": str, "harness": dict,
    "model": dict, "toggles": dict, "pin": dict, "repeats": int,
    "score": dict, "wall_clock_h": (int, float), "tokens": dict, "notes": str,
}
BENCHMARKS = {"scicode", "tb2",
              "livecodebench", "ifbench", "evalplus", "cruxeval"}
ARMS = {"L1-remote-canonical", "L2-local-canonical", "L3-local-pi-bare",
        "L4-local-pi-tools", "L5-local-pi-ext", "L6-local-pi-advisor"}

def fail(ln, msg):
    print(f"line {ln}: {msg}", file=sys.stderr); sys.exit(1)

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
        for k, t in REQUIRED.items():
            if k not in o:
                fail(ln, f"missing field {k!r}")
            if not isinstance(o[k], t):
                fail(ln, f"field {k!r} should be {t}, got {type(o[k]).__name__}")
        if o["benchmark"] not in BENCHMARKS:
            fail(ln, f"benchmark must be one of {BENCHMARKS}")
        if o["arm"] not in ARMS:
            fail(ln, f"arm must be one of {ARMS}")
        for k in ("metric", "value"):
            if k not in o["score"]:
                fail(ln, f"score missing {k!r}")
    print(f"OK: {n} ledger entry/entries valid")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "runs.jsonl")
