"""Build + append ledger entries to docs/benchmarks/results/runs.jsonl (doc 00).

Every scored run appends exactly one JSON object. Entries are validated against
the repo's own ``results/check_ledger.py`` before being written, so a runner
that assembles a malformed entry crashes loudly (doc 00) instead of polluting
the ledger.
"""

import datetime
import importlib.util
import json
import os

from . import LEDGER_PATH, RESULTS_DIR

_checker_cache = None


def _checker():
    global _checker_cache
    if _checker_cache is None:
        spec = importlib.util.spec_from_file_location(
            "check_ledger", RESULTS_DIR / "check_ledger.py"
        )
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _checker_cache = m
    return _checker_cache


ARM_SHORT = {
    "L1-remote-canonical": "L1",
    "L2-local-canonical": "L2",
    "L3-local-pi-bare": "L3",
    "L4-local-pi-tools": "L4",
    "L5-local-pi-ext": "L5",
    "L6-local-pi-advisor": "L6",
}

# Doc-00 default toggles for a clean baseline (T1/T2 off, global q8_0 KV).
DEFAULT_TOGGLES = {
    "T1_froggeric": False,
    "T2_reasoning_budget": False,
    "T3_kv": "q8_0",
    "parallel_slots": 1,
}


def toggles_from_env() -> dict:
    """Read the doc-00 named confounds from env (the runner can't see models.ini).

    The operator sets these to match the served preset:
      BENCH_T1_FROGGERIC   truthy => custom froggeric chat template on (default off)
      BENCH_T2_REASONING_BUDGET  int => capped at N thinking tokens; truthy => on;
                                 unset/falsey => uncapped (default off)
      BENCH_T3_KV          KV cache quant label (default 'q8_0')
      BENCH_PARALLEL_SLOTS llama-server --parallel N (default 1)
    """

    def _truthy(v: str) -> bool:
        return v.strip().lower() in ("1", "true", "yes", "on")

    t2_raw = os.environ.get("BENCH_T2_REASONING_BUDGET")
    if t2_raw is None or t2_raw.strip().lower() in ("", "0", "false", "no", "off"):
        t2: bool | int = False
    elif t2_raw.strip().isdigit():
        t2 = int(t2_raw)
    else:
        t2 = True

    t1_raw = os.environ.get("BENCH_T1_FROGGERIC", "")
    return {
        "T1_froggeric": _truthy(t1_raw),
        "T2_reasoning_budget": t2,
        "T3_kv": os.environ.get("BENCH_T3_KV", "q8_0"),
        "parallel_slots": int(os.environ.get("BENCH_PARALLEL_SLOTS", "1")),
    }


def make_run_id(benchmark: str, arm: str, when=None) -> str:
    when = when or datetime.datetime.now()
    return f"{when:%Y-%m-%dT%H-%M}_{benchmark}_{ARM_SHORT.get(arm, arm)}"


def model_dict(deployment, endpoint, precision, preset, config="") -> dict:
    return {
        "deployment": deployment,
        "endpoint": endpoint,
        "precision": precision,
        "preset": preset,
        "config": config,
    }


def score_dict(metric, value, raw="") -> dict:
    return {"metric": metric, "value": value, "raw": raw}


def pin_dict(file, sha256, n_items) -> dict:
    return {"file": file, "sha256": sha256, "n_items": n_items}


def build_entry(
    *,
    run_id,
    benchmark,
    arm,
    harness,
    model,
    score,
    timing,
    pin=None,
    toggles=None,
    repeats=1,
    wall_clock_h=0.0,
    tokens=None,
    notes="",
) -> dict:
    return {
        "run_id": run_id,
        "benchmark": benchmark,
        "arm": arm,
        "harness": harness,
        "model": model,
        "toggles": dict(DEFAULT_TOGGLES, **(toggles or {})),
        "pin": pin or pin_dict("", "", timing.get("n", 0)),
        "repeats": repeats,
        "score": score,
        "timing": timing,
        "wall_clock_h": wall_clock_h,
        "tokens": tokens or {"in": 0, "out": 0},
        "notes": notes,
    }


def validate(entry: dict) -> None:
    """Crash (exit 1) if the entry violates the doc-00 schema (check_ledger.py)."""
    _checker().validate_obj(entry, 0)


def append(entry: dict, path=None):
    """Validate then append one JSON line to the ledger; returns the path."""
    validate(entry)
    p = path or LEDGER_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return p
