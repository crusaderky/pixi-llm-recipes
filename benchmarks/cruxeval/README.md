# CRUXEval (design doc 09) — code reasoning (-I / -O)

800 short self-contained Python functions, each with one input/output example,
yielding two single-turn tasks measuring reasoning about _execution semantics_
(orthogonal to code generation):

- **CRUXEval-O** (output prediction): given code + input, predict the output.
  Graded by **literal equality** (`ast.literal_eval` both sides — NO model code
  executed), so it needs **no sandbox**.
- **CRUXEval-I** (input prediction): given code + a target output, predict an
  input. Graded by **executing** `f(predicted_input) == output` — untrusted, so
  it runs through `benchmarks/scripts/bench-sandbox.sh` (doc 05 D2), all items in
  **one** sandbox invocation (a batched grader script, per-item `SIGALRM`
  timeout, no network).

- Harness: `facebookresearch/cruxeval` pinned at `190faf16` (its `prompts.py` +
  `data/cruxeval.jsonl` are reused; generation is ours via the OpenAI endpoint).
- Arms: **L2** (runnable), **L1** (wired). `benchmark=cruxeval`,
  `score.metric=pass@1`, **two ledger entries** (`-I`, `-O`).
- Mode: **direct (no CoT)** by default — keep it fixed across arms and matching
  the leaderboard column you compare against (`--cot` for the CoT column).

## Setup

```bash
cd benchmarks/cruxeval
bash setup.sh          # isolated .venv (numpy/tabulate/openai) + pinned CRUXEval
```

## Run

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local
export BENCH_MODEL=Qwen3.6-35B-A3B BENCH_PRECISION="IQ4_XS+q8_0kv"
export BENCH_T1_FROGGERIC=0 BENCH_T2_REASONING_BUDGET=0 BENCH_T3_KV=q8_0

.venv/bin/python run_cruxeval.py --arm L2 --task output_prediction,input_prediction
.venv/bin/python run_cruxeval.py --arm L2 --limit 20 --no-think     # fast smoke
```

`--no-think` disables reasoning for a fast smoke (records `toggles.no_think`);
omit it for a deployed-stack headline run. Each task appends one ledger entry;
the run writes a combined `REPORT-<run_id>.md`.

## Smoke checks

- **Offline (no model):** `.venv/bin/python run_cruxeval.py --selftest` exercises
  -O extraction+literal-equality and the -I sandbox grader on hand-made answers.
- **Live (L2):** `--limit 20 --no-think` for a quick end-to-end pass.

## Contamination posture

Moderate/weak (derived from public code). Read CRUXEval as capability + the
L1→L2 local-stack delta, not a contamination-free absolute. If the local stack
degrades reasoning more than surface code-gen, expect the -I/-O L1→L2 drop to
exceed EvalPlus's — a reportable contrast. `-O ≥ -I` is the usual pattern.
