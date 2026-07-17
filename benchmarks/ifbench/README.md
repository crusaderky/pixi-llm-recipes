# IFBench (design doc 07) — instruction-following, single-turn

**Instruction-following, NOT coding.** IFBench measures whether a model obeys
precise, verifiable output constraints on held-out OOD WildChat prompts. It is
in the panel as a _reliability_ probe (constraint/format adherence is what
quantization degrades early and what breaks tool/edit-format compliance in the
pi arms), orthogonal to coding capability. Every REPORT labels it as such.

- Harness: upstream [`allenai/IFBench`](https://github.com/allenai/IFBench),
  pinned at commit `1091c4c3de6c1f6ed12c012ed68f11ea450b0117`.
- Arms: **L2-local-canonical** (runnable) and **L1-remote-canonical** (wired,
  needs a reference endpoint — set `OPENAI_*`/`BENCH_MODEL` at it).
- No exec sandbox (D2), no Docker: the verifiers run in-process.
- Metric: headline `prompt_acc_strict`; the ledger `score.raw` records all four
  (`prompt`/`instruction` × `strict`/`loose`). Test set: 300 held-out OOD
  prompts (`data/IFBench_test.jsonl`); the IFTrain constraints are NOT used.

## Setup

```bash
cd benchmarks/ifbench
bash setup.sh          # isolated .venv, pinned IFBench + openai, nltk resources
```

## Run

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local
export BENCH_MODEL=Qwen3.6-35B-A3B BENCH_PRECISION="IQ4_XS+q8_0kv"
# Toggles the runner can't see from the API — set to match your models.ini:
export BENCH_T1_FROGGERIC=0 BENCH_T2_REASONING_BUDGET=0 BENCH_T3_KV=q8_0

.venv/bin/python run_ifbench.py --arm L2                 # full set (300 prompts)
.venv/bin/python run_ifbench.py --arm L2 --limit 20      # quick smoke
.venv/bin/python run_ifbench.py --arm L1                 # reference endpoint
```

Generation is sequential by default for clean per-item timing (prompts are
short; the full set is minutes). `--repeats N` averages N passes. `--max-tokens`
bounds the completion (reasoning content is separate; we grade `message.content`).

Each run appends one entry to `docs/benchmarks/results/runs.jsonl`
(`benchmark=ifbench`, `score.metric=prompt_acc_strict`) and writes
`docs/benchmarks/results/REPORT-<run_id>.md`. Responses are saved under `out/`.

## Smoke checks

- **Offline plumbing (no model):** `.venv/bin/python run_ifbench.py --selftest`
  fakes responses, grades them with the real verifiers, and validates a ledger
  entry — proves generate→grade→ledger without the server.
- **Live (L2):** `--arm L2 --limit 2` against the running llama-server.

## Contamination posture

Strong by design (held-out OOD constraints with fresh verifier functions), but
this is an instruction-following score, not a coding score — read it as a
format-reliability signal on the L1→L2 local-stack delta. The paper
(arXiv:2507.02833) reports frontier models <50%.
