# 05 — Common infrastructure for the lightweight single-turn coding panel

Status: DESIGN. Depends on: `00-common-infrastructure.md`.
Prerequisite for: `06-livecodebench`, `07-ifbench`, `08-evalplus`, `09-cruxeval`.

## Why this doc exists (and what it is NOT)

Docs 06–09 cover four community benchmarks that are all **single-turn,
deterministically graded, Docker-free, and cheap to run**: LiveCodeBench,
IFBench, EvalPlus (HumanEval+/MBPP+), and CRUXEval. They share a small amount
of setup that must not be re-implemented four times. That shared setup is
**this doc**.

This doc does **not** redefine anything already in doc 00. Doc 00 owns:

- the remote BF16/FP16 anchor endpoint and the local `[Qwen3.6-35B-A3B-bench]`
  server profile (incl. sampling, MTP, toggles T1/T2/T3, serving rules);
- the run ledger (`results/runs.jsonl`) schema and `results/check_ledger.py`;
- the calibration-probe + 4 h abort rule;
- benchmark hygiene (no web, no advisor, unmanned-run rules);
- the subset-pin file convention.

If something here seems to contradict doc 00, doc 00 wins; open an issue.

## Relationship to the canonical ladder (reduced)

These four benchmarks are **single-shot capability** tests. The pi agentic
arms (L3/L4/L5) from docs 01/03 are **not meaningful** here — there is no
multi-turn task, no tool use, nothing for an agent loop to do — so this panel
runs **only two arms**:

| Arm | Meaning here |
|-----|--------------|
| `L1-remote-canonical` | Remote BF16/FP16 anchor, the benchmark's native harness. Reference capability. |
| `L2-local-canonical`  | Local `Qwen3.6-35B-A3B-bench`, the same native harness. Local-stack delta vs L1. |

No new arm IDs are introduced; the existing `check_ledger.py` `ARMS` set
already permits L1/L2. The two arms are independent: L2 is free, runs first;
L1 can be run any time a remote endpoint is available. Neither blocks the
other and neither blocks a different benchmark.

## The three shared deliverables (each independently implementable)

The whole point of this doc is that **once these three exist, docs 06–09 can
be built in any order or in parallel.** They are also independent of each
other.

### D1 — OpenAI-endpoint plumbing convention

Every harness in this panel talks to an OpenAI-compatible `/v1` endpoint.
Standardize on the same two-variable switch already used in doc 01:

```bash
# L2 local
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="sk-local"            # llama-server ignores the value
export BENCH_MODEL="Qwen3.6-35B-A3B-bench"  # served preset name

# L1 remote anchor
export OPENAI_BASE_URL="https://<vendor>/v1"
export OPENAI_API_KEY="…"
export BENCH_MODEL="Qwen3.6-35B-A3B"
```

Each harness's "model" argument is `openai/$BENCH_MODEL` (or the harness's own
spelling of an OpenAI-compatible model id). Sampling defaults to the deployed
values from doc 00 (temp 0.6 / top-p 0.95 / top-k 20 / min-p 0); see
**§Sampling** below for the leaderboard-parity caveat. This is pure
convention — no code — and is shared verbatim by all four docs.

### D2 — Untrusted-code execution sandbox (`scripts/bench-sandbox.sh`)

Three of the four benchmarks **execute model-generated code** to grade it
(LiveCodeBench, EvalPlus, and the input-prediction half of CRUXEval). That
code is untrusted and must not run naked on the host. IFBench does **not**
execute model code and therefore does not depend on this deliverable.

The repo already ships bubblewrap + an AppArmor profile (`pixi run
install-apparmor`) for the pi/claude sandboxes. Reuse it. Contract for a
shared `scripts/bench-sandbox.sh <cmd…>` wrapper:

- read-only root; a fresh **tmpfs** as the only writable area; `$PWD` bound
  read-only (the harness writes results outside the sandbox, the sandboxed
  step only *executes*);
- **no network** (`--unshare-all`, no `--share-net`) — code grading must never
  reach out;
- resource caps: wall `timeout` per process (harness passes its own per-task
  limit), plus `ulimit -v`/`-t` memory+CPU guards;
- `--die-with-parent`; non-zero exit propagates as a test failure;
- same AppArmor profile as `bwrap-pi.sh` (no new profile).

Implementations may instead use each harness's *own* container/sandbox flag if
it provides one (EvalPlus has execution-isolation options), **provided** the
no-network + resource-cap properties above hold and Docker is not pulled in
(doc 00 scope guard: day-to-day `pixi run pi` must work with the Docker daemon
stopped — so do **not** satisfy this with Docker). Record the chosen mechanism
in the ledger `notes`.

Acceptance for D2 is a 2-snippet smoke test (see Pass criteria): a known-good
snippet exits 0, a snippet that tries to open a socket fails.

### D3 — Ledger benchmark-name extension

`results/check_ledger.py` currently allows `benchmark ∈ {scicode, tb2}`. Extend
the `BENCHMARKS` set to add the four names used by this panel:

```python
BENCHMARKS = {"scicode", "tb2",
              "livecodebench", "ifbench", "evalplus", "cruxeval"}
```

That one-line change is the *only* edit to shared code this panel requires; the
arm/score/required-field validation is unchanged. (This repo's copy of
`check_ledger.py` already carries this extension — see the file.) Each
benchmark doc specifies the `score.metric` string it writes.

## Harness isolation (so the four don't couple)

Do **not** install all four harnesses into one Python environment — their
pins conflict (e.g. `livecodebench`, `evalplus`, `crux-eval`, and IFBench's
deps disagree on `datasets`/`transformers`/`vllm` ranges). Instead, **each
benchmark gets its own isolated env** (a `uv` tool/venv or a dedicated throw-
away venv), created and pinned at implementation time and recorded in the
ledger `harness.version`. This mirrors doc 02's "harbor in its own env" choice
and is what makes the four docs installable independently and in parallel.

The shared `agents`/`pytools` pixi env is used only for the tiny OpenAI smoke
calls and the ledger checker — never for the benchmark harnesses themselves.

## Sampling and repeats (panel-wide)

- **Default: deployed sampling** (doc 00, decision Q10) — temp 0.6 etc. — for
  both L1 and L2, because the project measures the *deployed* stack, not an
  idealized greedy decode. Consequence: pass@1 has run-to-run variance.
- **Leaderboard-parity caveat:** public LiveCodeBench/EvalPlus/CRUXEval numbers
  are almost always **greedy (temp 0) pass@1**. Each REPORT must state that our
  default is sampled, not greedy, when comparing. An optional **greedy toggle**
  (temp 0 via the harness's generation config) is available for a closer apples-
  to-apples number; if used, record it in the ledger `toggles`.
- **Repeats:** default **1** (doc 00). For these *fast* benchmarks, bumping to
  **3** for a headline number is cheap and recommended where variance matters
  (LiveCodeBench small date-windows especially). Remote L1 defaults to 3.

## Contamination handling (panel-wide)

The reason this panel earns its keep is isolating the **L1→L2 local-stack
delta**, which only means something if the score isn't dominated by training-
set memorization. Every REPORT records the model's **training-data cutoff** (in
the ledger `notes`) and states each benchmark's contamination posture:

- LiveCodeBench — strong: select a contest **date window after the cutoff**
  (doc 06). This is the panel's cleanest quant-delta signal.
- CRUXEval / EvalPlus — moderate/weak: derived from public code; treat as
  capability + relative L1↔L2 delta, not as contamination-free absolutes.
- IFBench — strong by design (held-out OOD constraints), but it is *not* a
  coding score (doc 07).

## Selection / pins

Per doc 00 §Subset pins. These benchmarks are cheap, so the **default is the
full suite** for IFBench, EvalPlus, and CRUXEval; LiveCodeBench's "pin" is a
date window, not an ID list. Pin files live under `pins/` and each doc names
its own. The same pin/window state is used for both arms (L1 and L2) of a
given benchmark.

## Optional: combined panel report

After any subset of 06–09 is run, an optional `REPORT-lightweight-panel.md` may
tabulate L1 vs L2 for whichever benchmarks have completed. It is **not** a
prerequisite for any single benchmark and imposes no ordering — unlike the
SciCode ladder report (doc 03), each benchmark here stands alone.

## Dependency graph (what unblocks what)

```
doc 00 ──┐
         ├─► doc 05 ─┬─ D1 endpoint plumbing ─┐
         │           ├─ D2 exec sandbox ──────┼─► 06 LiveCodeBench  (needs D1,D2,D3)
         │           └─ D3 ledger names ──────┼─► 08 EvalPlus       (needs D1,D2,D3)
         │                                     ├─► 09 CRUXEval       (needs D1,D2,D3)
         │                                     └─► 07 IFBench        (needs D1,D3 only — NOT D2)
```

- D1, D2, D3 are independent of one another and can be built in any order.
- 06/08/09 are independent of one another (separate envs, separate datasets,
  separate ledger entries, separate REPORTs).
- **07 (IFBench) can be implemented before the sandbox (D2) exists**, because
  it executes no model code.
- Within every benchmark, L2 (local, free) and L1 (remote) are independent and
  may be run out of order.

## Pass criteria for this document (doc 05)

- [ ] D1: the `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`BENCH_MODEL` convention is
      documented in each of 06–09 and a one-line OpenAI completion smoke call
      succeeds against both L2 and (when available) L1.
- [ ] D2: `scripts/bench-sandbox.sh` runs `python -c "print(2+2)"` → exit 0,
      and runs a snippet that opens a TCP socket → non-zero/blocked, with the
      Docker daemon stopped (scope guard intact).
- [ ] D3: `BENCHMARKS` in `check_ledger.py` includes the four panel names and a
      dummy entry for each validates.
- [ ] The four harness envs install independently (no shared-env dependency
      conflict); each records its pin in the ledger.
