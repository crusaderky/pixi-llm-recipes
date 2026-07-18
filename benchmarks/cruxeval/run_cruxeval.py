#!/usr/bin/env python3
"""CRUXEval runner (design doc 09) — arms L1-remote-canonical / L2-local-canonical.

800 short Python functions, two tasks:
  - output_prediction (-O): given code+input, predict output. Graded by literal
    equality (ast.literal_eval, NO code executed) — no sandbox.
  - input_prediction (-I): given code+output, predict an input. Graded by
    EXECUTING f(predicted_input)==output — untrusted, run through
    benchmarks/scripts/bench-sandbox.sh (doc 05 D2), all items in ONE sandbox call.

Metric pass@1 per task (two ledger entries). Default direct mode (no CoT) to
match the most-cited leaderboard column. Run inside the CRUXEval venv (setup.sh):
  .venv/bin/python run_cruxeval.py --arm L2 --task output_prediction,input_prediction
  .venv/bin/python run_cruxeval.py --selftest       # offline grading-path check
"""

import argparse
import ast
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
CRUX = HERE / "CRUXEval"
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(CRUX))  # prompts.py

from benchlib import endpoint, ledger, pins, report, sandbox, timing  # noqa: E402

PIN_FILE = "pins/cruxeval-full.txt"
HARNESS_SHA = "190faf16d175b5847b0af05d937872b1fb395942"
DATA = CRUX / "data" / "cruxeval.jsonl"
OUT = HERE / "out"

# Grader script for the -I half: reads /tmp/in.jsonl, execs each
# `code; f(pred_input) == output` with a per-item alarm, writes /tmp/out.jsonl.
# Runs inside bench-sandbox.sh (no network, resource-capped).
IGRADER = r"""
import json, signal
def _to(s, f): raise TimeoutError()
signal.signal(signal.SIGALRM, _to)
res = []
for line in open("/tmp/in.jsonl"):
    it = json.loads(line)
    ok = False
    try:
        signal.alarm(3)
        ns = {}
        exec(it["code"], ns)
        exec("__r = (f(" + it["pred_input"] + ") == (" + it["output"] + "))", ns)
        ok = bool(ns.get("__r"))
    except Exception:
        ok = False
    finally:
        signal.alarm(0)
    res.append({"id": it["id"], "ok": ok})
open("/tmp/out.jsonl", "w").write("\n".join(json.dumps(r) for r in res))
"""


def _pin_path():
    from benchlib import PINS_DIR

    return PINS_DIR / "cruxeval-full.txt"


def load_data(limit=None):
    items = [json.loads(x) for x in open(DATA) if x.strip()]
    return items[:limit] if limit else items


def _answer_body(gen: str) -> str:
    """Text between the model's completion start and [/ANSWER] (the prompt ends at
    [ANSWER]\\n, so the model emits the assertion, then [/ANSWER])."""
    g = gen.split("[/ANSWER]")[0]
    g = g.split("[ANSWER]")[-1]
    return g.strip()


def extract_output(gen: str) -> str:
    """-O: predicted output literal (RHS of the last `==`)."""
    body = _answer_body(gen)
    if "==" in body:
        body = body.split("==")[-1]
    return body.strip().rstrip(";").strip()


def extract_input(gen: str) -> str | None:
    """-I: predicted input args, from `f(<args>) == ...`."""
    body = _answer_body(gen)
    m = re.search(r"f\((.*)\)\s*==", body, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"f\((.*)\)", body, re.DOTALL)
    return m.group(1).strip() if m else None


def grade_output(pred: str, true_output: str) -> bool:
    """Literal equality — NO code executed (doc 09). ast.literal_eval both sides;
    fall back to normalized string compare."""
    try:
        return ast.literal_eval(pred) == ast.literal_eval(true_output)
    except Exception:
        return " ".join(pred.split()) == " ".join(true_output.split())


def grade_inputs_sandboxed(rows):
    """rows: [{id, code, pred_input, output}] -> {id: ok}. One sandbox invocation."""
    if not rows:
        return {}
    OUT.mkdir(parents=True, exist_ok=True)
    stage = OUT / "igrade"
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "in.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    (stage / "grader.py").write_text(IGRADER)
    cp = sandbox.run(
        ["python3", "/tmp/grader.py"],
        stage=str(stage),
        time=600,
        capture_output=True,
        text=True,
    )
    outp = stage / "out.jsonl"
    if not outp.exists():
        print(cp.stdout[-1500:], cp.stderr[-1500:], file=sys.stderr)
        raise RuntimeError("sandboxed -I grader produced no output")
    return {
        r["id"]: r["ok"]
        for r in (json.loads(x) for x in outp.read_text().splitlines() if x.strip())
    }


def generate(items, task, cot, e, temperature, top_p, max_tokens, no_think):
    import prompts

    mk = {
        ("output_prediction", False): prompts.make_direct_output_prompt,
        ("output_prediction", True): prompts.make_cot_output_prompt,
        ("input_prediction", False): prompts.make_direct_input_prompt,
        ("input_prediction", True): prompts.make_cot_input_prompt,
    }[(task, cot)]
    client = endpoint.client()
    gens, secs, tin, tout = [], [], 0, 0
    for i, it in enumerate(items, 1):
        arg = (
            (it["code"], it["input"])
            if task == "output_prediction"
            else (it["code"], it["output"])
        )
        prompt = mk(arg)
        t0 = time.perf_counter()
        try:
            content, (pin_t, pout_t) = endpoint.chat_once(
                client,
                e["model"],
                [{"role": "user", "content": prompt}],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                no_think=no_think,
            )
            tin += pin_t
            tout += pout_t
        except Exception as exc:  # noqa: BLE001
            content = ""
            print(f"  [{i}/{len(items)}] gen error: {exc}", file=sys.stderr)
        secs.append(time.perf_counter() - t0)
        gens.append(content)
        if i % 50 == 0:
            print(f"  {task}: generated {i}/{len(items)}", file=sys.stderr)
    return gens, secs, {"in": tin, "out": tout}


def score_task(items, task, gens):
    if task == "output_prediction":
        correct = [
            grade_output(extract_output(g), it["output"]) for it, g in zip(items, gens)
        ]
    else:
        rows = []
        for it, g in zip(items, gens):
            pi = extract_input(g)
            if pi is not None:
                rows.append(
                    {
                        "id": it["id"],
                        "code": it["code"],
                        "pred_input": pi,
                        "output": it["output"],
                    }
                )
        verdict = grade_inputs_sandboxed(rows)
        correct = [bool(verdict.get(it["id"], False)) for it in items]
    return correct


def run_task(items, task, args, e):
    arm = "L1-remote-canonical" if args.arm == "L1" else "L2-local-canonical"
    gens, secs, tok = generate(
        items,
        task,
        args.cot,
        e,
        args.temperature,
        args.top_p,
        args.max_tokens,
        args.no_think,
    )
    correct = score_task(items, task, gens)
    passk = round(sum(correct) / len(correct), 4)
    tmg = timing.summarize(secs)
    run_id = ledger.make_run_id(
        f"cruxeval-{'O' if task == 'output_prediction' else 'I'}", arm
    )
    toggles = ledger.toggles_from_env()
    if args.no_think:
        toggles["no_think"] = True
    ledger.append(
        ledger.build_entry(
            run_id=run_id,
            benchmark="cruxeval",
            arm=arm,
            harness={"name": "cruxeval", "version": HARNESS_SHA},
            model=ledger.model_dict(
                endpoint.deployment(), e["base_url"], endpoint.precision(), e["model"]
            ),
            toggles=toggles,
            pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), len(items)),
            repeats=1,
            score=ledger.score_dict(
                "pass@1", passk, json.dumps({"task": task, "n": len(items)})
            ),
            timing=tmg,
            tokens=tok,
            notes=(
                f"task={task}; mode={'CoT' if args.cot else 'direct'}; "
                f"moderate contamination posture (public code); no_think={args.no_think}"
            ),
        )
    )
    unit = "item"
    line = report.one_liner(
        e["base_url"], e["model"], passk, tmg["mean_s"], tmg["n"], unit=unit
    )
    return arm, task, passk, tmg, line


def selftest():
    """Offline: exercise both grading paths with hand-made generations (no model)."""
    # -O: a correct + a wrong prediction.
    assert grade_output(extract_output("assert f(17) == 17 [/ANSWER]"), "17")
    assert not grade_output(extract_output("assert f(17) == 18 [/ANSWER]"), "17")
    assert grade_output(
        extract_output("[1, 2, 3][/ANSWER]"), "[1,2,3]"
    )  # spacing-robust
    # -I: sandbox grader on one correct + one wrong input.
    rows = [
        {
            "id": "ok",
            "code": "def f(x):\n    return x + 1",
            "pred_input": "16",
            "output": "17",
        },
        {
            "id": "bad",
            "code": "def f(x):\n    return x + 1",
            "pred_input": "99",
            "output": "17",
        },
    ]
    v = grade_inputs_sandboxed(rows)
    assert v == {"ok": True, "bad": False}, v
    print(f"SELFTEST OK: -O literal-eq + -I sandbox grading correct ({v})")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["L1", "L2"], default="L2")
    ap.add_argument(
        "--task",
        default="output_prediction,input_prediction",
        help="comma list: output_prediction,input_prediction",
    )
    ap.add_argument("--limit", type=int, default=None, help="first-N smoke cap")
    ap.add_argument(
        "--cot",
        action="store_true",
        help="chain-of-thought mode (keep fixed across arms)",
    )
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument(
        "--no-think", action="store_true", help="fast smoke: disable reasoning"
    )
    ap.add_argument(
        "--selftest", action="store_true", help="offline grading-path check (no model)"
    )
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    e = endpoint.env()
    if args.arm == "L1" and endpoint.deployment() != "remote":
        sys.exit("L1 selected but OPENAI_BASE_URL is not a remote endpoint (doc 00).")
    if not e["model"]:
        sys.exit("BENCH_MODEL is unset (doc 05 D1).")

    items = load_data(limit=args.limit)
    tasks = [t.strip() for t in args.task.split(",") if t.strip()]
    print(
        f"CRUXEval {args.arm}: {len(items)} items x {len(tasks)} task(s) via {e['base_url']} / {e['model']}"
    )
    lines, rows = [], []
    for task in tasks:
        arm, task, passk, tmg, line = run_task(items, task, args, e)
        half = "-O" if task == "output_prediction" else "-I"
        lines.append(f"[{half}] {line}")
        rows.append(
            f"- **{half} ({task})**: pass@1={passk}  (n={tmg['n']}, mean {tmg['mean_s']}s/item)"
        )

    run_id = ledger.make_run_id("cruxeval", arm)
    report.write(
        run_id,
        f"CRUXEval {arm} — code reasoning (-I / -O)",
        lines,
        body=(
            "\n".join(rows) + "\n\n"
            "CRUXEval stresses reasoning about execution. -O >= -I is the usual "
            "pattern. Mode: " + ("CoT" if args.cot else "direct") + " (match the "
            "leaderboard column you compare against). Sampling is deployed temp "
            "0.6, not greedy. Moderate contamination posture (public code) — read "
            "as capability + L1->L2 delta, not a contamination-free absolute."
        ),
    )
    print(f"\nREPORT + ledger written for cruxeval {arm}")


if __name__ == "__main__":
    main()
