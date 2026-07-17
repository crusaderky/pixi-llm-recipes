# 06 — LiveCodeBench (canonical, single-turn)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00` (bench profile, ledger, calibration, hygiene),
  `05` (deliverables **D1 + D2 + D3**).
- **Independent of:** docs 01–04, 07, 08, 09 (own isolated env, own pin, own
  ledger entries, own REPORT).
- **Unblocks:** nothing (leaf).

## External dependencies (require user input)

- **Reference endpoint + API key** (L1 only) — per doc 00; user supplies
  endpoint URL + model id + explicit precision label (no vendor certification).
- **Model training-data cutoff** — resolved from `docs/benchmarks/model-cutoffs.toml`
  via `lookup_cutoff.py` (doc 00). The LiveCodeBench date window START must be
  after this cutoff or the contamination posture is void. **If the model is
  missing from the table, `lookup_cutoff.py` crashes (exit 2) with instructions
  to add it** — fix the table, do not bypass. If the entry's `cutoff` is
  `"unknown"`, the posture is void by default (warn, record in the REPORT).
  The resolved cutoff + confidence is recorded in the ledger `notes`.
- **LiveCodeBench harness version + dataset `release_version` pin** — pin both
  together at implementation time (LCB grows over time, "latest" is not
  reproducible); record in `harness.version` and `pin`.

Implements arms **L1-remote-canonical** and **L2-local-canonical** for
LiveCodeBench code-generation. This is the panel's **primary quant-delta
signal** because its contamination control is the strongest (doc 05
§Contamination).

## What LiveCodeBench is, precisely

A contamination-resistant coding benchmark that continuously harvests fresh
problems from competitive-programming sites (LeetCode, AtCoder, Codeforces),
each stamped with the contest **release date**. Multiple scenarios exist
(code generation, self-repair, test-output prediction, code execution); **we
run `code_generation` only**. Each problem ships public + hidden tests; the
model emits a single program, which is **executed against the tests** — a
problem passes iff all tests pass within the time limit. Metric: **pass@1**
(pass@k available).

The date stamp is the whole point: selecting problems whose contest date is
**after the model's training cutoff** makes the score memorization-free, so the
L1→L2 gap is attributable to the local stack rather than recall.

Not in `inspect_evals`: the registry's `livecodebench_pro` is a _different_,
olympiad-grade benchmark. Use the **upstream `livecodebench` harness**.

## Setup (isolated env — doc 05 §Harness isolation)

```bash
uv venv .venv-lcb && . .venv-lcb/bin/activate
uv pip install livecodebench         # pin the version; record in ledger
# dataset (HF: livecodebench/code_generation_lite) is pulled on first run;
# record the dataset revision/release_version in the ledger.
```

The harness invokes generation through an OpenAI-compatible endpoint and runs
grading locally. **All code execution must go through `benchmarks/scripts/bench-sandbox.sh`
(doc 05 D2)** — wire the harness's test-runner subprocess through it, or run the
whole `evaluate` step inside it. No network during grading.

## The date-window "pin"

LiveCodeBench's subset is a **date window**, not an ID list, so its pin file
(`pins/livecodebench-window.txt`) records a `release_version`/date range and
the model cutoff it must sit after. The harness exposes this as
`--release_version release_vN` and/or `--start_date/--end_date`; the exact flag
names are pinned at implementation time and the resolved problem count is
recorded in the ledger `pin.n_items`. The **same window** is used for L1 and L2.

**Cutoff resolution (mandatory pre-check):** before running, the runner calls
`docs/benchmarks/lookup_cutoff.py $BENCH_MODEL` to get the model's cutoff. If
the model is not in `model-cutoffs.toml`, the lookup **crashes (exit 2) with
instructions to add it** — add the entry and re-run; do not bypass. The window
START must be `> cutoff`; if `cutoff == "unknown"` the contamination posture is
void (the REPORT must say so). Record the cutoff + confidence in the ledger
`notes`.

Default window: the smallest recent window (after cutoff) that yields **~50–100
problems** — enough signal, small enough to fit budget at 3 repeats. Widen by
editing the window in the pin file.

## Running

### L2 — local canonical (bench profile)

```bash
pixi r -e llamacpp-source-cuda start-server     # your bench preset (models.ini)
export OPENAI_BASE_URL="http://localhost:8080/v1" OPENAI_API_KEY="sk-local"
export BENCH_MODEL="Qwen3.6-35B-A3B"             # your models.ini preset; record in ledger model.preset
# representative invocation (confirm exact flags against the pinned version):
python -m livecodebench.runner \
  --model openai/$BENCH_MODEL \
  --scenario code_generation \
  --release_version <window-from-pin> \
  --temperature 0.6 --top_p 0.95 \
  --n 1 \
  --evaluate                                    # grading via bench-sandbox.sh
```

- May use llama-server `--parallel 4` (single-turn requests fit the per-slot
  window comfortably; doc 00 §Serving). The runner warns if any prompt+output
  exceeds 80% of a slot.
- Default **1 repeat**; **3 recommended** for a headline (small windows are
  variance-prone). `score.metric = "pass@1"`.

### L1 — reference endpoint (precision labelled)

```bash
export OPENAI_BASE_URL="https://<vendor>/v1" OPENAI_API_KEY="…"
export BENCH_MODEL="Qwen3.6-35B-A3B"
python -m livecodebench.runner --model openai/$BENCH_MODEL \
  --scenario code_generation --release_version <same-window> \
  --temperature 0.6 --top_p 0.95 --n 3 --evaluate
```

- Same window as L2. 3 repeats (cheap remotely). Record the endpoint's
  precision label in the ledger (doc 00); no vendor certification required.

## Wall-time plan

- Single-turn, so per-item time ≈ one generation + a few seconds of test
  execution — far cheaper than SciCode/TB. The cost driver is generation length
  (reasoning uncapped by default, T2 OFF), not grading.
- Mandatory calibration (doc 00): 3–5 problems end-to-end at the chosen
  concurrency; extrapolate; abort >4 h. A ~75-problem window × 3 repeats should
  sit well under budget; if not, shrink the window (pin) before cutting repeats.

## Pass criteria

Smoke-level (minutes):

- [ ] One-problem run against L2 generates a program, executes it through
      `bench-sandbox.sh`, and writes a parseable pass/fail.
- [ ] `lookup_cutoff.py $BENCH_MODEL` resolves (does not crash); the selected
      window resolves to a non-zero problem count entirely **after the resolved
      model cutoff** (else contamination posture is void; if cutoff is
      `"unknown"`, the REPORT must label the posture void).

Arm-complete:

- [ ] L2 window run finishes within budget; pass@1 recorded; ledger
      (`benchmark=livecodebench`, `score.metric=pass@1`) + `REPORT-<run_id>.md`.
- [ ] L1 window run (3 repeats) finishes; pass@1 recorded.
- [ ] `REPORT` prints the one-line summary for each arm
      (`<URL> / <model> -> <pass@1>  (mean <mean_s>s/problem, n=<N>)`, doc 00),
      states both pass@1 values, the window + problem count + model cutoff, and
      compares to the **LiveCodeBench leaderboard** filtered to the same
      release window (nearest model class if Qwen3.6-35B-A3B is unlisted),
      noting sampled-vs-greedy (doc 05 §Sampling). No numeric gate (Q8).

Sanity (narrative):

- [ ] L2 ≤ L1 expected. Because the window is post-cutoff, a large L1→L2 drop
      is a _clean_ read on local-stack damage (4-bit + q8_0 KV), the headline
      result this benchmark exists to produce.

## Notes / gotchas

- Pin the dataset `release_version` AND the harness version together; LCB grows
  over time, so "latest" is not reproducible — the ledger must let a reader
  reconstruct the exact problem set.
- Keep to `code_generation`; self-repair/test-prediction are separate
  experiments, not part of this arm.
- Some problems carry tight time limits; ensure `bench-sandbox.sh`'s wall cap is
  ≥ the problem's own limit so a slow-but-correct solution isn't failed by the
  sandbox rather than by the grader.
