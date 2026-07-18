# SciCode (design docs 01 canonical + 03 pi)

Single-shot scientific code generation: 65 test problems / 288 subproblems, one
Python function per subproblem with earlier solutions fed forward. Headline
metric: **subproblem pass rate**. Run `with_background=True` to match the
Artificial Analysis reference protocol. Grading is identical across all arms —
the pi arms (doc 03) **reuse the canonical evaluator** (`ScicodeEvaluator`), only
generation differs.

- Harness: `scicode-bench/SciCode` pinned at `e3158ea` + its inspect_ai
  integration. Numeric tests: `test_data.h5` (~1 GB, fetched by setup.sh via
  gdown; sha256 `48b0272a…`).
- Arms: **L1/L2** canonical (`run_canonical.py`), **L3/L4/L5** through pi
  (`run_pi.py`). Same pin across all so L2↔L3↔L4↔L5 share one yardstick.

## Setup

```bash
cd benchmarks/scicode
bash setup.sh          # clone + venv (lean) + fetch test_data.h5 (~1 GB)
```

## Canonical (doc 01) — L1 / L2 via inspect_ai

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local
export BENCH_MODEL=Qwen3.6-35B-A3B BENCH_PRECISION="IQ4_XS+q8_0kv"
export BENCH_T1_FROGGERIC=0 BENCH_T2_REASONING_BUDGET=0

.venv/bin/python run_canonical.py --arm L2                 # pinned subset (28 problems)
.venv/bin/python run_canonical.py --mode dummy --limit 1   # plumbing smoke (no LLM; still scores)
```

Honors `pins/scicode-subset.txt` via an explicit problem-ID allowlist and
**errors on pin/dataset drift** (a pinned ID not in the test split) — `--limit`
alone would take the first N. `--max-connections N` maps to N llama-server
`--parallel` slots. Score → `subproblem_pass@1` in the ledger + REPORT (compared
to the AA SciCode number, noting subset + precision differences).

> The pin was **reconciled to the actual test split on 2026-07-18** (the draft
> had validation-split IDs the runner correctly rejected). Default = 28-problem
> even spread; uncomment the rest for the full 65.

## Through pi (doc 03) — L3 / L4 / L5

Requires `pi` on PATH (the repo `agents` env) pointed at the local server.

```bash
.venv/bin/python run_pi.py --arm L3 --limit 3    # bare: --no-tools (V3), single-shot
.venv/bin/python run_pi.py --arm L4              # default tools (read/write/edit/bash)
.venv/bin/python run_pi.py --arm L5              # + ONLY pi-caveman + rtk
```

**V3 resolved** via pi's CLI: L3 = `pi -p --no-tools --no-extensions` (zero tool
calls guaranteed by the flag). L4 keeps default tools, no extensions. L5 adds the
doc-03 KEEP set (`pi-caveman`, `rtk`) only; every other deployed extension is
DROP, enforced by `--no-extensions`. Token totals come from `llama-server.log`
(doc 03), not a pi extension. Each arm reuses `ScicodeEvaluator` for scoring; a
combined `REPORT-scicode-ladder.md` tabulates L1–L5.

## Smoke checks

- **Canonical plumbing (no LLM):** `run_canonical.py --mode dummy --limit 1`
  (model=mockllm; still scores via test_data.h5) — proves the inspect_ai harness.
- **Pin filter** (validated at implementation): the 28 default IDs all resolve to
  test-split problems; a bogus ID raises (error-on-drift).

## Note on generation speed

The pi arms and L2 issue long reasoning traces; on the reference RTX 3080 config
(~10 tok/s) a full 28-problem × multi-subproblem run is multi-hour. Use the
doc-00 calibration probe + abort rule, and `--parallel` (canonical) to fan out.
