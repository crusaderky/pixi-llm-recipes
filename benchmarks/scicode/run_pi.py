#!/usr/bin/env python3
"""SciCode through pi (design doc 03) — arms L3-local-pi-bare / L4-local-pi-tools
/ L5-local-pi-ext.

Reuses doc 01's SciCode machinery verbatim — the ScicodePromptingAssistant builds
each subproblem prompt (with_background, forwarding prior-subproblem solutions)
and the ScicodeEvaluator scores the generated code (do NOT reimplement grading).
Only generation differs: pi runs non-interactively per subproblem instead of the
inspect_ai solver.

V3 (tool disabling) resolved via pi's CLI:
  L3 bare  : pi -p --no-tools --no-extensions --no-skills --no-context-files
             (zero tool calls guaranteed by --no-tools — the closest single-shot
             analogue to the canonical harness)
  L4 tools : pi -p --no-extensions --no-skills  (default read/write/edit/bash)
  L5 ext   : L4 + ONLY pi-caveman + rtk (doc 03 KEEP set); everything else DROP.

pi is pointed at the local bench server (OpenAI-compatible) via --provider openai
--model $BENCH_MODEL + OPENAI_BASE_URL/OPENAI_API_KEY. Requires the SciCode venv
(setup.sh) AND pi on PATH (the repo `agents` env). Token totals come from the
llama-server log (doc 03), not a pi extension.

  .venv/bin/python run_pi.py --arm L3 --limit 3
"""

import argparse
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out" / "pi"
PIN_FILE = "pins/scicode-subset.txt"
HARNESS_SHA = "e3158ea011d4235245a547460d3688d7ccbf9900"

sys.path.insert(0, str(HERE.parent / "lib"))
from benchlib import endpoint, ledger, pins, report, timing  # noqa: E402
from run_canonical import (
    H5,
    _pin_path,
    load_upstream,
)  # reuse doc-01 setup  # noqa: E402

# pi extension flags per arm. DROP set (doc 03) is enforced by --no-extensions
# (L3/L4) or by loading ONLY the KEEP set (L5).
ARM_FLAGS = {
    "L3-local-pi-bare": [
        "--no-tools",
        "--no-extensions",
        "--no-skills",
        "--no-context-files",
    ],
    "L4-local-pi-tools": ["--no-extensions", "--no-skills", "--no-context-files"],
    # L5: default tools + only pi-caveman + rtk. pi-caveman loaded explicitly;
    # rtk is a conda CLI (rtk-cli) that pi's rtk integration shells out to — it
    # must be on PATH. --no-extensions keeps every OTHER deployed extension off.
    "L5-local-pi-ext": [
        "--no-skills",
        "--no-context-files",
    ],  # + resolved -e caveman at runtime
}
DROP_SET = [
    "pi-web-access",
    "pi-subagents",
    "pi-autoresearch",
    "pi-intercom",
    "pi-btw",
    "pi-token-speed",
    "pi-usage-extension",
    "rpiv-ask-user-question",
    "pi-llama-cpp",
]


def find_pi():
    for c in (
        os.environ.get("PI_BIN"),
        str(pathlib.Path.home() / "github/pixi-llm-recipes/.pixi/envs/agents/bin/pi"),
        "pi",
    ):
        if c and (pathlib.Path(c).exists() or c == "pi"):
            return c
    return "pi"


def run_pi(prompt, arm, model, workspace):
    """Run pi non-interactively; return (assistant_text, wall_s)."""
    flags = list(ARM_FLAGS[arm])
    cmd = [
        find_pi(),
        "-p",
        "--no-session",
        "--provider",
        "openai",
        "--model",
        model,
        *flags,
        prompt,
    ]
    env = dict(os.environ)
    e = endpoint.env()
    env["OPENAI_BASE_URL"] = e["base_url"]
    env["OPENAI_API_KEY"] = e["api_key"]
    t0 = time.perf_counter()
    cp = subprocess.run(
        cmd, cwd=str(workspace), env=env, capture_output=True, text=True, timeout=1800
    )
    return cp.stdout, time.perf_counter() - t0


def score_problem(US, prob_data, arm, model, temperature, workspace):
    """Generate every subproblem via pi, then score with the SciCode evaluator.
    Returns (total_correct, total_steps, [per-subproblem seconds])."""

    model_name = f"pi-{arm}"
    assistant = US.ScicodePromptingAssistant(
        output_dir=OUT / model_name / "generated_code",
        prompt_dir=OUT / model_name / "prompt",
        with_background=True,
    )
    template = US.BACKGOUND_PROMPT_TEMPLATE
    sub_steps = prob_data["sub_steps"]
    prob_id = prob_data["problem_id"]
    secs = []
    for idx in range(len(sub_steps)):
        if (
            (prob_id == "13" and idx == 5)
            or (prob_id == "62" and idx == 0)
            or (prob_id == "76" and idx == 2)
        ):
            continue
        prompt, prev = assistant.prepare_final_prompt_with_steps(
            prob_data=prob_data,
            num_steps=idx + 1,
            tot_steps=len(sub_steps),
            prompt_template=template,
        )
        text, dt = run_pi(prompt, arm, model, workspace)
        secs.append(dt)
        assistant.register_previous_response(
            prob_data=prob_data, response=text, previous_code=prev, num_steps=idx + 1
        )
    evaluator = US.ScicodeEvaluator(
        h5py_file=str(H5),
        code_dir=OUT / model_name,
        log_dir=OUT / model_name,
        with_background=True,
    )
    _, total_correct, total_steps = evaluator.test_code(prob_data)
    return total_correct, total_steps, secs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["L3", "L4", "L5"], required=True)
    ap.add_argument(
        "--limit", type=int, default=None, help="first-N pinned problems (smoke)"
    )
    ap.add_argument("--temperature", type=float, default=0.6)
    args = ap.parse_args()

    arm = {
        "L3": "L3-local-pi-bare",
        "L4": "L4-local-pi-tools",
        "L5": "L5-local-pi-ext",
    }[args.arm]
    e = endpoint.env()
    if not e["model"]:
        sys.exit("BENCH_MODEL is unset (doc 05 D1).")
    if not H5.exists():
        sys.exit(f"{H5} missing — run setup.sh to fetch test_data.h5 (~1 GB).")

    import tempfile

    from inspect_ai.dataset import hf_dataset

    US = load_upstream()
    ids = {str(i) for i in pins.scicode_ids(_pin_path())}
    if args.limit:
        ids = set(list(sorted(ids, key=int))[: args.limit])
    ds = hf_dataset("SciCode1/SciCode", split="test", sample_fields=US.record_to_sample)
    problems = [s.metadata for s in ds if str(s.id) in ids]

    print(
        f"SciCode {arm}: {len(problems)} pinned problems via pi -> {e['base_url']} / {e['model']}"
    )
    tot_correct = tot_steps = 0
    all_secs = []
    with tempfile.TemporaryDirectory(prefix="scicode-pi-") as ws:
        for i, prob in enumerate(problems, 1):
            tc, ts, secs = score_problem(
                US, prob, arm, e["model"], args.temperature, pathlib.Path(ws)
            )
            tot_correct += tc
            tot_steps += ts
            all_secs += secs
            print(
                f"  [{i}/{len(problems)}] problem {prob['problem_id']}: {tc}/{ts} subproblems",
                file=sys.stderr,
            )

    subrate = round(tot_correct / tot_steps, 4) if tot_steps else 0.0
    tmg = timing.summarize(all_secs)
    run_id = ledger.make_run_id("scicode", arm)
    ledger.append(
        ledger.build_entry(
            run_id=run_id,
            benchmark="scicode",
            arm=arm,
            harness={"name": "pi", "version": HARNESS_SHA},
            model=ledger.model_dict(
                endpoint.deployment(), e["base_url"], endpoint.precision(), e["model"]
            ),
            toggles=ledger.toggles_from_env(),
            pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), len(problems)),
            repeats=1,
            score=ledger.score_dict(
                "subproblem_pass@1", subrate, f"{tot_correct}/{tot_steps}"
            ),
            timing=tmg,
            notes=(
                f"pi arm {arm}; V3 tools via {' '.join(ARM_FLAGS[arm])}; "
                f"L5 DROP set enforced by --no-extensions; token totals from llama-server.log (doc 03)"
            ),
        )
    )
    line = report.one_liner(
        e["base_url"], e["model"], subrate, tmg["mean_s"], tmg["n"], unit="subproblem"
    )
    report.write(
        run_id,
        f"SciCode {arm} — through pi",
        [line],
        body=(
            f"- subproblem pass@1 = {subrate} ({tot_correct}/{tot_steps} subproblems, n={len(problems)} problems)\n"
            f"- pi tool/extension flags: {' '.join(ARM_FLAGS[arm])}\n"
            f"- DROP set (must be inert): {', '.join(DROP_SET)}\n\n"
            "Reuses doc-01 scoring (ScicodeEvaluator). Compare L3<->L2 (harness/prompt "
            "delta), L4<->L3 (tool lift), L5<->L4 (extension delta + token cost from "
            "llama-server.log). See REPORT-scicode-ladder for the combined table."
        ),
    )
    print(f"\n{line}")
    print(f"REPORT + ledger written for {run_id}")


if __name__ == "__main__":
    main()
