# 00 — Common Benchmark Infrastructure

Status: DESIGN — to be implemented before any of docs 01–04.

## Purpose

Shared contracts for running SciCode and Terminal-Bench 2.0 against the local
llama.cpp stack and against a remote full-precision endpoint, so that results
from different harnesses and different model deployments are comparable with
each other. Docs 01–04 all depend on this document and must not redefine
anything specified here.

## The experiment ladder

Every benchmark is run as a sequence of *arms*. An arm = (model deployment,
harness, harness configuration). The canonical ladder:

| Arm ID | Model deployment | Harness | Measures |
|--------|-----------------|---------|----------|
| `L1-remote-canonical` | Remote API, BF16/FP16 | Canonical (inspect_ai / Terminus 2) | Anchor: the model's reference capability |
| `L2-local-canonical` | Local llama-server (bench profile) | Canonical | Local-stack delta vs L1 |
| `L3-local-pi-bare` | Local llama-server (bench profile) | pi, minimal | Harness delta vs L2 (SciCode only; see doc 03 §Bare) |
| `L4-local-pi-tools` | Local llama-server (bench profile) | pi, default tools | Agentic-tools delta |
| `L5-local-pi-ext` | Local llama-server (bench profile) | pi + extension ladder | pi-augmented outcome |

Terminology note: L2 is the **local-stack delta**, not "quantization delta".
It conflates at least: 4-bit weights, q8_0 KV cache, and any server-side
config differences. Confounds that are *not* part of the bench profile
(custom chat template, reasoning budget) are individual toggles — see below.

rpiv-advisor arms are explicitly **out of scope** for the initial
implementation (deferred by decision 2026-06-12); docs 03/04 reserve the arm
ID `L6-local-pi-advisor` for it.

## Model deployments

### Remote anchor endpoint

- Must serve **Qwen3.6-35B-A3B** over an OpenAI-compatible API.
- HARD REQUIREMENT: the vendor must document that the model is served at
  **BF16 or FP16** weights. If the precision is not documented, the vendor is
  not eligible. Record vendor, endpoint URL, documented precision, and date in
  the run ledger before the first anchor run.
- Selection hint: Artificial Analysis model pages list the endpoints they
  benchmark and the precision per endpoint; preferring one of those buys
  comparability with the AA-published SciCode score.
- Sampling: same as local deployed sampling (temperature 0.6, top-p 0.95,
  top-k 20, min-p 0) to the extent the vendor API accepts the parameters.
  Record which parameters were accepted/dropped.

### Local bench profile (llama-server)

Add a new preset to `models.ini`. It is the deployed Qwen profile with the
two known benchmark-hostile customizations removed (per decision U3):

```ini
[Qwen3.6-35B-A3B-bench]
# Same weights and placement as the daily-driver profile
hf = byteshape/Qwen3.6-35B-A3B-MTP-GGUF:Qwen3.6-35B-A3B-IQ4_XS-3.97bpw
ctx-size = 262144
ngl = 99
n-cpu-moe = 40
image-min-tokens = 1024
no-mmproj-offload = true

# MTP speculative decoding: speed-only, distribution-preserving. Keep ON,
# but it is a calibration-phase verification item (see below) because its
# interaction with --parallel is unverified.
spec-type = draft-mtp
spec-draft-n-max = 4
spec-draft-ngl = 99

# Deployed sampling (kept identical to daily driver, per decision Q10)
temperature = 0.6
top-p = 0.95
top-k = 20
min-p = 0.0
presence-penalty = 0.0
repeat-penalty = 1.0

# --- Deliberately ABSENT vs. the daily-driver profile ---
# chat-template-file = chat-templates/qwen3.6-froggeric-v20.jinja
#     TOGGLE T1 (default OFF for ladder runs): custom chat template.
# reasoning-budget = 8192
#     TOGGLE T2 (default OFF): thinking cap. Hard benchmark items are exactly
#     where reasoning models want >8k thinking tokens; with the cap on, its
#     effect can dominate and masquerade as quantization damage.
# chat-template-kwargs = {"preserve_thinking":true}
#     Belongs to the froggeric template; follows T1.
```

KV cache stays at the global `q8_0` (it is part of the local stack under
test; raising it to f16 is a future goal-2 experiment, TOGGLE T3, default
deployed value).

Toggles T1/T2/T3 are flipped by editing `models.ini`; every run records the
toggle state in the run ledger. A ladder run is only comparable to another
ladder run with identical toggle state.

### Serving rules

- `pixi r -e llamacpp-source-cuda start-server` as today; port 8080.
- Exactly one model loaded during a benchmark run (`models-max = 1` already
  enforces this). No interactive use of the server during runs.
- SciCode runs MAY use request concurrency via llama-server `--parallel N`
  (N=4 recommended). This *divides* the existing 262144-token context into
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

| Asset | Source | Notes |
|-------|--------|-------|
| SciCode problems | HuggingFace `SciCode1/SciCode` | pulled automatically by the inspect_ai task |
| SciCode numeric test data | Google Drive link in SciCode README | save as `eval/data/test_data.h5`; ~required for scoring; record sha256 in ledger |
| Terminal-Bench 2.0 tasks | Harbor registry `terminal-bench@2.0` | tasks pinned by Harbor to repo `laude-institute/terminal-bench-2` at commit `69671fba…` |
| pi-terminal-bench adapter | github.com/badlogic/pi-terminal-bench | pin a commit at implementation time, record in ledger |

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
4. Record probe numbers in the ledger even for aborted plans.

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
(directory is gitignored except for `.gitkeep`; the ledger itself is the
implementer's choice to commit or not). Required fields:

```json
{
  "run_id": "2026-06-15T09-12_scicode_L2",
  "benchmark": "scicode | tb2",
  "arm": "L1-remote-canonical | L2-local-canonical | L3 | L4 | L5",
  "harness": {"name": "inspect_ai | harbor-terminus2 | pi | harbor-pi", "version": "…"},
  "model": {"deployment": "remote|local", "endpoint": "…", "precision": "BF16|IQ4_XS+q8_0kv", "preset": "Qwen3.6-35B-A3B-bench"},
  "toggles": {"T1_froggeric": false, "T2_reasoning_budget": false, "T3_kv": "q8_0", "parallel_slots": 1},
  "pin": {"file": "pins/tb2-quick.txt", "sha256": "…", "n_items": 12},
  "repeats": 1,
  "score": {"metric": "subproblem_pass@1 | task_pass@1", "value": 0.0, "raw": "path/to/harness/output"},
  "wall_clock_h": 0.0,
  "tokens": {"in": 0, "out": 0},
  "notes": ""
}
```

Each headline run additionally produces a short `REPORT-<run_id>.md` that
states the score and **compares it against external references** (per
decision Q8: no hard numeric tolerances, but the comparison must be written
down):

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

## Pass criteria for this document (doc 00)

- [ ] `models.ini` contains `[Qwen3.6-35B-A3B-bench]` as specified; server
      starts and `/health` is green; a one-prompt smoke call returns a
      completion with thinking uncapped (response contains an unstripped
      `</think>` segment or equivalent).
- [ ] Toggles T1/T2 can be flipped by uncommenting documented lines only.
- [ ] Docker present; scope guard check passes (pi works with daemon stopped).
- [ ] `docs/benchmarks/results/` exists; a dummy ledger entry validates
      against the schema (a 20-line Python checker script is part of this
      deliverable).
- [ ] Calibration procedure executed once end-to-end (any 3 SciCode items via
      doc 01 tooling) and recorded.
- [ ] Remote vendor selected; documented BF16/FP16 evidence (URL or doc
      snapshot) stored under `docs/benchmarks/results/vendor-precision/`.

## Open verification items (carried by later docs)

- V1: MTP draft (`spec-type = draft-mtp`) compatibility with
  `--parallel > 1`. If broken: disable MTP for parallel SciCode runs
  (speed-only change, results unaffected) and note in ledger.
- V2: Exact Harbor flag plumbing for host networking (doc 02/04).
- V3: Whether pi can fully disable tools for the L3 bare arm (doc 03).
