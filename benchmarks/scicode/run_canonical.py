#!/usr/bin/env python3
"""SciCode canonical runner (design doc 01) — arms L1 / L2 via inspect_ai.

Builds the SciCode inspect_ai task IN-PROCESS (importlib-loading the upstream
eval/inspect_ai/scicode.py under a non-colliding name so its `from scicode...`
resolves to the installed package), filters the dataset to
pins/scicode-subset.txt (erroring on pin/dataset drift — `--limit` alone would
take the first N, not the stratified pin), runs inspect_ai.eval, then records
the subproblem pass rate + per-subproblem timing to a ledger entry + REPORT.

Smoke: `--mode dummy` calls no LLM (model=mockllm; still scores via test_data.h5)
— proves harness plumbing. Run inside the SciCode venv (setup.sh):
  .venv/bin/python run_canonical.py --arm L2
  .venv/bin/python run_canonical.py --mode dummy --limit 1
"""

import argparse
import importlib.util
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CLONE = HERE / "SciCode"
INSPECT_CWD = CLONE / "eval" / "inspect_ai"
H5 = CLONE / "eval" / "data" / "test_data.h5"
OUT = HERE / "out"
PIN_FILE = "pins/scicode-subset.txt"
HARNESS_SHA = "e3158ea011d4235245a547460d3688d7ccbf9900"

sys.path.insert(0, str(HERE.parent / "lib"))
from benchlib import endpoint, ledger, pins, report, timing  # noqa: E402


def _pin_path():
    from benchlib import PINS_DIR

    return PINS_DIR / "scicode-subset.txt"


def load_upstream():
    """Load eval/inspect_ai/scicode.py under a non-'scicode' name so its
    `from scicode.parse import ...` resolves to the INSTALLED package, and chdir
    into it so its module-level `../data` template reads succeed. INSPECT_CWD is
    deliberately NOT added to sys.path (that would shadow the scicode package)."""
    os.chdir(INSPECT_CWD)
    # Strip cwd-ish entries so INSPECT_CWD/scicode.py cannot shadow the installed
    # `scicode` package (e.g. when launched via `python -c`, where '' == cwd).
    sys.path[:] = [p for p in sys.path if p not in ("", ".", str(INSPECT_CWD))]
    spec = importlib.util.spec_from_file_location(
        "upstream_scicode", INSPECT_CWD / "scicode.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_task(US, ids, mode, with_background, h5file):
    from inspect_ai import Task
    from inspect_ai.dataset import hf_dataset

    dataset = hf_dataset(
        "SciCode1/SciCode", split="test", sample_fields=US.record_to_sample
    )
    if ids:
        present = {str(s.id) for s in dataset}
        missing = [i for i in ids if i not in present]
        if missing:
            raise ValueError(
                f"pinned problem IDs absent from test split: {missing} (pin/dataset drift)"
            )
        wanted = set(ids)
        dataset = dataset.filter(lambda s: str(s.id) in wanted)
    return Task(
        dataset=dataset,
        solver=US.scicode_solver(
            output_dir=str(OUT / "tmp"), with_background=with_background, mode=mode
        ),
        scorer=US.scicode_scorer(
            output_dir=str(OUT / "tmp"),
            with_background=with_background,
            h5py_file=str(h5file),
        ),
    )


def extract(log):
    """(subproblem_pass_rate, [per-sample seconds]) from an inspect EvalLog."""
    subrate = None
    if log.results and log.results.scores:
        for sc in log.results.scores:
            for name, m in (sc.metrics or {}).items():
                if "sub_problem" in name or "subproblem" in name:
                    subrate = m.value
        if subrate is None:
            last = log.results.scores[-1]
            if last.metrics:
                subrate = next(iter(last.metrics.values())).value
    secs = []
    for s in log.samples or []:
        t = getattr(s, "total_time", None) or getattr(s, "working_time", None)
        if t:
            secs.append(float(t))
    return subrate, secs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["L1", "L2"], default="L2")
    ap.add_argument(
        "--limit", type=int, default=None, help="first-N pinned problems (smoke)"
    )
    ap.add_argument("--mode", choices=["normal", "dummy", "gold"], default="normal")
    ap.add_argument(
        "--with-background", type=lambda x: x.lower() != "false", default=True
    )
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument(
        "--max-connections",
        type=int,
        default=4,
        help="=> llama-server --parallel slots",
    )
    args = ap.parse_args()

    e = endpoint.env()
    arm = "L1-remote-canonical" if args.arm == "L1" else "L2-local-canonical"
    if args.arm == "L1" and endpoint.deployment() != "remote":
        sys.exit("L1 selected but OPENAI_BASE_URL is not a remote endpoint (doc 00).")
    if args.mode == "normal" and not e["model"]:
        sys.exit("BENCH_MODEL is unset (doc 05 D1).")
    if not H5.exists():
        sys.exit(f"{H5} missing — run setup.sh to fetch test_data.h5 (~1 GB).")

    ids = [str(i) for i in pins.scicode_ids(_pin_path())]
    if args.limit:
        ids = ids[: args.limit]

    # inspect's openai provider reads these; harmless for dummy (mockllm).
    os.environ["OPENAI_BASE_URL"] = e["base_url"]
    os.environ["OPENAI_API_KEY"] = e["api_key"]
    model = "mockllm/model" if args.mode == "dummy" else f"openai/{e['model']}"
    print(f"SciCode {arm} ({args.mode}): {len(ids)} pinned problems, model={model}")

    import inspect_ai

    US = load_upstream()
    task = build_task(US, ids, args.mode, args.with_background, H5)
    log_dir = OUT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    logs = inspect_ai.eval(
        task,
        model=model,
        temperature=args.temperature,
        max_connections=args.max_connections,
        log_dir=str(log_dir),
        display="plain",
    )
    wall_h = (time.perf_counter() - t0) / 3600
    subrate, secs = extract(logs[0])
    val = round(subrate, 4) if subrate is not None else 0.0
    tmg = (
        timing.summarize(secs)
        if secs
        else {"n": len(ids), "mean_s": 0.0, "median_s": 0.0, "min_s": 0.0, "max_s": 0.0}
    )

    run_id = ledger.make_run_id("scicode", arm)
    ledger.append(
        ledger.build_entry(
            run_id=run_id,
            benchmark="scicode",
            arm=arm,
            harness={"name": "inspect_ai", "version": HARNESS_SHA},
            model=ledger.model_dict(
                endpoint.deployment(), e["base_url"], endpoint.precision(), e["model"]
            ),
            toggles=ledger.toggles_from_env(),
            pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), len(ids)),
            repeats=1,
            score=ledger.score_dict("subproblem_pass@1", val, str(logs[0].location)),
            timing=tmg,
            wall_clock_h=round(wall_h, 3),
            notes=(
                f"mode={args.mode}; with_background={args.with_background}; inspect_ai canonical; "
                "AA reference protocol (subproblem scoring, with background)"
            ),
        )
    )
    line = report.one_liner(
        e["base_url"], e["model"], val, tmg["mean_s"], tmg["n"], unit="subproblem"
    )
    report.write(
        run_id,
        f"SciCode {arm} — canonical (inspect_ai)",
        [line],
        body=(
            f"- subproblem pass@1 = {val} (n={len(ids)} pinned problems, mode={args.mode})\n"
            f"- inspect log: {logs[0].location}\n\n"
            "Compare to the Artificial Analysis SciCode number for this model "
            "(subproblem scoring, with background). Protocol differences: our pinned "
            "subset vs AA's full 65-problem set; local quant + q8_0 KV vs the L1 "
            "precision label. No numeric gate (decision Q8)."
        ),
    )
    print(f"\n{line}")
    print(f"REPORT + ledger written for {run_id}")


if __name__ == "__main__":
    main()
