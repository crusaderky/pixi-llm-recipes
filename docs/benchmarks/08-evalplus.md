# 08 — EvalPlus (HumanEval+ / MBPP+, single-turn)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00` (bench profile, ledger, calibration, hygiene),
  `05` (deliverables **D1 + D2 + D3**).
- **Independent of:** docs 01–04, 06, 07, 09.
- **Unblocks:** nothing (leaf).

## External dependencies (require user input)

- **Reference endpoint + API key** (L1 only) — per doc 00; user supplies
  endpoint URL + model id + explicit precision label (no vendor certification).
- **EvalPlus harness version pin** — pin the `evalplus` package version; record
  in `harness.version`.

Implements arms **L1-remote-canonical** and **L2-local-canonical** for
EvalPlus. Role in the panel: a **cheap capability floor**, not a headline
number (see §Read this first).

## Read this first — what EvalPlus is for here

HumanEval (164 problems) and MBPP (sanitized 378) are the canonical "write a
function from a docstring" benchmarks. **Plain HumanEval is saturated and
contaminated** — frontier models exceed 95% and the problems are all over the
training data. EvalPlus rescues them by adding **~80× more test cases**
(HumanEval+ / MBPP+), which catches subtly-wrong solutions that pass the
original sparse tests. We therefore **headline the `+` numbers**, never the
base ones.

Even so, treat EvalPlus as a **sanity floor**: if L2 craters here, the local
stack is broken (bad template, KV corruption, server misconfig) — it is a
tripwire, not a differentiator. The headline capability/quant signal lives in
LiveCodeBench (doc 06).

`inspect_evals` has `humaneval`/`mbpp`, **but those use the original sparse
tests** — not the `+` sets that give EvalPlus its value. Use the **upstream
`evalplus` harness** so the `+` tests are applied.

## Setup (isolated env — doc 05 §Harness isolation)

```bash
uv venv .venv-evalplus && . .venv-evalplus/bin/activate
uv pip install "evalplus[vllm]"   # pin version; record in ledger. (vllm extra
                                  # optional — we generate via the OpenAI endpoint.)
```

EvalPlus generates samples via a backend and then runs `evalplus.evaluate`,
which **executes every sample against the augmented tests**. That execution is
untrusted and **must run through `benchmarks/scripts/bench-sandbox.sh` (doc 05 D2)** — or
use EvalPlus's own isolation flags as long as they meet D2's no-network +
resource-cap contract and pull in **no Docker** (doc 00 scope guard).

## Selection / pin

Both datasets run in full (164 + 378) in minutes, so the **default is the full
suite for both**, recorded in `pins/evalplus-full.txt` (with an optional first-N
cap for smoke). Run **HumanEval+ and MBPP+ as two separate ledger entries**
(different `score.raw`, same `benchmark=evalplus`, distinguished by a
`dataset` note) so each is independently reportable. Same selection for L1/L2.

## Running

### L2 — local canonical (HumanEval+ shown; repeat with `--dataset mbpp`)

```bash
pixi r -e llamacpp-source-cuda start-server     # your bench preset (models.ini)
export OPENAI_BASE_URL="http://localhost:8080/v1" OPENAI_API_KEY="sk-local"
export BENCH_MODEL="Qwen3.6-35B-A3B"             # your models.ini preset; record in ledger model.preset

# 1) generate
evalplus.codegen --dataset humaneval --backend openai \
  --base-url $OPENAI_BASE_URL --model $BENCH_MODEL \
  --temperature 0.6 --greedy false --root ./ep-out
# 2) grade (untrusted execution -> bench-sandbox.sh)
benchmarks/scripts/bench-sandbox.sh evalplus.evaluate --dataset humaneval \
  --samples ./ep-out/...jsonl
```

- May use `--parallel 4` server-side. Default **1 repeat**.
- `score.metric = "pass@1_plus"` (headline). Also capture base `pass@1` in
  `score.raw` to show the base-vs-plus gap.

### L1 — reference endpoint (precision labelled)

Same two steps with the remote endpoint vars and `BENCH_MODEL=Qwen3.6-35B-A3B`;
3 repeats. Record the endpoint's precision label in the ledger (doc 00); no
vendor certification required.

## Sampling note (matters more here)

Public EvalPlus leaderboard numbers are **greedy pass@1**. Our default is
sampled (doc 05 §Sampling), which depresses pass@1 slightly and adds variance.
Because EvalPlus is the _floor_ benchmark, running the **greedy toggle** here is
reasonable for the closest leaderboard parity — if used, set `--greedy true`
and record `toggles.greedy=true` in the ledger. State the choice in the REPORT.

## Wall-time plan

Cheapest in the panel after IFBench: 542 short single-turn generations total +
fast augmented-test execution. Run the doc 00 calibration probe for the record;
the abort rule will not realistically trigger. Generation length (T2 OFF) is the
only cost driver.

## Pass criteria

Smoke-level:

- [ ] One-problem generate→`bench-sandbox.sh` evaluate cycle on HumanEval
      produces a parseable base + plus verdict.

Arm-complete:

- [ ] L2 produces HumanEval+ and MBPP+ `pass@1_plus` (two ledger entries) within
      budget; ledger (`benchmark=evalplus`) + `REPORT`.
- [ ] L1 produces the same two numbers.
- [ ] `REPORT` tabulates base vs plus pass@1 for both datasets at L1 and L2
      with each arm's one-line summary (doc 00: `<URL> / <model> -> <pass@1_plus>
      (mean <mean_s>s/problem, n=<N>)`), compares to the **EvalPlus leaderboard**
      (greedy) noting our sampling choice, and explicitly frames EvalPlus as a
      floor/tripwire. No numeric gate (Q8).

Sanity (narrative):

- [ ] At L1 (at the labelled precision) HumanEval+ should be high (this model
      class clears the base
      set easily); a low L1 number means a harness/template bug, not model
      weakness — investigate before trusting L2.
- [ ] L2 ≤ L1 expected and likely small (these are easy problems); a _large_
      L2 drop on such easy tasks is a strong red flag for the local stack and
      should be chased down.

## Notes / gotchas

- Always report `+` (augmented) numbers as the headline; base numbers exist only
  to show the gap and to sanity-check against historical scores.
- Keep generation prompt format identical across arms.
- EvalPlus's own multiprocessing executor can be pointed through the sandbox or
  wrapped wholesale; whichever, verify no-network holds (doc 05 D2 acceptance).
