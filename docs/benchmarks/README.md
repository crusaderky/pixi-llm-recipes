# Benchmark design docs

Design documents for running coding benchmarks against the local llama.cpp
stack in this repo and against a remote full-precision endpoint, with results
comparable across model deployments and harnesses. Two heavy benchmarks
(**SciCode**, **Terminal-Bench 2.0**) plus a **lightweight single-turn coding
panel** (LiveCodeBench, IFBench, EvalPlus, CRUXEval).

Read `00` first; it defines everything the others rely on. The lightweight
panel additionally shares `05`.

| Doc                                                         | Scope                                                                                                                        | Arms                              |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| [00-common-infrastructure](00-common-infrastructure.md)     | Shared contracts: model deployments, bench profile, Docker scope, datasets, run budget, pins, ledger/results schema, hygiene | —                                 |
| [01-scicode-canonical](01-scicode-canonical.md)             | SciCode via inspect_ai                                                                                                       | L1 reference, L2 local            |
| [02-terminalbench-canonical](02-terminalbench-canonical.md) | TB 2.0 via Harbor + Terminus 2                                                                                               | L1 reference, L2 local            |
| [03-scicode-pi](03-scicode-pi.md)                           | SciCode through pi                                                                                                           | L3 bare, L4 tools, L5 +extensions |
| [04-terminalbench-pi](04-terminalbench-pi.md)               | TB 2.0 through pi (badlogic adapter)                                                                                         | L4 vanilla pi, L5 +extensions     |
| [05-common-lightweight](05-common-lightweight.md)           | Shared prereqs for the single-turn panel: endpoint plumbing, exec sandbox, ledger names, isolation                           | —                                 |
| [06-livecodebench](06-livecodebench.md)                     | LiveCodeBench code-gen (contamination-controlled)                                                                            | L1 reference, L2 local            |
| [07-ifbench](07-ifbench.md)                                 | IFBench instruction-following (not coding; reliability probe)                                                                | L1 reference, L2 local            |
| [08-evalplus](08-evalplus.md)                               | HumanEval+ / MBPP+ (cheap capability floor)                                                                                  | L1 reference, L2 local            |
| [09-cruxeval](09-cruxeval.md)                               | CRUXEval code-reasoning (-I / -O)                                                                                            | L1 reference, L2 local            |

## The lightweight single-turn panel (docs 05–09)

Four cheap, Docker-free, deterministically-graded benchmarks added after the
two heavy ones. They run **only L1/L2** (single-shot — no pi agentic arms) and
are deliberately structured for **independent, parallel, out-of-order**
implementation:

- **`05` is the only shared prerequisite** (on top of `00`). It carries three
  independent deliverables: D1 endpoint plumbing, D2 untrusted-code exec
  sandbox, D3 ledger-name extension. Build them in any order.
- **`06`/`08`/`09` each need D1+D2+D3**; **`07` (IFBench) needs only D1+D3** (it
  runs no model code) and can be built before the sandbox exists.
- No benchmark doc depends on another. Each uses its **own isolated harness
  env**, its own pin file, its own ledger entries and REPORT.
- None map cleanly to `inspect_evals` (unlike SciCode), so each uses its native
  upstream harness — see `05` for why.

## The ladder (why these arms exist)

The point is not a single score; it is a chain of deltas:

1. **L1 reference endpoint, canonical harness** — the model's reference
   capability at a user-designated OpenAI-compatible endpoint (precision
   explicitly labelled; may be a remote vendor or a second local llama-server).
   External anchor (AA for SciCode; tbench.ai 2.0 board for TB).
2. **L2 local quantised, canonical harness** — the _local-stack delta_ (4-bit
   weights + q8_0 KV + bench profile). NOT a pure "quantization" number.
3. **L3 local, bare pi** — the _harness delta_ (SciCode only; TB has no
   tool-less arm).
4. **L4 local, pi default tools** — the agentic-tools delta.
5. **L5 local, pi + repo extensions** — the pi-augmented outcome and its token
   cost.

`L6-local-pi-advisor` (rpiv-advisor escalating to a datacenter model) is
designed-for but deferred. Note: `rpiv-advisor` is not in the current
`pixi-recipes/pi-extensions` recipe at all — the arm ID is reserved, the
deployment is not wired.

## Decisions locked in (2026-06-12)

- **L1 is a user-designated reference endpoint, not a BF16 vendor.** The user
  supplies an OpenAI-compatible URL + model id + an explicit precision label
  (any value incl. `unknown`; no vendor certification). Every REPORT prints
  `<endpoint URL> / <model id> -> <score>` so the deployment is visible.
- Terminal-Bench pinned at **2.0** (not 2.1); SciCode external ref is Artificial
  Analysis, TB external ref is tbench.ai 2.0 leaderboard + badlogic's pi run.
- 1–4 h per arm, enforced by a mandatory calibration probe + abort rule.
- Default **1 repeat** (toggle 3); default **pinned subsets** (toggle full).
- Two known benchmark-hostile customizations (froggeric chat template,
  reasoning-budget cap) are OFF by default and exposed as toggles T1/T2.
- pi web tools / ask-user / advisor disabled in all scored arms.
- No hard numeric pass/fail tolerances; every headline run writes a `REPORT`
  that states the score and compares to the external reference narratively.

## Decisions added (2026-06-17)

- **L5 extension set narrowed to `{pi-caveman, rtk}` only.** The full
  `pixi-recipes/pi-extensions` PLUGINS list (pi-autoresearch, pi-btw,
  pi-caveman, pi-intercom, pi-llama-cpp, pi-web-access, pi-subagents,
  pi-token-speed, rpiv-ask-user-question, pi-usage-extension + rtk-cli conda
  dep) is reduced to just the token-economy output-shaping pair for L5;
  everything else is DROP (web access, subagent spawning, experiment loops,
  cross-session comms, endpoint-override risk, TUI-only display, unmanned-run
  blockers). `rtk` is a conda-forge package (`rtk-cli`), not npm — doc 04's
  in-container install needs a conda/binary install, not `pi install npm:...`.
- **L5 token accounting** comes from the `llama-server.log` (retained per
  doc 00), not `pi-usage-extension` (now DROP).
- **TB runner per-task cap lowered 1200→900** to equal the default pin's max
  task `agent_to` (all included tasks are 900/750), so it binds as a true
  ceiling; the old "overrides downward" wording was wrong (1200 > 900 never
  bound). 12 × 900 s ≈ 3 h worst case at n=1.
- **No prescribed `models.ini` bench preset.** The docs no longer mandate a
  `[Qwen3.6-35B-A3B-bench]` preset; you configure `models.ini` yourself and
  label each run with `model.preset` (the preset name) + `model.config`
  (freeform string for any non-default tweaks the runner can't auto-detect —
  e.g. `n-cpu-moe=20; ctx-size=131072; MTP off; froggeric template on`). The
  REPORT prints `config: <model.config>` when non-empty. Named confounds
  (froggeric template, reasoning-budget cap, KV quant, parallel slots) get
  structured `toggles` fields; everything else goes in `model.config`.
- **L1 reference endpoint = user-designated, precision explicitly labelled**
  (replaces the prior "remote BF16 vendor must document precision"). No vendor
  certification; the precision label (any value, incl. `unknown`) is recorded
  in the ledger and printed in every REPORT as `<URL> / <model> -> <score>`.
  The `vendor-precision/` evidence dir is no longer required (reverted).
- **Static model-cutoff table** (`docs/benchmarks/model-cutoffs.toml` +
  `lookup_cutoff.py`) covers every `models.ini` preset + Sonnet 5, GPT-5.6,
  Kimi K3, GLM-5.2, MiniMax-M3, Hy3, MiMo-V2.5, DeepSeek-V4-Flash. Runners look
  up the cutoff via `lookup_cutoff.py`; a **missing model crashes (exit 2)**
  with instructions to add it (no silent fallback); `cutoff = "unknown"`
  warns (posture void) but does not crash. Year-level vendor claims use the
  release date as the safe upper bound.
- **TB 2.0 commit SHA resolved**: `69671fbaac6d67a7ef0dfec016cc38a64ef7a77c`
  (2025-10-31, "update storage") — written into docs 00/02; no manual step.
- **SciCode `test_data.h5` is NOT gated**: `gdown` fetches it from the GDrive
  folder without sign-in (sha256 `48b0272a…`, ~1.05 GB); the working one-liner
  is in docs 00/01.
- **Per-task time recorded next to every benchmark output.** Ledger gains a
  required `timing` field (`{n, mean_s, median_s, min_s, max_s}` — per-item
  wall seconds, validated by `check_ledger.py`); every REPORT prints
  `<URL> / <model> -> <score>  (mean <mean_s>s/item, n=<N>)` per arm. Per-item
  time is the throughput signal comparable across harnesses (unlike
  `wall_clock_h`, which includes model load / Docker pull / grading overhead).

## Pins

- [pins/scicode-subset.txt](pins/scicode-subset.txt) — 30/65 problems, stratified.
- [pins/tb2-quick.txt](pins/tb2-quick.txt) — 12/89 tasks, budget-safe mediums.
- [pins/livecodebench-window.txt](pins/livecodebench-window.txt) — post-cutoff date window (not an ID list).
- [pins/ifbench-full.txt](pins/ifbench-full.txt) — full eval set (default).
- [pins/evalplus-full.txt](pins/evalplus-full.txt) — HumanEval+ & MBPP+ full (default).
- [pins/cruxeval-full.txt](pins/cruxeval-full.txt) — -I & -O full (default).

Same pin state is used across all compared arms. For the heavy benchmarks,
comment/uncomment to resize; the lightweight panel defaults to the full suite
(it's cheap) with an optional first-N smoke cap.

## Implementation order (suggested)

1. `00` — bench profile, Docker scope guard, results schema + checker, vendor
   precision evidence, one calibration probe.
2. `01` L2 smoke (`mode=dummy`) → L2 pinned → L1 anchor.
3. `02` connectivity proof → L2 single-task smoke → L2 pinned → L1.
4. `03` pi driver + scoring bridge → L3 → L4 → L5.
5. `04` adapter setup → L4 → L5 (extension install in container).

The lightweight panel (`05`–`09`) is **not** part of this linear order — it
hangs off `00` independently. Once `05`'s three deliverables exist, do `06`,
`07`, `08`, `09` in any order or in parallel (`07` doesn't even need `05`'s
sandbox). Suggested first taste: `07` (IFBench — no sandbox, minutes) or `08`
(EvalPlus — floor/tripwire), then `06` (LiveCodeBench — the headline quant
signal).

Each step is self-contained and produces a ledger entry; you can stop after any
arm with a usable result.
