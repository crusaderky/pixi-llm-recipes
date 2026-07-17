# 00 — Common Benchmark Infrastructure

Status: DESIGN — to be implemented before any of docs 01–09.

## Dependency chain

- **Depends on:** nothing (root of the benchmark design tree).
- **Unblocks:** `01`–`04` (heavy ladder), `05` (lightweight panel), and
  transitively `06`–`09` via `05`.
- Docs 01–09 must not redefine anything specified here; if a later doc seems
  to contradict this one, this one wins.

## External dependencies (require user input)

These are **not** implementable from the repo alone; they need a human decision
or off-repo asset before any arm can run. Each later doc inherits them.

- **Reference endpoint (L1)** — at run time the user supplies an arbitrary
  OpenAI-compatible endpoint: `OPENAI_BASE_URL` + `OPENAI_API_KEY` + a model id
  - an **explicit precision label**. The endpoint may be a remote vendor OR a
    second local `llama-server` — anything that speaks `/v1`. **Vendors are NOT
    required to certify their precision**; the precision is whatever the user
    asserts (e.g. `BF16`, `FP16`, `IQ4_XS+q8_0kv`, `unknown`) and is recorded
    verbatim in the ledger `model.precision` and printed in every REPORT. The L1
    arm is therefore "the user-designated reference endpoint, labelled with its
    precision" — not specifically a BF16 vendor. Comparability comes from the
    label being explicit, not from it being a particular value.
- **API key / auth** — for whatever endpoint L1 points at.
- **Model training-data cutoff** — looked up from the **static table**
  `docs/benchmarks/model-cutoffs.toml` via `docs/benchmarks/lookup_cutoff.py`
  (see §Model cutoff table). Needed by the contamination-controlled benchmarks
  (doc 06 LiveCodeBench especially). If the model is missing from the table,
  the lookup **crashes with instructions** on how to add it — it does not fall
  back silently.

## Purpose

Shared contracts for running SciCode and Terminal-Bench 2.0 against the local
llama.cpp stack and against a user-designated reference endpoint, so that
results from different harnesses and different model deployments are
comparable with each other.

## The experiment ladder

Every benchmark is run as a sequence of _arms_. An arm = (model deployment,
harness, harness configuration). The canonical ladder:

| Arm ID                | Model deployment                                                                              | Harness                             | Measures                                                   |
| --------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------- | ---------------------------------------------------------- |
| `L1-remote-canonical` | User-designated reference endpoint (any OpenAI-compatible URL; precision explicitly labelled) | Canonical (inspect_ai / Terminus 2) | Anchor: the reference capability at the labelled precision |
| `L2-local-canonical`  | Local llama-server (bench profile)                                                            | Canonical                           | Local-stack delta vs L1                                    |
| `L3-local-pi-bare`    | Local llama-server (bench profile)                                                            | pi, minimal                         | Harness delta vs L2 (SciCode only; see doc 03 §Bare)       |
| `L4-local-pi-tools`   | Local llama-server (bench profile)                                                            | pi, default tools                   | Agentic-tools delta                                        |
| `L5-local-pi-ext`     | Local llama-server (bench profile)                                                            | pi + extension ladder               | pi-augmented outcome                                       |

Terminology note: L2 is the **local-stack delta**, not "quantization delta".
It conflates at least: 4-bit weights, q8_0 KV cache, and any server-side
config differences. Confounds that are _not_ part of the bench profile
(custom chat template, reasoning budget) are individual toggles — see below.

rpiv-advisor arms are explicitly **out of scope** for the initial
implementation (deferred by decision 2026-06-12); docs 03/04 reserve the arm
ID `L6-local-pi-advisor` for it. (Note: `rpiv-advisor` is not in the current
`pixi-recipes/pi-extensions` recipe; the arm ID is reserved, the deployment is
not wired.)

## Model deployments

### Reference endpoint (L1 — user-designated, precision explicitly labelled)

The L1 arm is whatever OpenAI-compatible endpoint the user points at as the
reference, labelled with its precision. It is **not** required to be a BF16
vendor, and the vendor is **not** required to certify its precision.

- Serves any model over an OpenAI-compatible `/v1` API. The model id is
  user-supplied (it need not be Qwen3.6-35B-A3B — L1 can be a different model
  family entirely, or a second local `llama-server`).
- **Precision is an explicit label the user provides**, recorded verbatim in
  the ledger `model.precision` (e.g. `BF16`, `FP16`, `IQ4_XS+q8_0kv`,
  `Q4_K_M`, `unknown`). No verification is performed — the point is that the
  label is written down, not that it is a particular value. A label of
  `unknown` is valid and must be stated honestly.
- Record endpoint URL, model id, precision label, and date in the run ledger
  before the first L1 run. The REPORT prints the line **`<endpoint URL> / <model id> -> <score>`** for every arm so the deployment is visible at a glance.
- Sampling: same as the local deployed sampling (temperature 0.6, top-p 0.95,
  top-k 20, min-p 0) to the extent the endpoint accepts the parameters. Record
  which parameters were accepted/dropped.
- Selection hint (optional, not required): pointing L1 at a vendor endpoint
  that Artificial Analysis benchmarks (and documenting that precision label)
  buys comparability with the AA-published SciCode score — but this is a
  convenience, not a hard requirement.

### Local bench profile (llama-server)

You configure `models.ini` yourself before running benchmarks — the docs do
not prescribe a preset. A reasonable starting point is your deployed Qwen
profile with the two known benchmark-hostile customizations removed (per
decision U3): the custom froggeric chat template and the reasoning-budget cap.
But the exact weights, placement, context size, MTP, sampling, and KV quant are
your call; tune them to your hardware and goals.

What the runner needs from you, and what you record in the ledger:

- **`model.preset`** — the `models.ini` preset name the runner should load
  (the value you pass as `BENCH_MODEL` / `--model openai/<preset>`).
- **`model.config`** — a **freeform string labelling anything the runner can't
  see** (this is the field you asked for). The runner auto-detects endpoint URL,
  model id, and the sampling params you pass via the API; it cannot see
  `models.ini` internals or server flags that are not exposed through the API.
  So write down anything non-default here, e.g.:
  `"n-cpu-moe=20; ctx-size=131072; MTP off; froggeric template on; KV q4_0"`.
  There is no fixed schema — be concise and human-readable; future-you (or a
  reviewer) needs to be able to reconstruct what was actually served.

**Named confounds (record in `toggles`, not `model.config`):** three settings
materially affect cross-run comparability and get structured fields so they
aren't buried in prose:

- **T1 — froggeric chat template** (`toggles.T1_froggeric: bool`). Default for
  a clean baseline: off (use the GGUF's embedded template). On = custom
  `chat-template-file`.
- **T2 — reasoning-budget cap** (`toggles.T2_reasoning_budget: bool|int`).
  Default off (uncapped thinking). Hard benchmark items are exactly where
  reasoning models want >8k thinking tokens; with the cap on, its effect can
  dominate and masquerade as quantization damage.
- **T3 — KV cache quant** (`toggles.T3_kv: "q8_0"|"q4_0"|"f16"|…`). Default is
  the global `q8_0` (part of the local stack under test); raising to `f16` is
  a future goal-2 experiment.
- **`parallel_slots`** (`toggles.parallel_slots: int`) — the `--parallel N`
  value, since it divides the context pool.

A ladder run is only comparable to another with identical `toggles` **and**
comparable `model.config`. If you tweak `models.ini` between two runs, say so
in `model.config` — do not silently let two runs look identical when they
aren't.

### Serving rules

- `pixi r -e llamacpp-source-cuda start-server` as today; port 8080.
- Exactly one model loaded during a benchmark run (`models-max = 1` already
  enforces this). No interactive use of the server during runs.
- SciCode runs MAY use request concurrency via llama-server `--parallel N`
  (N=4 recommended). This _divides_ the existing 262144-token context into
  N slots (~64k each); it does not reduce quality as long as no request
  approaches the per-slot ceiling. The runner must log a warning for any
  request whose prompt+output exceeds 80% of the slot window.
- Terminal-Bench runs use a single slot (agentic episodes need the full
  context window).

## Docker (Terminal-Bench only)

- A Docker daemon is required on the host for docs 02 and 04. Scope guard
  (per decision C3): Docker is used exclusively by the Harbor harness; no
  pixi task outside `docs/benchmarks` scope may depend on it, and day-to-day
  `pixi run pi` must keep working with the daemon stopped. Acceptance check:
  `systemctl stop docker && pixi r pi - -- -p "hello"` still works.
- Containers must be able to reach the host llama-server. Standard recipe on
  Linux: run Harbor with
  `--add-host host.docker.internal:host-gateway` semantics (or configure the
  agent's base URL as `http://host.docker.internal:8080/v1`); verify with a
  one-off `docker run --add-host host.docker.internal:host-gateway curlimages/curl curl -s http://host.docker.internal:8080/health`.
  The exact flag plumbing through Harbor is an implementation item in docs
  02/04.

## Datasets and one-off downloads

| Asset                     | Source                                                  | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SciCode problems          | HuggingFace `SciCode1/SciCode`                          | pulled automatically by the inspect_ai task                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| SciCode numeric test data | Google Drive folder `1W5GZW6_bdiDAiipuFMqdUhvUaHIj6-pR` | save as `eval/data/test_data.h5`; required for scoring. **Not actually gated** — `gdown` fetches it without sign-in (the web UI shows "Sign in" but gdown's confirm-token bypass works). One-liner: `pip install gdown && python -m gdown --folder "https://drive.google.com/drive/folders/1W5GZW6_bdiDAiipuFMqdUhvUaHIj6-pR" -O eval/data` (drops `eval/data/test_data.h5` in place). Expected sha256 `48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890` (~1.05 GB); record in ledger. |
| Terminal-Bench 2.0 tasks  | Harbor registry `terminal-bench@2.0`                    | tasks pinned by Harbor to repo `laude-institute/terminal-bench-2` at commit `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c` (2025-10-31, "update storage"); record the SHA in the ledger                                                                                                                                                                                                                                                                                                                     |
| pi-terminal-bench adapter | github.com/badlogic/pi-terminal-bench                   | pin a commit at implementation time, record in ledger                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

Version pins (decision A): Terminal-Bench **2.0**, not 2.1. Migration to 2.1
is a one-line dataset-version change plus possible adapter patch; out of
scope now.

## Run budget and calibration

Target: **1–4 hours per arm**. Enforcement is procedural, not aspirational:

1. **Calibration probe (mandatory, before every multi-hour run).**
   Run 3–5 items of the planned workload with the exact run configuration.
   Measure wall time per item and tok/s (use `pixi r llama-benchy` for raw
   throughput; use the harness itself for per-item time).
2. **Extrapolate**: projected total = mean item time × item count × repeats.
3. **Abort rule**: if the projection exceeds 4 h, do not start. Either shrink
   the pin (see per-doc pin files), reduce repeats to 1, or (SciCode) enable
   `--parallel`.
4. Record probe numbers in the ledger `notes` (there is no dedicated
   calibration field — the schema is intentionally minimal) even for aborted
   plans.

Repeats: default **1** for local arms; toggle to **3** for headline numbers.
Remote anchor arms default to **3** (cheap and fast at provider concurrency).

## Subset pins

Each benchmark doc carries a pin file (committed under
`docs/benchmarks/pins/`). Format: one item per line; excluded items present
but commented out with `#`; every line carries an inline `# rationale`
comment. Toggling = comment/uncomment. The full suite = uncomment everything.
Rules:

- The same pin file state must be used for **all arms being compared**.
- Swap rule: if calibration shows a pinned task blowing the budget on this
  hardware, it may be swapped for a commented-out task **of the same
  category**, and the swap is recorded in the ledger. No silent edits.

## Run ledger and results schema

All runs append one JSON object to `docs/benchmarks/results/runs.jsonl`
(directory contents are gitignored except `check_ledger.py`; the ledger itself
is the implementer's choice to commit or not). The cutoff table
`docs/benchmarks/model-cutoffs.toml` and its reader `lookup_cutoff.py` live in
the parent `docs/benchmarks/` dir and are committed. Required fields:

```json
{
  "run_id": "2026-06-15T09-12_scicode_L2",
  "benchmark": "scicode | tb2 | livecodebench | ifbench | evalplus | cruxeval",
  "arm": "L1-remote-canonical | L2-local-canonical | L3-local-pi-bare | L4-local-pi-tools | L5-local-pi-ext",
  "harness": { "name": "inspect_ai | harbor-terminus2 | pi | harbor-pi", "version": "…" },
  "model": {
    "deployment": "remote|local",
    "endpoint": "…",
    "precision": "BF16 | FP16 | IQ4_XS+q8_0kv | Q4_K_M | unknown",
    "preset": "<models.ini preset name you loaded>",
    "config": "freeform: any models.ini / server tweaks not auto-detectable by the runner (e.g. 'n-cpu-moe=20; ctx-size=131072; MTP off')"
  },
  "toggles": { "T1_froggeric": false, "T2_reasoning_budget": false, "T3_kv": "q8_0", "parallel_slots": 1 },
  "pin": { "file": "pins/tb2-quick.txt", "sha256": "…", "n_items": 12 },
  "repeats": 1,
  "score": { "metric": "subproblem_pass@1 | task_pass@1", "value": 0.0, "raw": "path/to/harness/output" },
  "timing": { "n": 12, "mean_s": 0.0, "median_s": 0.0, "min_s": 0.0, "max_s": 0.0 },
  "wall_clock_h": 0.0,
  "tokens": { "in": 0, "out": 0 },
  "notes": ""
}
```

`timing` is the **per-item wall time** (seconds) the runner records for every
graded item (a SciCode subproblem, a TB task, a LiveCodeBench problem, an
IFBench prompt, an EvalPlus problem, a CRUXEval item). `n` = number of items
timed (= `pin.n_items` × `repeats` unless the runner dropped items); `mean_s` /
`median_s` / `min_s` / `max_s` are the per-item wall times. The harness measures
this directly (per request/episode); for local arms it is also recoverable from
`llama-server.log` prompt timings as a cross-check. `wall_clock_h` stays the
total run wall clock (includes harness overhead, model loading, Docker pulls,
grading — not comparable across harnesses, which is why per-item `timing` is the
primary throughput signal).

Each headline run additionally produces a short `REPORT-<run_id>.md` that
states the score and **compares it against external references** (per
decision Q8: no hard numeric tolerances, but the comparison must be written
down). **Every REPORT prints a one-line summary per arm**:

`<endpoint URL> / <model id> -> <score>  (mean <mean_s>s/item, n=<n>)`

followed, when `model.config` is non-empty, by a second line:

`config: <model.config>`

so the deployment (precision label in parentheses), the score, the **time per
task**, and **any invisible `models.ini` tweaks** are all visible at a glance.
The endpoint is a user-supplied label, not an assumed BF16 vendor, so it must be
shown explicitly; `model.config` is the label for everything the runner can't
auto-detect (see §Local bench profile); the per-item time is the throughput
signal that is actually comparable across harnesses (unlike `wall_clock_h`,
which includes unrelated overhead).

- SciCode → Artificial Analysis SciCode page for Qwen3.6-35B-A3B (subproblem
  scoring, with background), noting any subset/protocol differences.
- TB 2.0 → the tbench.ai `terminal-bench@2.0` leaderboard (Terminus-class
  entries) and badlogic's published pi results (pi-terminal-bench repo /
  blog), noting that subset runs are not directly comparable to full-suite
  leaderboard numbers.

## Benchmark hygiene (applies to every arm)

- Unmanned runs: no interactive prompts may block. `rpiv-ask-user-question`
  must be absent/disabled in any pi arm (decision C2).
- No web access for the model: web search/fetch tools disabled in all pi
  arms; TB task containers keep whatever network the task itself defines
  (`allow_internet` is task metadata and part of the benchmark).
- No advisor/escalation extensions in L1–L5 (anything that routes tokens to
  a different model invalidates the arm).
- The server log (`llama-server.log`) for local runs is retained alongside
  the harness output for each run_id.

## Model cutoff table (contamination control)

`docs/benchmarks/model-cutoffs.toml` is a **static, committed table** mapping
model → training-data cutoff, covering every preset in `models.ini` plus the
external reference models (Claude Sonnet 5, GPT-5.6, Kimi K3, GLM-5.2,
MiniMax-M3, Hy3, MiMo-V2.5, DeepSeek-V4-Flash). `docs/benchmarks/lookup_cutoff.py`
reads it.

Contract (enforced by `lookup_cutoff.py`, which the benchmark runners call):

- The runner normalizes the model id (lowercase; strip HF repo prefix, GGUF
  `:quant` tag, and common suffixes like `-bench`/`-iq4_xs`/`-it`) and matches
  it against each entry's `key` + `aliases`.
- **If the model is not in the table, the lookup crashes (exit 2) with
  instructions on how to append a `[[models]]` block.** It does not fall back
  silently — a missing entry is a bug to fix, not a warning.
- `cutoff` is the comparison bound: `window_start > cutoff` ⇒ contamination
  posture holds. For vendor "year-level" claims (e.g. Qwen "2026") the cutoff
  is set to the **release date** (a hard upper bound — a model cannot have
  trained on data released after it) rather than 31 Dec of that year, which
  would void contamination control for the whole year. For undocumented
  cutoffs the release date is used as a proxy; `cutoff = "unknown"` means no
  usable bound (the runner **warns** that the posture is void, but does not
  crash — the model IS in the table).
- `confidence` ∈ {documented, year-level, proxy, inherited, unknown}; `source`
  records the URL/note. Last reviewed 2026-06-17.

To add a model: append a `[[models]]` block (key, cutoff, released,
confidence, source, optional aliases) and re-run.

## Pass criteria for this document (doc 00)

**Implemented (static, 2026-06-17):**

- [x] The ledger schema carries `model.preset` + `model.config` so a run can
      be labelled with the `models.ini` preset name **and** any non-default
      tweaks the runner can't auto-detect (freeform string). `check_ledger.py`
      validates both; the REPORT prints `config: <model.config>` when non-empty.
      `models.ini` itself is user-managed — the docs do not prescribe a preset.
- [x] `docs/benchmarks/results/` exists; a dummy ledger entry validates
      against the schema — `python results/check_ledger.py --self-test` →
      `OK: self-test example valid`. `check_ledger.py` validates the `timing`
      field (n/mean_s/median_s/min_s/max_s) too.
- [x] `docs/benchmarks/model-cutoffs.toml` + `lookup_cutoff.py` present; a
      lookup for `Qwen3.6-35B-A3B` returns its cutoff (`2026-04-15`) and a
      lookup for a missing id crashes (exit 2) with update instructions.

**Runtime / user-input (pending — need a running server, Docker, or a human):**

- [ ] You configure a `models.ini` preset for benchmarking; the server starts
      on it and `/health` is green; a one-prompt smoke call returns a
      completion with thinking uncapped (response contains an unstripped
      `</think>` segment or equivalent). Record the preset name in``model.preset`and any non-default settings in`model.config`.
- [ ] Docker present; scope guard check passes (`systemctl stop docker &&
      pixi r pi - -- -p "hello"` still works).
- [ ] Calibration procedure executed once end-to-end (any 3 SciCode items via
      doc 01 tooling) and recorded in the ledger `notes`.
- [ ] An L1 reference endpoint is designated (endpoint URL + model id +
      precision label recorded in a ledger entry); the precision label is
      explicit (any value, including `unknown`, is acceptable — the
      requirement is that it is written down). No vendor certification needed.

## Open verification items (carried by later docs)

- V1: MTP draft (`spec-type = draft-mtp`) compatibility with
  `--parallel > 1`. If broken: disable MTP for parallel SciCode runs
  (speed-only change, results unaffected) and note in ledger.
- V2: Exact Harbor flag plumbing for host networking (doc 02/04).
- V3: Whether pi can fully disable tools for the L3 bare arm (doc 03).
