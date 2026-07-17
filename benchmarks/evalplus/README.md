# EvalPlus (design doc 08) — HumanEval+ / MBPP+, capability FLOOR

A **cheap capability floor / tripwire**, not a headline (the headline quant
signal is LiveCodeBench, doc 06). Plain HumanEval/MBPP are saturated +
contaminated; EvalPlus adds ~80× more tests (the `+` sets) that catch subtly
wrong solutions. **Always headline the `+` pass@1**; base pass@1 is kept only to
show the gap. If L2 craters here, the local stack is broken (bad template, KV
corruption, misconfig) — a tripwire.

- Harness: upstream `evalplus` **0.3.1** (pip). No `[vllm]` extra — generation is
  via the OpenAI endpoint; grading executes locally.
- Arms: **L2-local-canonical** (runnable) and **L1-remote-canonical** (wired).
- Two ledger entries per arm (HumanEval+ and MBPP+), `benchmark=evalplus`,
  `score.metric=pass@1_plus` (base pass@1 recorded in `score.raw`).
- Full datasets only (164 + 378): `evalplus.evaluate` asserts a complete sample
  set, so there is no partial-subset run.

## How grading stays sandboxed (doc 05 D2)

`evalplus.evaluate` executes every model sample against the augmented tests.
Untrusted, so it runs inside `benchmarks/scripts/bench-sandbox.sh` (no network,
resource-capped). Two wrinkles the runner handles:

- **Cache under a read-only host:** evalplus caches datasets + ground-truth
  expected outputs under `XDG_CACHE_HOME`. The runner points that at the staged
  writable `/tmp` inside the sandbox.
- **No network in the sandbox:** the cache is pre-warmed **outside** the sandbox
  by grading the dataset's own **canonical (trusted) solutions** first, so the
  in-sandbox grading of untrusted samples is a pure offline cache hit.

## Setup

```bash
cd benchmarks/evalplus
bash setup.sh          # isolated .venv, evalplus==0.3.1 + openai
```

## Run

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local
export BENCH_MODEL=Qwen3.6-35B-A3B BENCH_PRECISION="IQ4_XS+q8_0kv"
export BENCH_T1_FROGGERIC=0 BENCH_T2_REASONING_BUDGET=0 BENCH_T3_KV=q8_0

.venv/bin/python run_evalplus.py --arm L2 --datasets humaneval,mbpp
.venv/bin/python run_evalplus.py --arm L2 --datasets humaneval --greedy   # leaderboard parity
```

`--greedy` (temp 0) is reasonable here for parity with the public greedy
leaderboard (records `toggles.greedy=true`); default is the deployed sampled
temp 0.6. `--max-new-tokens` (default 12000) is raised so a reasoning model can
finish thinking and still emit the code block (evalplus's own default of 768 is
far too small).

Each dataset appends one ledger entry and the run writes a combined
`REPORT-<run_id>.md`. Per-item timing is `codegen wall / n` (evalplus emits no
per-item timing) — noted in the ledger.

## Smoke checks

- **Offline grading (no model):**
  `.venv/bin/python run_evalplus.py --smoke-grade --datasets humaneval` grades
  the full canonical solution set through the sandbox and expects pass@1≈1.0 —
  proves the exec sandbox + cache redirection + parsing without the server.
- **Live (L2):** a full `--arm L2` run (generation is slow on a reasoning model;
  see the note below).

## Note on generation speed

On the reference RTX 3080 config (Qwen3.6-35B-A3B IQ4_XS, `n-cpu-moe=40`, ~10
tok/s) with reasoning enabled, each answer costs thousands of thinking tokens →
minutes per problem. A full EvalPlus run is therefore a multi-hour job; size it
with the doc-00 calibration probe + abort rule, and consider a
benchmark-tuned server preset (fewer CPU-offloaded experts, managed reasoning
budget) per doc 00 T2.
