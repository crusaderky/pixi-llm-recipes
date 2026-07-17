# 09 — CRUXEval (code reasoning, single-turn)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00` (bench profile, ledger, calibration, hygiene),
  `05` (deliverables **D1 + D2 + D3**). The **-O half needs only D1 + D3**
  (string-match grading, no sandbox) and can be done before D2 exists; only
  the **-I half blocks on D2** (it executes model-chosen inputs).
- **Independent of:** docs 01–04, 06, 07, 08.
- **Unblocks:** nothing (leaf).

## External dependencies (require user input)

- **Reference endpoint + API key** (L1 only) — per doc 00; user supplies
  endpoint URL + model id + explicit precision label (no vendor certification).
- **CRUXEval commit pin** — clone github.com/crux-eval/CRUXEval and pin a
  commit; record in `harness.version`.

Implements arms **L1-remote-canonical** and **L2-local-canonical** for
CRUXEval. Role in the panel: a **code-reasoning / execution-semantics** signal
orthogonal to code _generation_ — does the model understand what code _does_,
not just how to write it.

## What CRUXEval is, precisely

800 short, self-contained Python functions, each with one input/output example,
yielding two tasks:

- **CRUXEval-O (output prediction):** given the function and an input, predict
  the output. Graded by **string match** against the known output — **no model
  code executed**.
- **CRUXEval-I (input prediction):** given the function and a target output,
  predict an input that produces it. Graded by **executing the function on the
  model's predicted input** and checking it yields the target — **this half
  requires the execution sandbox**.

Metric: **pass@1** per task (reported separately for -I and -O). Optional CoT
variant exists; pick one mode and keep it fixed across arms (default: **direct,
no CoT**, to match the most-cited leaderboard column).

Not in `inspect_evals` — use the upstream **`crux-eval/CRUXEval`** harness.

## Setup (isolated env — doc 05 §Harness isolation)

```bash
git clone https://github.com/crux-eval/CRUXEval && cd CRUXEval
uv venv .venv-crux && . .venv-crux/bin/activate
uv pip install -r requirements.txt    # pin commit; record in ledger
```

Generation via the OpenAI endpoint (doc 05 D1). **CRUXEval-I grading executes
model-chosen inputs and MUST run through `benchmarks/scripts/bench-sandbox.sh` (doc 05
D2)**; CRUXEval-O grading is pure string match and needs no sandbox. Wire the
harness's executor through the sandbox for the -I pass.

## Selection / pin

Full set is 800 functions × 2 tasks and runs quickly, so the **default is the
full suite** (`pins/cruxeval-full.txt`, with an optional first-N cap for smoke).
Same selection for L1/L2. Run **-I and -O as two separate ledger entries**
(`benchmark=cruxeval`, distinguished by a `task` note) so each is independently
reportable and the sandbox-free -O half can be done before D2 exists if desired.

## Running

### L2 — local canonical

```bash
pixi r -e llamacpp-source-cuda start-server     # your bench preset (models.ini)
export OPENAI_BASE_URL="http://localhost:8080/v1" OPENAI_API_KEY="sk-local"
export BENCH_MODEL="Qwen3.6-35B-A3B"             # your models.ini preset; record in ledger model.preset

# generate (run once per task: output / input)
python -m cruxeval.generate --model openai/$BENCH_MODEL \
  --task output_prediction --temperature 0.6 --top_p 0.95 --out ./crux-O
python -m cruxeval.generate --model openai/$BENCH_MODEL \
  --task input_prediction  --temperature 0.6 --top_p 0.95 --out ./crux-I

# grade: -O is string match (no sandbox); -I executes (sandbox)
python -m cruxeval.evaluate --task output_prediction --samples ./crux-O
benchmarks/scripts/bench-sandbox.sh python -m cruxeval.evaluate \
  --task input_prediction --samples ./crux-I
# (confirm exact entrypoints/flags against the pinned commit)
```

- May use `--parallel 4` server-side. Default **1 repeat**.
- `score.metric = "pass@1"` (one ledger entry per task).

### L1 — reference endpoint (precision labelled)

Same, with remote endpoint vars and `BENCH_MODEL=Qwen3.6-35B-A3B`; 3 repeats.
Record the endpoint's precision label in the ledger (doc 00); no vendor
certification required.

## Wall-time plan

800×2 short single-turn generations; grading is fast (string match for -O,
quick function execution for -I). Comfortably within budget. Run the doc 00
calibration probe for the record. Generation length (T2 OFF) is the only cost
driver; CoT mode (if ever enabled) would roughly double it — another reason to
keep the default direct.

## Pass criteria

Smoke-level:

- [ ] One -O item graded by string match (no sandbox) and one -I item graded by
      `bench-sandbox.sh` execution both produce parseable verdicts.

Arm-complete:

- [ ] L2 produces -I and -O `pass@1` (two ledger entries) within budget; ledger
      (`benchmark=cruxeval`) + `REPORT`.
- [ ] L1 produces the same two numbers.
- [ ] `REPORT` tabulates -I and -O pass@1 at L1 and L2 with each arm's
      one-line summary (doc 00: `<URL> / <model> -> <pass@1>  (mean <mean_s>s/item,
      n=<N>)`), compares to the **CRUXEval leaderboard** (matching the
      CoT/direct column used), and notes sampled-vs-greedy (doc 05 §Sampling).
      No numeric gate (Q8).

Sanity (narrative):

- [ ] L2 ≤ L1 expected. CRUXEval stresses _reasoning about execution_; if the
      local stack degrades reasoning more than surface code-gen, expect the
      L1→L2 drop here to exceed EvalPlus's — a reportable contrast.
- [ ] -O ≥ -I is the usual pattern (output prediction is easier); a reversal is
      worth a note.

## Notes / gotchas

- The -O half needs **no sandbox** — it can be implemented and run before D2
  exists; only the -I half blocks on the sandbox.
- Keep CoT on/off identical across arms and matching the leaderboard column you
  compare against — mixing modes invalidates the comparison.
- CRUXEval is derived from public code (moderate contamination posture, doc 05);
  read it as capability + L1↔L2 delta, not a contamination-free absolute.
