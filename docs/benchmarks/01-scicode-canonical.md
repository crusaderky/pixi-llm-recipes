# 01 — SciCode via the canonical harness (inspect_ai)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00-common-infrastructure.md` (bench profile, ledger schema,
  calibration, hygiene, pin convention).
- **Shares with `03`:** the SciCode dataset, `test_data.h5`, and the scoring
  path — doc 03 reuses this doc's evaluator, it does not reimplement grading.
- **Unblocks:** nothing (leaf); doc 03's scoring bridge depends on this doc's
  evaluator being set up.

Implements ladder arms **L1-remote-canonical** and **L2-local-canonical** for
SciCode. (pi-based SciCode arms L3–L5 are in doc 03.)

## External dependencies (require user input)

- **Reference endpoint + API key** (L1 only) — per doc 00; the user supplies
  an OpenAI-compatible endpoint URL + model id + an explicit precision label
  (any value, including `unknown`; no vendor certification required).
- **SciCode numeric test data (`test_data.h5`)** — fetched via `gdown` (the
  GDrive folder is not actually gated; see §Setup for the one-liner). A human
  runs the `gdown` command and verifies the sha256
  (`48b0272a…`, ~1.05 GB); record it in the ledger. (This same asset is shared
  by doc 03.)

## What SciCode is, precisely

SciCode is single-shot scientific code generation. The test split is 65 main
problems decomposed into 288 subproblems. The model writes a Python function
per subproblem; earlier subproblem solutions are fed forward as context. A
subproblem passes iff its unit tests pass (numeric tests live in a
`test_data.h5` that is fetched via `gdown` — see §Setup). Reported metrics:
**subproblem pass rate** (the one we track) and main-problem resolve rate (all
subproblems of a problem pass). Artificial Analysis reports SciCode with
**scientist-annotated background prompting** and **subproblem scoring** — so
we run `with_background=True` to match their reference (decision: AA is the
SciCode external reference).

The canonical harness is the SciCode repo's `inspect_ai` integration
(`eval/inspect_ai/scicode.py`), which talks to any OpenAI-compatible endpoint
through inspect_ai's `openai/…` provider. This is the shortcut: no custom
harness for the baseline (per the agreed 5 shortcuts).

## Setup

```bash
git clone https://github.com/scicode-bench/SciCode.git
cd SciCode
pip install -e .
# Fetch numeric tests -> eval/data/test_data.h5 (NOT actually gated; gdown
# bypasses the GDrive web "Sign in" prompt via its confirm token):
pip install gdown
python -m gdown --folder "https://drive.google.com/drive/folders/1W5GZW6_bdiDAiipuFMqdUhvUaHIj6-pR" -O eval/data
# drops eval/data/test_data.h5 in place (the folder contains just that file)
sha256sum eval/data/test_data.h5
# expected: 48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890 (~1.05 GB)
```

inspect_ai is invoked from `eval/inspect_ai/`. Relevant flags (verified
against the repo README):

- `--model openai/<name>` and standard OpenAI env vars (`OPENAI_API_KEY`,
  `OPENAI_BASE_URL`) to point at either the remote anchor or local
  llama-server.
- `-T split=test` (the 65-problem set; `validation` is 15).
- `-T with_background=True` (matches AA).
- `-T mode=normal` (real eval; `dummy` calls no LLM — use it for a plumbing
  smoke test; `gold` is validation-only).
- `-T h5py_file=<path>` if `test_data.h5` is not in the default location.
- `--max-connections N` request concurrency.
- `--temperature 0.6` and other sampling via inspect_ai generation config.
- `--limit N` (see "Honoring the pin" — NOT used alone for the subset).

### L1 — reference endpoint (precision labelled)

```bash
export OPENAI_BASE_URL="https://<vendor>/v1"
export OPENAI_API_KEY="…"
inspect eval scicode.py \
  --model openai/Qwen3.6-35B-A3B \
  -T split=test -T with_background=True -T mode=normal \
  --temperature 0.6 --top-p 0.95 \
  --max-connections 8
```

- Run the **full 288 subproblems**, **3 repeats** (cheap remotely). This is
  the anchor; we want it solid.
- Pre-run: precision label recorded in the ledger (doc 00); no vendor
  certification required.
- Sampling: pass what the vendor accepts; record dropped params.

### L2 — local canonical (bench profile)

```bash
pixi r -e llamacpp-source-cuda start-server      # loads your bench preset (models.ini)
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="sk-local"                 # llama-server ignores the value
export BENCH_MODEL="Qwen3.6-35B-A3B"             # your models.ini preset; record in ledger model.preset
inspect eval scicode.py \
  --model openai/$BENCH_MODEL \
  -T split=test -T with_background=True -T mode=normal \
  --temperature 0.6 --top-p 0.95 \
  --max-connections 4                            # => 4 llama-server --parallel slots
```

- Start `llama-server` with `--parallel 4` (edit `start-server.sh` invocation
  or pass through; doc 00 §Serving). Verify V1 (MTP + parallel) during
  calibration; if broken, drop MTP for this run.
- Default **1 repeat**; toggle to 3 for a headline number.
- Use the **pinned subset** by default (`pins/scicode-subset.txt`); toggle to
  full set by uncommenting the pin.

## Honoring the pin

`--limit N` selects the first N problems, which is not our stratified pin. The
runner must restrict to the pinned problem IDs explicitly. Two acceptable
implementations (implementer picks one, documents it):

1. **Sample filter**: a small wrapper that loads the pin file, parses
   uncommented IDs, and passes them to the inspect_ai task as a
   `problem_ids` task parameter (add a thin `-T problem_ids=@pins/scicode-subset.txt`
   handler to a local copy of `scicode.py`; the dataset is filtered before
   sampling). Preferred — deterministic and visible.
2. **Pre-filtered dataset**: materialize a filtered JSONL containing only
   pinned problems and point the task at it.

The runner MUST error (not skip) if a pinned ID is absent from the test split
(e.g. it is a validation-split ID) — this catches pin/dataset drift.

## Wall-time plan

- Binding constraint is L2. 288 subproblems × (8k reasoning cap is OFF by
  default per toggle T2, so generations can be long) → full set is multi-hour
  sequential. Mitigations, in order: pinned subset (~30 problems ≈ ~130
  subproblems), `--parallel 4` (~2–3× aggregate), `1` repeat.
- Mandatory calibration probe (doc 00): run 3–5 subproblems with `mode=normal`
  at the chosen concurrency, measure per-subproblem wall time, extrapolate,
  abort if >4h.
- Note on T2: with the reasoning budget OFF, hard SciCode subproblems may
  produce very long thinking traces and dominate runtime. If calibration
  projects >4h even on the pinned subset at `--parallel 4`, the sanctioned
  responses are (a) shrink the pin further, or (b) turn T2 on for _all_
  compared arms and record it — never turn it on for just one arm.

## Pass criteria (optimized to minimize implementer run time)

Smoke-level (must pass before any real run; ~minutes):

- [ ] `mode=dummy` run completes end-to-end and produces an inspect_ai log —
      proves harness plumbing without spending model time.
- [ ] One-subproblem `mode=normal` run against L2 returns a graded result and
      writes a parseable score.

Arm-complete:

- [ ] L2 pinned-subset run finishes within budget; produces subproblem
      pass@1; ledger entry + `REPORT-<run_id>.md` written.
- [ ] L1 full-set run (3 repeats) finishes; produces subproblem pass@1.
- [ ] `REPORT` for each prints the one-line summary
      `<URL> / <model> -> <subproblem_pass@1>  (mean <mean_s>s/subproblem, n=<N>)`
      (doc 00) and compares the score to the Artificial Analysis SciCode number
      for Qwen3.6-35B-A3B, explicitly noting protocol differences (subset vs
      full; local quant+KV vs the L1 precision label).
      No numeric tolerance gate — the comparison is narrative (decision Q8).

Sanity (narrative, not gates):

- [ ] L1 (at the labelled precision, same model) should land near the AA
      SciCode number for the same model (within a handful of points is
      expected; larger gaps are a finding about prompt-template/provider
      differences, to be noted not failed). Only meaningful when L1 is the
      same model family at a comparable precision.
- [ ] L2 ≤ L1 is the expected direction (local quant + q8_0 KV). If L2 > L1,
      suspect a confound (toggle mismatch, different subset) and investigate.

## Notes / gotchas

- inspect_ai's OpenAI provider expects `OPENAI_BASE_URL` to end in `/v1` with
  nothing appended; llama-server serves the OpenAI routes there.
- llama-server `--parallel` divides the KV pool; with the 262144 ctx that is
  ~64k/slot, ample for SciCode. The runner warns if any request exceeds 80%
  of a slot (doc 00).
- Keep `with_background` identical across all arms — flipping it is a separate
  experiment, not part of the ladder.
