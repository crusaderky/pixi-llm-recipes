#!/usr/bin/env python
"""Aggregate context-bench results across reruns into CSV.

Usage:
    python aggregate_benchmark_results.py -o aggregated.csv context-bench.results.*.toml
"""
import argparse
import csv
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

FILENAME_RE = re.compile(
    r"^context-bench\.results\.(?P<label>[^.]+)\.(?P<rerun>\d+)\.toml$"
)
N_QUESTIONS = 20


def postprocess_outcomes(outcomes: list[str]) -> list[str]:
    """Replace empty ``outcomes`` list with ``MODEL COLLAPSE * 20``."""
    if not outcomes:
        return ["MODEL COLLAPSE"] * N_QUESTIONS
    return outcomes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Result TOML files (context-bench.results.<label>.<rerun>.toml)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output CSV path",
    )
    args = parser.parse_args(argv)

    # ------------------------------------------------------------------ #
    # 1) Validate filenames + group by label
    # ------------------------------------------------------------------ #
    label_files: dict[str, list[Path]] = {}
    for fpath in args.files:
        m = FILENAME_RE.match(fpath.name)
        if not m:
            raise SystemExit(
                f"Filename {fpath.name!r} does not match "
                f"'context-bench.results.<label>.<rerun>.toml'"
            )
        label = m.group("label").replace("--", "/")
        label_files.setdefault(label, []).append(fpath)

    # ------------------------------------------------------------------ #
    # 2) Aggregate per label
    # ------------------------------------------------------------------ #
    rows: list[tuple[str, str, str, int, float, float, float, float, float, int, int, float]] = []

    for label in sorted(label_files):
        fpaths = sorted(label_files[label], key=lambda p: p.name)

        # Collect (counter, completion_tokens) per (model, context) across all reruns
        buckets: dict[tuple[str, str], list[tuple[Counter[str], int | None]]] = {}

        for fpath in fpaths:
            data = tomllib.loads(fpath.read_text(encoding="utf-8"))

            # In TOML, [Gemma4-E2B.16k] parses as {"Gemma4-E2B": {"16k": {...}}}
            for raw_model, contexts in data.items():
                if not isinstance(contexts, dict):
                    continue
                for raw_context, row in contexts.items():
                    if not isinstance(row, dict):
                        continue

                    outcomes = row.get("outcomes")
                    if not isinstance(outcomes, list):
                        continue

                    outcomes_pp = postprocess_outcomes(outcomes)
                    counter = Counter(outcomes_pp)
                    completion = row.get("completion_tokens")
                    if not isinstance(completion, int):
                        completion = None
                    key = (raw_model, raw_context)
                    buckets.setdefault(key, []).append((counter, completion))

        # Aggregate each bucket
        for (model, context), entries in buckets.items():
            total_counter: Counter[str] = Counter()
            n = len(entries)
            comp_vals: list[int] = []

            for counter, comp in entries:
                total_counter += counter
                if comp is not None:
                    comp_vals.append(comp)

            total_cnt = sum(total_counter.values())
            coll = total_counter.get("MODEL COLLAPSE", 0)
            valid_cnt = total_cnt - coll

            if total_cnt > 0:
                coll_pct = round(coll / total_cnt * 100, 2)
            else:
                coll_pct = 0.0

            if valid_cnt > 0:
                lo = {k.lower(): v for k, v in total_counter.items() if k.lower() != "model collapse"}
                pass_ = lo.get("pass", 0)
                wrong = lo.get("wrong", 0)
                no_ans = lo.get("no answer", 0)
                pass_pct = round(pass_ / valid_cnt * 100, 2)
                wrong_pct = round(wrong / valid_cnt * 100, 2)
                no_ans_pct = round(no_ans / valid_cnt * 100, 2)
                grade = round((pass_ - wrong) / valid_cnt * 100, 2)
            else:
                pass_pct = wrong_pct = no_ans_pct = grade = 0.0

            comp_mean = round(sum(comp_vals) / len(comp_vals)) if comp_vals else 0
            comp_std = round(
                (sum((c - comp_mean) ** 2 for c in comp_vals) / len(comp_vals)) ** 0.5
            ) if comp_vals else 0
            exceeded_budget = round(sum(1 for c in comp_vals if c > 8192) / len(comp_vals) * 100, 2) if comp_vals else 0.0

            rows.append((
                label,
                model,
                context,
                n,
                grade,
                pass_pct,
                no_ans_pct,
                wrong_pct,
                coll_pct,
                comp_mean,
                comp_std,
                exceeded_budget,
            ))

    # ------------------------------------------------------------------ #
    # 3) Write CSV
    # ------------------------------------------------------------------ #
    with args.output.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "model", "context", "runs_count", "grade", "pass", "no answer", "wrong", "model collapse", "completion_tokens_mean", "completion_tokens_std", "exceeded_reasoning_budget"])
        w.writerows(rows)

    print(f"Wrote {args.output} ({len(rows)} rows)", file=sys.stderr)


if __name__ == "__main__":
    main()
