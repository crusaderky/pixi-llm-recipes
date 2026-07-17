"""Per-item wall-time aggregation for the ledger ``timing`` field (doc 00).

``timing`` is the per-item wall time (seconds) recorded for every graded item
(a SciCode subproblem, a TB task, a LiveCodeBench/EvalPlus/CRUXEval problem, an
IFBench prompt). It is the throughput signal comparable across harnesses,
unlike ``wall_clock_h`` which folds in model load / Docker pull / grading.
"""

import statistics


def summarize(seconds) -> dict:
    """[per-item seconds] -> {n, mean_s, median_s, min_s, max_s} (doc 00 schema)."""
    xs = [float(s) for s in seconds]
    if not xs:
        return {"n": 0, "mean_s": 0.0, "median_s": 0.0, "min_s": 0.0, "max_s": 0.0}
    return {
        "n": len(xs),
        "mean_s": round(statistics.mean(xs), 3),
        "median_s": round(statistics.median(xs), 3),
        "min_s": round(min(xs), 3),
        "max_s": round(max(xs), 3),
    }
