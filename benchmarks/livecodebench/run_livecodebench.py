#!/usr/bin/env python3
"""LiveCodeBench runner (design doc 06) — arms L1-remote-canonical / L2-local-canonical.

The panel's PRIMARY quant-delta signal: contamination is controlled by selecting
contest problems whose date is AFTER the model's training cutoff. Scenario:
code_generation only. Metric: pass@1.

Reuses LiveCodeBench's canonical dataset + prompt + extraction + grading
(imported from the pinned clone) and drives generation via the OpenAI endpoint.
Grading (codegen_metrics executes untrusted solutions) runs INSIDE
benchmarks/scripts/bench-sandbox.sh (doc 05 D2): no network, resource-capped.

Cutoff pre-check is mandatory (doc 06): the window START must be after the model
cutoff from docs/benchmarks/lookup_cutoff.py, else the contamination posture is
void. Run inside the LCB venv (setup.sh):
  .venv/bin/python run_livecodebench.py --arm L2
  .venv/bin/python run_livecodebench.py --smoke-grade     # offline grading-path check
"""

import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CLONE = HERE / "LiveCodeBench"
VENV_PY = str(HERE / ".venv" / "bin" / "python")
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(CLONE))
os.chdir(CLONE)  # lcb_runner.prompts reads few-shot examples by a cwd-relative path

from benchlib import cutoff, endpoint, ledger, pins, report, sandbox, timing  # noqa: E402

PIN_FILE = "pins/livecodebench-window.txt"
HARNESS_SHA = "28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24"
OUT = HERE / "out"

# Sandboxed grader: import LCB's canonical codegen_metrics, grade staged
# samples/generations, write pass@1. No network; runs the untrusted solutions.
GRADER = f"""
import sys, json
sys.path.insert(0, {str(CLONE)!r})
from lcb_runner.evaluation import codegen_metrics
es = json.load(open("/tmp/eval_samples.json"))
gens = json.load(open("/tmp/generations.json"))
metrics = codegen_metrics(es, gens, num_process_evaluate=4, timeout=6)
json.dump({{"pass@1": metrics[0].get("pass@1")}}, open("/tmp/metrics.json", "w"))
"""


def _pin_path():
    from benchlib import PINS_DIR

    return PINS_DIR / "livecodebench-window.txt"


def load_window(release_version, start_date, end_date, limit=None):
    from lcb_runner.benchmarks import load_code_generation_dataset

    ds = load_code_generation_dataset(
        release_version=release_version, start_date=start_date, end_date=end_date
    )
    return ds[:limit] if limit else ds


def generate(problems, e, temperature, top_p, max_tokens, no_think):
    from lcb_runner.lm_styles import LMStyle
    from lcb_runner.prompts import format_prompt_generation
    from lcb_runner.utils.extraction_utils import extract_code

    client = endpoint.client()
    codes, secs, tin, tout = [], [], 0, 0
    for i, p in enumerate(problems, 1):
        prompt = format_prompt_generation(p, LMStyle.OpenAIChat)
        messages = (
            prompt
            if isinstance(prompt, list)
            else [{"role": "user", "content": prompt}]
        )
        t0 = time.perf_counter()
        try:
            content, (pin_t, pout_t) = endpoint.chat_once(
                client,
                e["model"],
                messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                no_think=no_think,
            )
            tin += pin_t
            tout += pout_t
            code = extract_code(content, LMStyle.OpenAIChat)
        except Exception as exc:  # noqa: BLE001
            code = ""
            print(f"  [{i}/{len(problems)}] gen error: {exc}", file=sys.stderr)
        secs.append(time.perf_counter() - t0)
        codes.append(code)
        if i % 20 == 0:
            print(f"  generated {i}/{len(problems)}", file=sys.stderr)
    return codes, secs, {"in": tin, "out": tout}


def grade(problems, codes):
    """pass@1 via LCB's codegen_metrics, executed in the sandbox. One invocation."""
    eval_samples = [p.get_evaluation_sample() for p in problems]
    generations = [[c] for c in codes]
    OUT.mkdir(parents=True, exist_ok=True)
    stage = OUT / "grade"
    stage.mkdir(parents=True, exist_ok=True)
    json.dump(eval_samples, open(stage / "eval_samples.json", "w"))
    json.dump(generations, open(stage / "generations.json", "w"))
    (stage / "grader.py").write_text(GRADER)
    cp = sandbox.run(
        [VENV_PY, "/tmp/grader.py"],
        stage=str(stage),
        time=3600,
        capture_output=True,
        text=True,
    )
    mp = stage / "metrics.json"
    if not mp.exists():
        print(cp.stdout[-1500:], cp.stderr[-1500:], file=sys.stderr)
        raise RuntimeError("sandboxed codegen_metrics produced no output")
    return json.load(open(mp))["pass@1"]


def smoke_grade():
    """Offline: grade a wrong generation for 2 (pre-cutoff release_v1) problems in
    the sandbox — proves the LCB grader + sandbox plumbing (expects pass@1==0.0)."""
    problems = load_window("release_v1", None, None, limit=2)
    codes = ["def wrong():\n    return None\n"] * len(problems)
    p1 = grade(problems, codes)
    status = "OK" if p1 == 0.0 else f"UNEXPECTED ({p1})"
    print(
        f"SMOKE-GRADE: {len(problems)} problems x wrong solution -> pass@1={p1}  [{status}]"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["L1", "L2"], default="L2")
    ap.add_argument("--limit", type=int, default=None, help="first-N smoke cap")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--max-tokens", type=int, default=12000)
    ap.add_argument(
        "--no-think", action="store_true", help="fast smoke: disable reasoning"
    )
    ap.add_argument(
        "--smoke-grade",
        action="store_true",
        help="offline grading-path check (no model)",
    )
    args = ap.parse_args()

    if args.smoke_grade:
        smoke_grade()
        return

    e = endpoint.env()
    arm = "L1-remote-canonical" if args.arm == "L1" else "L2-local-canonical"
    if args.arm == "L1" and endpoint.deployment() != "remote":
        sys.exit("L1 selected but OPENAI_BASE_URL is not a remote endpoint (doc 00).")
    if not e["model"]:
        sys.exit("BENCH_MODEL is unset (doc 05 D1).")

    # Mandatory cutoff pre-check (doc 06).
    entry = cutoff.resolve(e["model"])
    void = cutoff.is_posture_void(entry)
    model_cutoff = str(entry["cutoff"])
    kv = pins.kv(_pin_path())
    release_version = kv.get("release_version", "release_latest")
    start_date = kv.get("start_date") or (None if void else model_cutoff)
    end_date = kv.get("end_date")
    if void:
        print(
            "WARNING: model cutoff is 'unknown' — contamination posture is VOID.",
            file=sys.stderr,
        )
    elif start_date and start_date < model_cutoff:
        sys.exit(
            f"pin start_date {start_date} is BEFORE model cutoff {model_cutoff} — posture void (doc 06)."
        )

    problems = load_window(release_version, start_date, end_date, limit=args.limit)
    n = len(problems)
    if n == 0:
        sys.exit(
            f"window ({release_version}, start={start_date}, end={end_date}) resolved to 0 problems."
        )
    print(
        f"LiveCodeBench {arm}: {n} problems (release={release_version}, start={start_date}, end={end_date}) "
        f"x {args.repeats} repeat(s) via {e['base_url']} / {e['model']}"
    )

    all_secs, tok, passes = [], {"in": 0, "out": 0}, []
    for _ in range(args.repeats):
        codes, secs, usage = generate(
            problems, e, args.temperature, args.top_p, args.max_tokens, args.no_think
        )
        all_secs += secs
        tok["in"] += usage["in"]
        tok["out"] += usage["out"]
        passes.append(grade(problems, codes))
    passk = round(sum(passes) / len(passes), 4)
    tmg = timing.summarize(all_secs)

    run_id = ledger.make_run_id("livecodebench", arm)
    toggles = ledger.toggles_from_env()
    if args.no_think:
        toggles["no_think"] = True
    ledger.append(
        ledger.build_entry(
            run_id=run_id,
            benchmark="livecodebench",
            arm=arm,
            harness={"name": "livecodebench", "version": HARNESS_SHA},
            model=ledger.model_dict(
                endpoint.deployment(), e["base_url"], endpoint.precision(), e["model"]
            ),
            toggles=toggles,
            pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), n),
            repeats=args.repeats,
            score=ledger.score_dict(
                "pass@1", passk, json.dumps({"release": release_version, "n": n})
            ),
            timing=tmg,
            tokens=tok,
            notes=(
                f"scenario=code_generation; window release={release_version} start={start_date} end={end_date}; "
                f"model cutoff={model_cutoff} ({entry['confidence']}); contamination_posture={'VOID' if void else 'ok'}"
            ),
        )
    )
    line = report.one_liner(
        e["base_url"], e["model"], passk, tmg["mean_s"], tmg["n"], unit="problem"
    )
    report.write(
        run_id,
        f"LiveCodeBench {arm} — code_generation (headline quant signal)",
        [line],
        body=(
            f"- pass@1={passk}  (n={n}, {args.repeats} repeat(s))\n"
            f"- window: release={release_version}, start={start_date}, end={end_date}\n"
            f"- model cutoff: {model_cutoff} ({entry['confidence']}); "
            f"contamination posture: {'VOID (cutoff unknown)' if void else 'ok (window is post-cutoff)'}\n"
            f"- tokens: in={tok['in']} out={tok['out']}\n\n"
            "Because the window is post-cutoff, a large L1->L2 drop is a CLEAN read "
            "on local-stack damage (4-bit weights + q8_0 KV) — the headline result "
            "this benchmark exists to produce. Compare to the LiveCodeBench "
            "leaderboard filtered to the same release window; note our sampling is "
            "the deployed temp 0.6, not greedy."
        ),
    )
    print(f"\n{line}")
    print(f"REPORT + ledger written for {run_id}")


if __name__ == "__main__":
    main()
