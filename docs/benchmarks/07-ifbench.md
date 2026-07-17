# 07 — IFBench (instruction-following, single-turn)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00` (bench profile, ledger, calibration, hygiene),
  `05` (deliverables **D1 + D3 only** — **NOT D2**: IFBench executes no model
  code, so this doc can be implemented before the execution sandbox exists).
- **Independent of:** docs 01–04, 06, 08, 09.
- **Unblocks:** nothing (leaf); the easiest panel benchmark to stand up first.

## External dependencies (require user input)

- **Reference endpoint + API key** (L1 only) — per doc 00; user supplies
  endpoint URL + model id + explicit precision label (no vendor certification).
- **IFBench commit pin** — clone github.com/allenai/IFBench and pin a commit;
  record in `harness.version`.

Implements arms **L1-remote-canonical** and **L2-local-canonical** for IFBench.

## Scope honesty (read first)

IFBench is **not a coding benchmark.** It measures whether a model obeys
precise, verifiable output constraints ("exactly 3 bullet points", "no commas",
"end every sentence with a period", word/char counts, copying/formatting) on
held-out WildChat prompts. It is in this panel because:

1. It is the **cheapest, easiest** benchmark here to stand up — single-turn,
   deterministic Python verifier functions, **no code execution, no Docker, no
   sandbox**.
2. Constraint/format adherence is exactly the brittle behavior that breaks
   tool-calling and edit-format compliance in the pi arms (docs 03/04), and it
   is a behavior **quantization tends to degrade early** — so it is a useful
   _reliability_ read on the L1→L2 local-stack delta, orthogonal to coding
   capability.

Every REPORT must label it as instruction-following, not coding.

## Why IFBench (not IFEval)

IFEval (the Open-LLM-Leaderboard one, present in `inspect_evals` as `ifeval`)
is **saturated** — strong models score >80% and high scores partly reflect
overfitting to its fixed constraint templates. IFBench (AI2/UW, NeurIPS 2025,
arXiv:2507.02833) uses **58 held-out out-of-domain constraints** with fresh
verifier functions; the same models that clear 80% on IFEval score **<50%** on
IFBench, so it still discriminates. 294 prompts. **Not** in `inspect_evals` —
use the upstream **`allenai/IFBench`** harness.

(Optional cheap cross-check: IFEval _is_ in `inspect_evals` and trivial to run
as a saturated baseline — but it is not this doc's deliverable.)

## Setup (isolated env — doc 05 §Harness isolation)

```bash
git clone https://github.com/allenai/IFBench && cd IFBench
uv venv .venv-ifb && . .venv-ifb/bin/activate
uv pip install -e .        # pin the commit; record in ledger harness.version
```

Generation goes through the OpenAI endpoint (doc 05 D1); grading runs the
benchmark's verifier functions in-process — **no sandbox needed**.

## Selection / pin

The full set is **294 prompts** and runs in minutes, so the **default is the
full suite** (`pins/ifbench-full.txt` documents this and an optional first-N cap
for a quick smoke). Same selection for L1 and L2. Use the **IFBench eval set**
(the held-out OOD constraints); do **not** evaluate on the IFTrain constraints.

## Running

### L2 — local canonical

```bash
pixi r -e llamacpp-source-cuda start-server     # Qwen3.6-35B-A3B-bench
export OPENAI_BASE_URL="http://localhost:8080/v1" OPENAI_API_KEY="sk-local"
export BENCH_MODEL="Qwen3.6-35B-A3B-bench"
python -m ifbench.eval \
  --model openai/$BENCH_MODEL \
  --input_data ifbench_eval \
  --temperature 0.6 --top_p 0.95
# (confirm exact entrypoint/flags against the pinned commit)
```

- May use `--parallel 4` server-side (prompts are short). Default **1 repeat**.
- `score.metric` = **`prompt_acc_strict`** (the primary headline; IFBench, like
  IFEval, also yields loose + instruction-level accuracies — record all four in
  `score.raw`, headline on strict prompt-level).

### L1 — reference endpoint (precision labelled)

```bash
export OPENAI_BASE_URL="https://<vendor>/v1" OPENAI_API_KEY="…"
export BENCH_MODEL="Qwen3.6-35B-A3B"
python -m ifbench.eval --model openai/$BENCH_MODEL \
  --input_data ifbench_eval --temperature 0.6 --top_p 0.95
# 3 repeats remotely (cheap)
```

Record the endpoint's precision label in the ledger (doc 00); no vendor
certification required.

## Wall-time plan

Trivially within budget: 294 short single-turn prompts, deterministic grading.
Still run the doc 00 calibration probe (3–5 prompts) for the record; abort rule
will essentially never trigger. The only real cost is generation length when
the model "thinks" a lot (T2 OFF by default).

## Pass criteria

Smoke-level:

- [ ] One-prompt run against L2 returns a completion and the verifier emits a
      per-constraint pass/fail — proves generation→verifier plumbing (no
      sandbox involved).

Arm-complete:

- [ ] L2 full-set run finishes; strict prompt-level accuracy recorded; ledger
      (`benchmark=ifbench`, `score.metric=prompt_acc_strict`) + `REPORT`.
- [ ] L1 full-set run finishes; accuracy recorded.
- [ ] `REPORT` prints the one-line summary for each arm
      (`<URL> / <model> -> <prompt_acc_strict>  (mean <mean_s>s/prompt, n=<N>)`,
      doc 00), states L1/L2 strict + loose + instruction-level accuracies,
      **labels the benchmark as instruction-following (not coding)**, and
      compares to the IFBench paper's reported numbers for a comparable model
      class (noting frontier models sit <50%). No numeric gate (Q8).

Sanity (narrative):

- [ ] L2 ≤ L1 expected; a notable L1→L2 drop here is a clean _format-reliability_
      signal of quantization damage and is worth flagging for the pi arms (a
      model that drops constraint adherence under quantization will also drop
      tool/edit-format adherence).

## Notes / gotchas

- This is the one panel benchmark that needs **neither** Docker **nor** the
  execution sandbox — schedule it first if the sandbox (D2) isn't ready.
- Do not let the model see the verifier code or constraint list beyond the
  prompt; standard hygiene (no web tools) applies (doc 00).
- Keep the constraint set fixed across arms — swapping eval/train constraints
  between L1 and L2 would break comparability.
