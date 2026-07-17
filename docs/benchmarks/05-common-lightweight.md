# 05 — Common infrastructure for the lightweight single-turn coding panel

Status: DESIGN.

## Dependency chain

- **Depends on:** `00-common-infrastructure.md` (bench profile, ledger +
  checker, calibration, hygiene, pin convention).
- **Prerequisite for:** `06` (LiveCodeBench), `07` (IFBench), `08` (EvalPlus),
  `09` (CRUXEval). Once this doc's three deliverables (D1/D2/D3) exist, 06–09
  can be built in any order or in parallel; `07` needs only D1+D3 (no D2).
- Does NOT redefine anything from doc 00; if a conflict seems to arise, doc 00
  wins.

## External dependencies (require user input)

- **Reference endpoint + API key** (L1 arms of 06–09 only) — per doc 00;
  user supplies endpoint URL + model id + explicit precision label (no vendor
  certification). L2 (local) arms need no user input beyond a running
  `llama-server`.
- **Model training-data cutoff** — looked up from `docs/benchmarks/model-cutoffs.toml`
  via `lookup_cutoff.py` (doc 00 §Model cutoff table). Needed by doc 06
  (LiveCodeBench) to pick a post-cutoff date window. If the model is missing
  from the table, `lookup_cutoff.py` **crashes with instructions** on how to
  add it — it does not fall back silently. The resolved cutoff is recorded in
  the ledger `notes`.

## What requires implementation (summary)

| Deliverable                              | Kind                                    | Status                                                                                        |
| ---------------------------------------- | --------------------------------------- | --------------------------------------------------------------------------------------------- |
| D1 endpoint plumbing                     | pure convention (env vars), **no code** | documented here + in 06–09; needs a one-line smoke call against L2/L1 (runtime)               |
| D2 `benchmarks/scripts/bench-sandbox.sh` | **new script**                          | **IMPLEMENTED** — `benchmarks/scripts/bench-sandbox.sh` exists; D2 smoke tests pass (see §D2) |
| D3 ledger benchmark names                | one-line edit to `check_ledger.py`      | **DONE** — the repo's `results/check_ledger.py` already lists all six names                   |

Prerequisite for: `06-livecodebench`, `07-ifbench`, `08-evalplus`, `09-cruxeval`.

## Why this doc exists (and what it is NOT)

Docs 06–09 cover four community benchmarks that are all **single-turn,
deterministically graded, Docker-free, and cheap to run**: LiveCodeBench,
IFBench, EvalPlus (HumanEval+/MBPP+), and CRUXEval. They share a small amount
of setup that must not be re-implemented four times. That shared setup is
**this doc**.

This doc does **not** redefine anything already in doc 00. Doc 00 owns:

- the user-designated reference endpoint (precision explicitly labelled) and
  the local server profile (whatever `models.ini` preset you load; record its
  name in `model.preset` and any non-default tweaks in `model.config`, doc 00)
  incl. sampling, MTP, toggles T1/T2/T3, serving rules;
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

| Arm                   | Meaning here                                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------------------------------- |
| `L1-remote-canonical` | User-designated reference endpoint (precision labelled), the benchmark's native harness. Reference capability. |
| `L2-local-canonical`  | Local bench preset (`models.ini`), the same native harness. Local-stack delta vs L1.                           |

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
export BENCH_MODEL="Qwen3.6-35B-A3B"   # your models.ini preset; record in ledger model.preset

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

### D2 — Untrusted-code execution sandbox (`benchmarks/scripts/bench-sandbox.sh`)

Three of the four benchmarks **execute model-generated code** to grade it
(LiveCodeBench, EvalPlus, and the input-prediction half of CRUXEval). That
code is untrusted and must not run naked on the host. IFBench does **not**
execute model code and therefore does not depend on this deliverable.

The repo already ships bubblewrap + an AppArmor profile (`pixi run
install-apparmor`) for the pi/claude sandboxes. Reuse it. Contract for a
shared `benchmarks/scripts/bench-sandbox.sh <cmd…>` wrapper:

- read-only root (entire host visible read-only, incl. the harness venv +
  repo, so the graded command can read inputs by their host absolute path);
  a fresh **tmpfs** (or the `--stage` dir bind-mounted) at `/tmp` is the
  **only writable area** and the sandboxed process's working directory —
  `/tmp` is used (not `/sandbox`) because it always exists on the host so
  bwrap can mount over it without creating a mountpoint on the read-only
  root. The harness **stages the model-generated snippet + any test inputs**
  into the `--stage` dir before invoking (it appears as `/tmp` inside), runs
  `<cmd…>` there, and reads any result files back out of the dir after;
  `$PWD` (the harness's own tree) is read-only via the root bind, so the
  sandboxed step cannot mutate it — all writes land in `/tmp`;
- **no network** (`--unshare-all`, no `--share-net`) — code grading must never
  reach out;
- resource caps: wall `timeout` per process (harness passes its own per-task
  limit), plus `ulimit -v`/`-t` memory+CPU guards;
- `--die-with-parent`; non-zero exit propagates as a test failure;
- same AppArmor profile as `bwrap-pi.sh` (no new profile).

**Implemented:** `benchmarks/scripts/bench-sandbox.sh` exists and the D2 smoke tests
(see Pass criteria) pass. Usage:
`bench-sandbox.sh [--stage <dir>] [--time <s>] [--mem <KB>] [--cpu <s>] -- <cmd…>`.
Inside the sandbox the writable workspace is `/tmp` (fresh tmpfs, or the
`--stage` dir); harnesses pass in-sandbox paths as `/tmp/<file>`.

Implementations may instead use each harness's _own_ container/sandbox flag if
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

That one-line change is the _only_ edit to shared code this panel requires; the
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
  both L1 and L2, because the project measures the _deployed_ stack, not an
  idealized greedy decode. Consequence: pass@1 has run-to-run variance.
- **Leaderboard-parity caveat:** public LiveCodeBench/EvalPlus/CRUXEval numbers
  are almost always **greedy (temp 0) pass@1**. Each REPORT must state that our
  default is sampled, not greedy, when comparing. An optional **greedy toggle**
  (temp 0 via the harness's generation config) is available for a closer apples-
  to-apples number; if used, record it in the ledger `toggles`.
- **Repeats:** default **1** (doc 00). For these _fast_ benchmarks, bumping to
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
- IFBench — strong by design (held-out OOD constraints), but it is _not_ a
  coding score (doc 07).

## Selection / pins

Per doc 00 §Subset pins. These benchmarks are cheap, so the **default is the
full suite** for IFBench, EvalPlus, and CRUXEval; LiveCodeBench's "pin" is a
date window, not an ID list. Pin files live under `pins/` and each doc names
its own. The same pin/window state is used for both arms (L1 and L2) of a
given benchmark.

## Optional: combined panel report

After any subset of 06–09 is run, an optional `REPORT-lightweight-panel.md` may
tabulate L1 vs L2 for whichever benchmarks have completed, one row per arm with
the doc-00 one-line summary (`<URL> / <model> -> <score>  (mean <mean_s>s/item,
n=<N>)`). It is **not** a prerequisite for any single benchmark and imposes no
ordering — unlike the SciCode ladder report (doc 03), each benchmark here stands
alone.

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
- [x] D2: `benchmarks/scripts/bench-sandbox.sh` runs `python -c "print(2+2)"` → exit 0,
      and runs a snippet that opens a TCP socket → non-zero/blocked
      (`OSError: Network is unreachable` under `--unshare-all`). The script is
      bwrap-only — zero Docker dependency (verified by inspection: no `docker`
      calls), so it works with the Docker daemon stopped (the explicit
      stop-Docker scope-guard check is a doc-00 runtime item). **Verified
      2026-06-17.**
- [ ] D3: `BENCHMARKS` in `check_ledger.py` includes the four panel names and a
      dummy entry for each validates.
- [ ] The four harness envs install independently (no shared-env dependency
      conflict); each records its pin in the ledger.
