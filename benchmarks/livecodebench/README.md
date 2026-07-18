# LiveCodeBench (design doc 06) — code generation, contamination-controlled

The panel's **primary quant-delta signal**: its contamination control is the
strongest, so the L1→L2 gap is a clean read on the local stack (4-bit weights +
q8_0 KV) rather than memorization. Scenario: **code_generation only**. Metric:
**pass@1**.

- Harness: `LiveCodeBench/LiveCodeBench` pinned at `28fef95` — **not on PyPI**
  (doc 06's `pip install livecodebench` was representative). We import its
  canonical dataset/prompt/extraction/grading from the clone and drive
  generation via the OpenAI endpoint. Installed **lean**: no torch/vllm (local
  inference, unused) and no pyext (its import is commented out upstream);
  `datasets` pinned `<4` (the dataset uses a loading script datasets≥4 dropped).
- Arms: **L2** (runnable), **L1** (wired).

## Contamination pre-check (mandatory, doc 06)

The runner calls `docs/benchmarks/lookup_cutoff.py $BENCH_MODEL`. A **missing**
model crashes (exit 2) — add it to `model-cutoffs.toml`. The window START must be
**after** the cutoff; the runner defaults `start_date` to the model cutoff
(so only post-cutoff problems load) unless the pin sets an explicit
`start_date`. If the cutoff is `"unknown"`, the posture is **VOID** (the runner
warns and the REPORT says so). Cutoff + confidence + posture go in the ledger
`notes`. For `Qwen3.6-35B-A3B` the table gives `2026-04-15`.

## The date-window "pin"

`docs/benchmarks/pins/livecodebench-window.txt` (parsed with `pins.kv`):
`release_version` (e.g. `release_latest`) and optional `start_date`/`end_date`.
The **same** window is used for L1 and L2; the resolved problem count is recorded
in `pin.n_items`.

## Setup

```bash
cd benchmarks/livecodebench
bash setup.sh          # clone + isolated .venv (lean; no torch/vllm)
```

## Run

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local
export BENCH_MODEL=Qwen3.6-35B-A3B BENCH_PRECISION="IQ4_XS+q8_0kv"
export BENCH_T1_FROGGERIC=0 BENCH_T2_REASONING_BUDGET=0 BENCH_T3_KV=q8_0

.venv/bin/python run_livecodebench.py --arm L2                    # full post-cutoff window
.venv/bin/python run_livecodebench.py --arm L2 --limit 5 --no-think   # fast smoke
```

`--repeats 3` is recommended for a headline (small windows are variance-prone).
Grading executes untrusted solutions via LCB's `codegen_metrics` **inside**
`bench-sandbox.sh` (one invocation, no network, `/dev/shm` for its
multiprocessing). Appends a ledger entry (`benchmark=livecodebench`,
`score.metric=pass@1`) + writes a REPORT.

## Smoke checks

- **Offline grading (no model):** `.venv/bin/python run_livecodebench.py
  --smoke-grade` grades a wrong solution for 2 problems in the sandbox and
  expects pass@1==0.0 — proves the LCB grader + sandbox plumbing.
- **Live (L2):** `--arm L2 --limit 5 --no-think` for a quick end-to-end pass.

## Note on window size + generation speed

Pick the smallest recent post-cutoff window that yields ~50–100 problems. On the
reference RTX 3080 config (Qwen3.6-35B-A3B IQ4_XS, `n-cpu-moe=40`, ~10 tok/s)
with reasoning on, each solution costs thousands of thinking tokens → minutes
per problem; size the run with the doc-00 calibration probe + abort rule.
