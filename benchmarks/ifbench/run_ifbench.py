#!/usr/bin/env python3
"""IFBench runner (design doc 07) — arms L1-remote-canonical / L2-local-canonical.

IFBench measures precise, verifiable instruction-following (NOT coding). We
generate one completion per held-out OOD prompt through the OpenAI-compatible
endpoint, then grade with IFBench's OWN verifier functions (``evaluation_lib``),
reporting strict/loose × prompt/instruction accuracies. No code is executed, so
no exec sandbox is used.

Run inside the IFBench venv created by setup.sh:
    .venv/bin/python run_ifbench.py --arm L2
"""

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
IFBENCH = HERE / "IFBench"

# benchlib is imported by path (it is not pip-installed).
sys.path.insert(0, str(HERE.parent / "lib"))
# IFBench modules (evaluation_lib, instructions_registry, ...) live in ./IFBench.
sys.path.insert(0, str(IFBENCH))

from benchlib import endpoint, ledger, pins, report, timing  # noqa: E402

PIN_FILE = "pins/ifbench-full.txt"
HARNESS_SHA = "1091c4c3de6c1f6ed12c012ed68f11ea450b0117"
TEST_DATA = IFBENCH / "data" / "IFBench_test.jsonl"


def load_inputs(limit=None):
    import evaluation_lib

    inputs = evaluation_lib.read_prompt_list(str(TEST_DATA))
    if limit:
        inputs = inputs[:limit]
    return inputs


def grade(inputs, prompt_to_response):
    """Return (per-example strict outputs, loose outputs) via IFBench verifiers."""
    import evaluation_lib

    strict = [
        evaluation_lib.test_instruction_following_strict(i, prompt_to_response)
        for i in inputs
    ]
    loose = [
        evaluation_lib.test_instruction_following_loose(i, prompt_to_response)
        for i in inputs
    ]
    return strict, loose


def accuracies(strict, loose):
    def prompt_acc(outs):
        return sum(o.follow_all_instructions for o in outs) / len(outs)

    def inst_acc(outs):
        num = sum(sum(o.follow_instruction_list) for o in outs)
        den = sum(len(o.follow_instruction_list) for o in outs)
        return num / den

    return {
        "prompt_acc_strict": round(prompt_acc(strict), 4),
        "prompt_acc_loose": round(prompt_acc(loose), 4),
        "inst_acc_strict": round(inst_acc(strict), 4),
        "inst_acc_loose": round(inst_acc(loose), 4),
    }


def generate(inputs, model, temperature, top_p, max_tokens):
    """Sequential generation for clean per-item timing. Returns (responses, secs, usage)."""
    client = endpoint.client()
    responses, secs = {}, []
    tok_in = tok_out = 0
    for i, inp in enumerate(inputs, 1):
        t0 = time.perf_counter()
        try:
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": inp.prompt}],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            content = r.choices[0].message.content or ""
            if getattr(r, "usage", None):
                tok_in += r.usage.prompt_tokens or 0
                tok_out += r.usage.completion_tokens or 0
        except Exception as exc:  # noqa: BLE001 — a failed gen scores as empty
            content = ""
            print(f"  [{i}/{len(inputs)}] generation error: {exc}", file=sys.stderr)
        secs.append(time.perf_counter() - t0)
        responses[inp.prompt] = content
        if i % 25 == 0:
            print(f"  generated {i}/{len(inputs)}", file=sys.stderr)
    return responses, secs, {"in": tok_in, "out": tok_out}


def selftest(limit):
    """Offline plumbing check: fake responses -> grade -> build+validate a ledger entry."""
    inputs = load_inputs(limit=limit or 3)
    fake = {i.prompt: i.prompt for i in inputs}  # echo — will mostly fail, that's fine
    strict, loose = grade(inputs, fake)
    acc = accuracies(strict, loose)
    entry = ledger.build_entry(
        run_id=ledger.make_run_id("ifbench", "L2-local-canonical"),
        benchmark="ifbench",
        arm="L2-local-canonical",
        harness={"name": "ifbench", "version": HARNESS_SHA},
        model=ledger.model_dict(
            "local", "http://localhost:8080/v1", "unknown", "selftest"
        ),
        score=ledger.score_dict(
            "prompt_acc_strict", acc["prompt_acc_strict"], json.dumps(acc)
        ),
        timing=timing.summarize([0.1] * len(inputs)),
        pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), len(inputs)),
        notes="selftest",
    )
    ledger.validate(entry)
    print(
        f"SELFTEST OK: graded {len(inputs)} inputs; metrics={acc}; ledger entry valid"
    )


def _pin_path():
    from benchlib import PINS_DIR

    return PINS_DIR / "ifbench-full.txt"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["L1", "L2"], default="L2")
    ap.add_argument("--limit", type=int, default=None, help="first-N smoke cap")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument(
        "--selftest", action="store_true", help="offline plumbing check (no model)"
    )
    args = ap.parse_args()

    if args.selftest:
        selftest(args.limit)
        return

    e = endpoint.env()
    arm = "L1-remote-canonical" if args.arm == "L1" else "L2-local-canonical"
    if args.arm == "L1" and endpoint.deployment() != "remote":
        sys.exit(
            "L1 selected but OPENAI_BASE_URL is not a remote endpoint — set it (doc 00)."
        )
    if not e["model"]:
        sys.exit("BENCH_MODEL is unset — set it to your models.ini preset (doc 05 D1).")

    inputs = load_inputs(limit=args.limit)
    pin_n = len(inputs)
    print(
        f"IFBench {arm}: {pin_n} prompts x {args.repeats} repeat(s) via {e['base_url']} / {e['model']}"
    )

    all_secs, tok = [], {"in": 0, "out": 0}
    accs = []
    resp_path = HERE / "out" / f"responses-{arm}.jsonl"
    resp_path.parent.mkdir(parents=True, exist_ok=True)
    for rep in range(args.repeats):
        responses, secs, usage = generate(
            inputs, e["model"], args.temperature, args.top_p, args.max_tokens
        )
        all_secs += secs
        tok["in"] += usage["in"]
        tok["out"] += usage["out"]
        strict, loose = grade(inputs, responses)
        accs.append(accuracies(strict, loose))
        if rep == 0:
            with open(resp_path, "w") as f:
                for p, c in responses.items():
                    f.write(json.dumps({"prompt": p, "response": c}) + "\n")

    # Mean across repeats.
    acc = {k: round(sum(a[k] for a in accs) / len(accs), 4) for k in accs[0]}
    tmg = timing.summarize(all_secs)

    run_id = ledger.make_run_id("ifbench", arm)
    entry = ledger.build_entry(
        run_id=run_id,
        benchmark="ifbench",
        arm=arm,
        harness={"name": "ifbench", "version": HARNESS_SHA},
        model=ledger.model_dict(
            endpoint.deployment(), e["base_url"], endpoint.precision(), e["model"]
        ),
        toggles=ledger.toggles_from_env(),
        pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), pin_n),
        repeats=args.repeats,
        score=ledger.score_dict(
            "prompt_acc_strict", acc["prompt_acc_strict"], json.dumps(acc)
        ),
        timing=tmg,
        tokens=tok,
        notes=(
            "instruction-following, NOT coding; IFBench held-out OOD eval set; "
            f"all accuracies: {acc}"
        ),
    )
    ledger.append(entry)

    line = report.one_liner(
        e["base_url"],
        e["model"],
        acc["prompt_acc_strict"],
        tmg["mean_s"],
        tmg["n"],
        unit="prompt",
        config=e.get("config", ""),
    )
    report.write(
        run_id,
        f"IFBench {arm} — instruction-following (NOT coding)",
        [line],
        body=(
            f"- prompt-level strict/loose: {acc['prompt_acc_strict']} / {acc['prompt_acc_loose']}\n"
            f"- instruction-level strict/loose: {acc['inst_acc_strict']} / {acc['inst_acc_loose']}\n"
            f"- tokens: in={tok['in']} out={tok['out']}\n\n"
            "IFBench is an instruction-following reliability probe, not a coding score. "
            "Frontier models sit <50% (paper, arXiv:2507.02833). Sampling here is the "
            "deployed temp=0.6, not greedy — the paper generally reports temp 0. "
            "A notable L1->L2 drop is a clean format-reliability signal of quantization "
            "damage worth flagging for the pi arms."
        ),
    )
    print(f"\n{line}")
    print(f"REPORT + ledger written for {run_id}")


if __name__ == "__main__":
    main()
