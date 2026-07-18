#!/usr/bin/env python3
"""Terminal-Bench 2.0 runner (design docs 02 Terminus 2 + 04 pi adapter).

Arms:
  L1/L2 (doc 02): Harbor + Terminus 2 (model-agnostic tmux agent, no native tools)
  L4    (doc 04a): vanilla pi via badlogic's pi-terminal-bench adapter
  L5    (doc 04b): pi + ONLY {pi-caveman, rtk} (install-extensions.sh in-container)

All arms use the IDENTICAL pin (pins/tb2-quick.txt) so Terminus-vs-pi (L2 vs L4)
and pi-vs-pi+ext (L4 vs L5) are clean comparisons. Metric: task_pass@1. Docker is
required at run time; the model endpoint is reached from the task container at
http://host.docker.internal:8080/v1 (doc 02 V2). `--dry` prints the resolved
Harbor JobConfig and exits (offline validation, no Docker).

Harbor CLI resolved against the installed version (0.19.x): dataset
`terminal-bench@2.0` (registry name; 2.0 is the latest — no 2.1 exists),
`-a terminus-2` / `-a <import:Class>`, `-i <task>` per
pin, `--agent-env` to inject the endpoint, `-k` attempts, `-o` jobs-dir. Task
`agent_to` (900/750 for the pinned set) binds via the default timeout multiplier.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ADAPTER = HERE / "pi-terminal-bench"
OUT = HERE / "out"
HARBOR = str(HERE / ".venv" / "bin" / "harbor")
PIN_FILE = "pins/tb2-quick.txt"
# Registry dataset NAME is `terminal-bench` version `2.0` (doc 00; confirmed via
# `harbor dataset list --legacy`) — NOT `terminal-bench-2`, which is the source
# REPO name. 2.0 is the latest published version (no 2.1 exists as of 2026-07-18).
DATASET = "terminal-bench@2.0"
DATASET_COMMIT = "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"  # repo laude-institute/terminal-bench-2 SHA (doc 00)
ADAPTER_SHA = "0074c915dc7d8ceeba5f61b19e7b9aa078564fa3"
PI_AGENT_IMPORT = "pi_terminal_bench.pi_agent:PiAgent"

sys.path.insert(0, str(HERE.parent / "lib"))
from benchlib import endpoint, ledger, pins, report, timing  # noqa: E402

ARMS = {
    "L1": "L1-remote-canonical",
    "L2": "L2-local-canonical",
    "L4": "L4-local-pi-tools",
    "L5": "L5-local-pi-ext",
}


def _pin_path():
    from benchlib import PINS_DIR

    return PINS_DIR / "tb2-quick.txt"


def agent_args(arm_short):
    """Terminus 2 for L1/L2; the pi adapter for L4/L5."""
    if arm_short in ("L1", "L2"):
        return ["-a", "terminus-2"]
    return ["-a", PI_AGENT_IMPORT]  # doc 04: badlogic adapter PiAgent


def build_cmd(arm_short, model, tasks, jobs_dir, attempts, agent_base_url, api_key):
    cmd = [
        HARBOR,
        "run",
        "-d",
        DATASET,
        *agent_args(arm_short),
        "-m",
        f"openai/{model}",
    ]
    for t in tasks:
        cmd += ["-i", t]
    cmd += [
        "-k",
        str(attempts),
        "-n",
        "1",  # single GPU: one trial at a time
        "-o",
        str(jobs_dir),
        # doc 02 V2: the agent (in-container) reaches the host llama-server here.
        "--agent-env",
        f"OPENAI_BASE_URL={agent_base_url}",
        "--agent-env",
        f"OPENAI_API_KEY={api_key}",
        "--allow-agent-host",
        "host.docker.internal",
        "-y",
    ]
    return cmd


def parse_results(jobs_dir):
    """(task_pass@1, [per-task seconds]) from Harbor trial results. Defensive across
    result-schema field names — confirm on first real run and pin the shape."""
    verdicts, secs = [], []
    for rp in jobs_dir.rglob("*.json"):
        if rp.name not in (
            "results.json",
            "result.json",
            "trial.json",
            "trial_result.json",
        ):
            continue
        try:
            d = json.loads(rp.read_text())
        except Exception:  # noqa: BLE001
            continue
        recs = d if isinstance(d, list) else [d]
        for r in recs:
            if not isinstance(r, dict):
                continue
            v = r.get("is_resolved", r.get("resolved", r.get("passed")))
            if v is None and "reward" in r:
                v = bool(r["reward"])
            if v is not None:
                verdicts.append(bool(v))
            for k in ("duration_s", "duration", "total_time", "agent_time_s"):
                if isinstance(r.get(k), (int, float)):
                    secs.append(float(r[k]))
                    break
    passk = round(sum(verdicts) / len(verdicts), 4) if verdicts else 0.0
    return passk, secs, len(verdicts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=list(ARMS), default="L2")
    ap.add_argument(
        "--limit", type=int, default=None, help="first-N pinned tasks (smoke)"
    )
    ap.add_argument(
        "--attempts", type=int, default=1, help="-k / n-attempts (doc: 1; toggle 3)"
    )
    ap.add_argument(
        "--dry",
        action="store_true",
        help="print the resolved Harbor JobConfig and exit (no Docker)",
    )
    args = ap.parse_args()

    e = endpoint.env()
    arm = ARMS[args.arm]
    if args.arm == "L1" and endpoint.deployment() != "remote":
        sys.exit("L1 selected but OPENAI_BASE_URL is not a remote endpoint (doc 00).")
    model = e["model"] or "MODEL"

    tasks = pins.list_items(_pin_path())
    if args.limit:
        tasks = tasks[: args.limit]

    # From inside a task container the host server is host.docker.internal; a
    # remote L1 endpoint is used verbatim. Overridable via BENCH_TB_AGENT_BASE_URL.
    default_base = (
        e["base_url"]
        .replace("localhost", "host.docker.internal")
        .replace("127.0.0.1", "host.docker.internal")
    )
    agent_base_url = os.environ.get("BENCH_TB_AGENT_BASE_URL", default_base)

    jobs_dir = OUT / f"tb2-{args.arm}"
    cmd = build_cmd(
        args.arm, model, tasks, jobs_dir, args.attempts, agent_base_url, e["api_key"]
    )

    if args.dry:
        subprocess.run([*cmd[: cmd.index("-y")], "--print-config"], check=False)
        print(
            f"\n[dry] {arm}: {len(tasks)} pinned tasks, dataset {DATASET}, agent endpoint {agent_base_url}",
            file=sys.stderr,
        )
        return

    print(
        f"Terminal-Bench {arm}: {len(tasks)} pinned tasks x k={args.attempts}, agent endpoint {agent_base_url}"
    )
    jobs_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    passk, secs, n = parse_results(jobs_dir)
    tmg = (
        timing.summarize(secs)
        if secs
        else {
            "n": len(tasks),
            "mean_s": 0.0,
            "median_s": 0.0,
            "min_s": 0.0,
            "max_s": 0.0,
        }
    )

    run_id = ledger.make_run_id("tb2", arm)
    harness = {
        "name": "harbor-terminus2" if args.arm in ("L1", "L2") else "harbor-pi",
        "version": f"harbor0.19; dataset@{DATASET_COMMIT}; adapter@{ADAPTER_SHA}",
    }
    ledger.append(
        ledger.build_entry(
            run_id=run_id,
            benchmark="tb2",
            arm=arm,
            harness=harness,
            model=ledger.model_dict(
                endpoint.deployment(), e["base_url"], endpoint.precision(), model
            ),
            toggles=ledger.toggles_from_env(),
            pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), len(tasks)),
            repeats=args.attempts,
            score=ledger.score_dict("task_pass@1", passk, str(jobs_dir)),
            timing=tmg,
            notes=(
                f"TB2.0 {'Terminus 2' if args.arm in ('L1', 'L2') else 'pi adapter'}; dataset "
                f"terminal-bench@2.0 (repo commit {DATASET_COMMIT}); {n} tasks graded; agent endpoint {agent_base_url}"
            ),
        )
    )
    line = report.one_liner(
        e["base_url"], model, passk, tmg["mean_s"], tmg["n"], unit="task"
    )
    report.write(
        run_id,
        f"Terminal-Bench 2.0 {arm}",
        [line],
        body=(
            f"- task pass@1 = {passk} ({n}/{len(tasks)} tasks graded)\n"
            "Compare to the tbench.ai terminal-bench@2.0 leaderboard (Terminus-class), "
            "noting subset != full suite. L2 (Terminus) vs L4 (pi) isolates the harness "
            "delta; L4 vs L5 the extension delta + token cost (llama-server.log)."
        ),
    )
    print(f"\n{line}\nREPORT + ledger written for {run_id}")


if __name__ == "__main__":
    main()
