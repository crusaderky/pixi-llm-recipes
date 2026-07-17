#!/usr/bin/env python3
"""EvalPlus runner (design doc 08) — arms L1-remote-canonical / L2-local-canonical.

HumanEval+ / MBPP+ as a cheap capability FLOOR/tripwire (never a headline). We
headline the augmented ``+`` pass@1; base pass@1 is kept only to show the gap.
Two steps (doc 08):
  1. generate with evalplus via the OpenAI endpoint (evalplus.codegen internals,
     with max_new_tokens raised so a reasoning model can finish + emit code);
  2. grade with evalplus.evaluate, which EXECUTES every sample against the
     augmented tests — run INSIDE benchmarks/scripts/bench-sandbox.sh (doc 05 D2):
     no network, resource-capped, EVALPLUS's XDG cache redirected into the
     staged writable /tmp so the read-only host root is never written.

Run inside the EvalPlus venv (setup.sh):
  .venv/bin/python run_evalplus.py --arm L2 --datasets humaneval,mbpp
  .venv/bin/python run_evalplus.py --smoke-grade      # offline: grade canonical solutions
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from benchlib import endpoint, ledger, pins, report, sandbox  # noqa: E402

PIN_FILE = "pins/evalplus-full.txt"
HARNESS_VERSION = "0.3.1"
OUT = HERE / "out"
XDG = OUT / "xdg"  # evalplus cache (dataset + ground-truth); staged into sandbox
EVALUATE_BIN = str(HERE / ".venv" / "bin" / "evalplus.evaluate")
N_FULL = {"humaneval": 164, "mbpp": 378}
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _pin_path():
    from benchlib import PINS_DIR

    return PINS_DIR / "evalplus-full.txt"


def parse_passk(text: str) -> list[float]:
    """Extract the two `pass@1:` values evalplus.evaluate prints (base, then plus)."""
    return [float(v) for v in re.findall(r"pass@1:\s*([0-9.]+)", _ANSI.sub("", text))]


def _patch_max_new_tokens(n: int) -> None:
    """evalplus hard-codes the decoder without max_new_tokens (default 768) — far
    too small for a reasoning model. Force it via DecoderBase.__init__."""
    import evalplus.provider.base as base

    orig = base.DecoderBase.__init__
    if getattr(orig, "_bench_patched", False):
        return

    def new_init(self, *a, **kw):
        kw["max_new_tokens"] = n
        orig(self, *a, **kw)

    new_init._bench_patched = True
    base.DecoderBase.__init__ = new_init


def codegen(dataset, model, base_url, temperature, greedy, max_new_tokens):
    """Generate samples via evalplus (OpenAI backend). Returns (target_path, n, wall_s).

    Full dataset only: evalplus.evaluate requires a complete sample set."""
    from evalplus.codegen import run_codegen

    _patch_max_new_tokens(max_new_tokens)
    os.environ["XDG_CACHE_HOME"] = str(XDG)  # dataset caches here (host, network ok)
    id_range = None
    root = OUT / "root"
    root.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    target = run_codegen(
        model=model,
        dataset=dataset,
        root=str(root),
        backend="openai",
        base_url=base_url,
        temperature=temperature,
        greedy=greedy,
        n_samples=1,
        id_range=id_range,
        jsonl_fmt=True,
        resume=False,
    )
    wall = time.perf_counter() - t0
    n = sum(1 for _ in open(target))
    return pathlib.Path(target), n, wall


def _write_canonical(dataset, path):
    """Write a samples file of the dataset's own CANONICAL (trusted) solutions."""
    import evalplus.data as epd

    probs = epd.get_human_eval_plus() if dataset == "humaneval" else epd.get_mbpp_plus()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for tid, p in probs.items():
            f.write(
                json.dumps(
                    {"task_id": tid, "solution": p["prompt"] + p["canonical_solution"]}
                )
                + "\n"
            )


def warm_cache(dataset):
    """Populate the evalplus cache (base+plus datasets + ground-truth expected
    outputs) OUTSIDE the sandbox by grading CANONICAL solutions, so the real
    in-sandbox grading is a pure cache hit with NO network. The canonical
    solutions are the benchmark's own reference code — trusted, safe to run
    unsandboxed. Idempotent via a marker file."""
    marker = XDG / f".warmed-{dataset}"
    if marker.exists():
        return
    os.environ["XDG_CACHE_HOME"] = str(XDG)
    canon = OUT / "root" / dataset / "_canonical_all.jsonl"
    _write_canonical(dataset, canon)
    subprocess.run(
        [EVALUATE_BIN, dataset, "--samples", str(canon)],
        env=dict(os.environ, XDG_CACHE_HOME=str(XDG)),
        check=True,
        capture_output=True,
        text=True,
    )
    XDG.mkdir(parents=True, exist_ok=True)
    marker.write_text("warmed\n")


def grade(dataset, target_path):
    """Grade `target_path` inside the sandbox; returns (base_pass, plus_pass, stdout)."""
    warm_cache(dataset)  # ensure caches exist so the sandboxed run needs no network
    rel = target_path.relative_to(OUT)  # e.g. root/humaneval/<id>.jsonl
    in_sandbox_samples = f"/tmp/{rel}"
    env = dict(os.environ, XDG_CACHE_HOME="/tmp/xdg")  # writable staged cache
    cp = sandbox.run(
        [EVALUATE_BIN, dataset, "--samples", in_sandbox_samples],
        stage=str(OUT),
        time=3600,
        capture_output=True,
        text=True,
        env=env,
    )
    vals = parse_passk(cp.stdout + cp.stderr)
    if len(vals) < 2:
        print(cp.stdout[-2000:], file=sys.stderr)
        print(cp.stderr[-2000:], file=sys.stderr)
        raise RuntimeError(
            f"could not parse base/plus pass@1 for {dataset} (got {vals})"
        )
    return vals[0], vals[1], cp.stdout


def smoke_grade(datasets):
    """Offline plumbing check: grade the dataset's full CANONICAL solution set
    through the sandbox (no model). evalplus.evaluate requires a complete sample
    set, so we grade all problems. Proves sandbox exec + XDG-in-stage cache
    redirection + /dev/shm multiprocessing + stdout parsing, expecting pass@1==1.0."""
    for dataset in datasets:
        warm_cache(dataset)  # writes _canonical_all.jsonl + warms cache OUTSIDE sandbox
        canon = OUT / "root" / dataset / "_canonical_all.jsonl"
        base, plus, _ = grade(dataset, canon)  # grade full canonical set INSIDE sandbox
        status = "OK" if plus >= 0.99 else "UNEXPECTED (<1.0)"
        print(
            f"SMOKE-GRADE {dataset}: full canonical set -> base={base} plus={plus}  [{status}]"
        )


def run_arm(args):
    e = endpoint.env()
    arm = "L1-remote-canonical" if args.arm == "L1" else "L2-local-canonical"
    if args.arm == "L1" and endpoint.deployment() != "remote":
        sys.exit("L1 selected but OPENAI_BASE_URL is not a remote endpoint (doc 00).")
    if not e["model"]:
        sys.exit("BENCH_MODEL is unset (doc 05 D1).")
    os.environ.setdefault(
        "OPENAI_API_KEY", e["api_key"]
    )  # evalplus openai backend reads this

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    arm_lines, report_rows = [], []
    for dataset in datasets:
        target, n, wall = codegen(
            dataset,
            e["model"],
            e["base_url"],
            args.temperature,
            args.greedy,
            args.max_new_tokens,
        )
        base, plus, _ = grade(dataset, target)
        per_item = round(wall / max(n, 1), 3)
        tmg = {
            "n": n,
            "mean_s": per_item,
            "median_s": per_item,
            "min_s": per_item,
            "max_s": per_item,
        }
        raw = json.dumps(
            {"dataset": dataset, "base_pass@1": base, "plus_pass@1": plus, "n": n}
        )
        run_id = ledger.make_run_id(f"evalplus-{dataset}", arm)
        toggles = ledger.toggles_from_env()
        if args.greedy:
            toggles["greedy"] = True
        ledger.append(
            ledger.build_entry(
                run_id=run_id,
                benchmark="evalplus",
                arm=arm,
                harness={"name": "evalplus", "version": HARNESS_VERSION},
                model=ledger.model_dict(
                    endpoint.deployment(),
                    e["base_url"],
                    endpoint.precision(),
                    e["model"],
                ),
                toggles=toggles,
                pin=ledger.pin_dict(PIN_FILE, pins.sha256(_pin_path()), n),
                repeats=1,
                score=ledger.score_dict("pass@1_plus", plus, raw),
                timing=tmg,
                notes=(
                    f"FLOOR/tripwire; dataset={dataset}+; base pass@1={base}; "
                    f"greedy={args.greedy}; per-item timing = codegen wall / n (evalplus emits no per-item time)"
                ),
            )
        )
        line = report.one_liner(
            e["base_url"],
            e["model"],
            plus,
            per_item,
            n,
            unit="problem",
            config=e.get("config", ""),
        )
        arm_lines.append(f"[{dataset}+] {line}")
        report_rows.append(
            f"- **{dataset}**: base pass@1={base}  |  **plus pass@1={plus}**  (n={n})"
        )

    run_id = ledger.make_run_id("evalplus", arm)
    report.write(
        run_id,
        f"EvalPlus {arm} — HumanEval+/MBPP+ (capability FLOOR)",
        arm_lines,
        body=(
            "\n".join(report_rows) + "\n\n"
            "EvalPlus is a floor/tripwire, not the headline quant signal (that is "
            "LiveCodeBench, doc 06). Headline the `+` numbers. Public leaderboard "
            f"numbers are greedy pass@1; this run used greedy={args.greedy} "
            "(sampled temp 0.6 unless --greedy). A large L2 drop on these easy "
            "problems is a red flag for the local stack, not model weakness."
        ),
    )
    print(f"\nREPORT + ledger written for evalplus {arm}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", choices=["L1", "L2"], default="L2")
    ap.add_argument(
        "--datasets", default="humaneval,mbpp", help="comma list: humaneval,mbpp"
    )
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument(
        "--greedy", action="store_true", help="temp 0 for leaderboard parity (doc 08)"
    )
    ap.add_argument(
        "--max-new-tokens", type=int, default=12000, help="raised for reasoning models"
    )
    ap.add_argument(
        "--smoke-grade",
        action="store_true",
        help="offline: grade canonical solutions (no model)",
    )
    args = ap.parse_args()

    if args.smoke_grade:
        smoke_grade([d.strip() for d in args.datasets.split(",") if d.strip()])
        return
    run_arm(args)


if __name__ == "__main__":
    main()
