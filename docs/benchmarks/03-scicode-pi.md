# 03 — SciCode through pi

Status: DESIGN.

## Dependency chain

- **Depends on:** `00` (bench profile, ledger, calibration, hygiene, toggles).
- **Shares with `01`:** the SciCode dataset, `test_data.h5`, and the scoring
  path — doc 03 reuses doc 01's evaluator, it does not reimplement grading.
- **Unblocks:** nothing (leaf).

Implements ladder arms **L3-local-pi-bare**, **L4-local-pi-tools**,
**L5-local-pi-ext** for SciCode, all on the local bench profile. Goal: isolate
the harness change (Terminus/inspect → pi), then the effect of giving the model
tools, then the effect of the repo's pi extensions.

## External dependencies (require user input)

- **SciCode numeric test data (`test_data.h5`)** — fetched via `gdown` (the
  GDrive folder is not actually gated; see doc 01 §Setup for the one-liner and
  expected sha256 `48b0272a…`). Needed because doc 03 reuses doc 01's scoring
  path, so the evaluator must have `test_data.h5` in place. A human runs the
  `gdown` command and verifies the sha256; record it in the ledger.
- **pi "no tools" mechanism (V3)** — not an external asset, but an
  implementation-time discovery: whether pi exposes a first-class way to start
  with an empty tool set. If not, the L3 arm is renamed and the impurity noted
  (see §Disabling tools). Not a user-input dependency, but it gates the L3
  acceptance criteria.

## Design intent (and a correction to the original framing)

The original plan had a "bare pi, no tools" step expecting it to score "the
same as L2". Two honest caveats baked into this doc:

1. **Bare pi ≠ canonical harness even at identical model.** Different system
   prompt, message framing, and no AA SciCode prompt template. Expect a small
   nonzero delta L2↔L3 in either direction; a large one is a _finding_ about
   prompt sensitivity, not a bug.
2. SciCode is single-shot by construction; "tools" only matter if pi is allowed
   to _run code before answering_. So the pi ladder for SciCode is:
   - **L3 bare**: pi with tools disabled — model emits the function, no
     execution. Closest analogue to canonical single-shot. Measures pure
     harness/prompt delta.
   - **L4 tools**: pi with default tools (read/write/edit/bash) — model MAY
     write and test-run candidate code against its own scratch tests before
     submitting. This is "agentic SciCode" and is **deliberately not protocol-
     comparable to L2/L3**; it is compared on the same axis (subproblem pass
     rate) to show the lift from letting the model iterate. Web tools stay OFF.
   - **L5 ext**: L4 plus the repo's **token-economy output-shaping** extensions
     only — `pi-caveman` (terse output) and `rtk` (CLI proxy that filters/summarises
     ls/read/git/gh output to save tokens). Measures whether this minimal
     extension set helps or hurts and at what token cost. Every other deployed
     extension is DROP (see §Running pi as deployed by the repo); advisor is not
     installed at all (L6 arm ID reserved, deployment deferred).

Expectation calibration (your Q9 hypothesis, recorded as hypothesis not gate):
bare pi at the reference precision ≈ AA; degrade with local quant; possibly
recover with tools.
The recovery is exactly what L4/L5 test — it is not assumed by any pass
criterion.

## How pi runs SciCode (harness shape)

pi is a coding agent, not a benchmark runner, so this arm needs a thin driver
that, per subproblem:

1. Materializes a workspace containing: the subproblem instruction (+
   background, matching `with_background=True`), the forwarded prior-subproblem
   solutions, and a target file path for the answer.
2. Invokes pi in **non-interactive print mode** (`pi -p "<task brief>"` or the
   JSON/RPC mode) inside the workspace, with the model pointed at the local
   bench server.
3. Extracts the produced function and feeds it to the **unmodified SciCode
   test harness** for scoring (reuse doc 01's scoring path — do NOT reimplement
   grading; call into the SciCode package's evaluator on the generated code).

This keeps scoring identical to canonical SciCode; only generation differs.

### Running pi as deployed by the repo

Per decision C4, doc 03 uses **vanilla pi** for the first deliverable (the bare
arm), and a controlled extension set for L5. Concretely:

- L3/L4 use pi with a controlled extension set (none / none, respectively) —
  launch via a benchmark-specific wrapper rather than the full
  `bwrap-pi.sh` extension injection, so the tool/extension state is exactly
  known. Sandboxing (bwrap) MAY still wrap it; the workspace is the per-
  subproblem dir.
- **L5 extension set = `{pi-caveman, rtk}` only.** Source of truth is
  `pixi-recipes/pi-extensions/recipe.yaml`; the full deployed set is reduced to
  just the token-economy output-shaping pair, with everything else DROP for
  benchmark validity:

  | package (npm, unless noted)                | L5       | reason                                                                                  |
  | ------------------------------------------ | -------- | --------------------------------------------------------------------------------------- |
  | `pi-caveman`                               | **KEEP** | terse-output token economy (the behavior under test)                                    |
  | `rtk` (**conda-forge `rtk-cli`**, not npm) | **KEEP** | CLI proxy that filters ls/read/git/gh output to save tokens                             |
  | `pi-web-access`                            | DROP     | provides web search/fetch (brave/exa/openai-search) → solution leakage; hygiene         |
  | `pi-subagents`                             | DROP     | can spawn child agents → extra compute / fidelity change                                |
  | `pi-autoresearch`                          | DROP     | autonomous experiment loop → fidelity change, uses subagents                            |
  | `pi-intercom`                              | DROP     | cross-session messaging → non-deterministic external input                              |
  | `pi-btw`                                   | DROP     | context-injection extension; not the L5 axis under test                                 |
  | `pi-token-speed`                           | DROP     | TUI display only; no model-facing effect, not needed                                    |
  | `@tmustier/pi-usage-extension`             | DROP     | TUI token display; token totals come from the llama-server log instead (see §Wall-time) |
  | `@juicesharp/rpiv-ask-user-question`       | DROP     | blocks unmanned runs (decision C2)                                                      |
  | `pi-llama-cpp`                             | DROP     | zero-config local provider; could override the configured bench endpoint                |
  | `rpiv-advisor` (not installed)             | n/a      | L6 arm reserved; not in the recipe, nothing to drop                                     |

  `rtk` is a **conda-forge package (`rtk-cli`)**, not an npm plugin — in the
  repo's pixi env it is a `run` dep of `pi-extensions` and `rtk init -g --agent
  pi` runs at build time. For doc 03 (pi launched from the repo env) rtk is
  available as-is; the wrapper just must not disable it. The wrapper MUST assert
  the DROP set is inert (see hygiene checks).

### Disabling tools for L3 (verification item V3)

pi gives the model read/write/edit/bash by default. The bare arm needs these
off so the model cannot execute code. Resolve V3 at implementation:

- Preferred: a pi prompt-template / config that exposes no tools, or a launch
  flag that starts with an empty tool set. If pi has no first-class "no tools"
  switch, the fallback is a minimal pi extension/config that removes the
  default tools, or running through pi's print mode with tools stripped.
- Acceptance: in an L3 run, the session transcript shows **zero tool calls**.
  If tools cannot be fully removed, document it and rename the arm
  "L3-local-pi-mintools" with whatever minimum remains, and note the impurity.

## Configuration matrix

| Arm      | pi tools             | pi extensions              | web tools | repeats (default) | pin            |
| -------- | -------------------- | -------------------------- | --------- | ----------------- | -------------- |
| L3 bare  | none                 | none                       | off       | 1                 | scicode-subset |
| L4 tools | read/write/edit/bash | none                       | off       | 1                 | scicode-subset |
| L5 ext   | read/write/edit/bash | **pi-caveman, rtk** (only) | off       | 1                 | scicode-subset |

All arms: local bench profile, toggles T1/T2 OFF by default (same as L2),
sampling temp 0.6. Same pin as doc 01 so L2↔L3↔L4↔L5 are on one yardstick.

## Wall-time plan

- pi adds per-subproblem orchestration overhead vs inspect_ai (process spawn,
  agent loop). L4/L5 add tool-execution turns, which multiply tokens and time.
  Budget risk is highest for L4/L5.
- Mandatory calibration per arm: 3–5 subproblems, measure wall time incl. tool
  turns, extrapolate, abort >4h. For L4/L5 expect materially longer per-item
  time than L3; if over budget, shrink the pin (same file, comment more) before
  reducing fidelity.
- Token accounting: `pi-usage-extension` is DROP in L5, so token totals are
  captured from the **llama-server log** (`llama-server.log`, retained per
  doc 00) or pi's native usage output, not a TUI extension. Record per-
  subproblem `tokens.in`/`tokens.out` in the ledger — the cost/benefit of the
  caveman+rtk set vs the pass-rate change is the deliverable's payload.

## Pass criteria (minimize implementer run time)

Smoke-level:

- [ ] Driver runs ONE subproblem through pi (L3), extracts the function, scores
      it with the SciCode evaluator — proves the generation→scoring bridge.
- [ ] L3 transcript shows zero tool calls (V3 satisfied) — else arm renamed and
      impurity noted.
- [ ] Hygiene assert: in L5, the DROP set is absent or provably never invoked
      — grep the transcript for `pi-web-access` / web tool names, `pi-subagents`,
      `pi-autoresearch`, `pi-intercom`, `pi-btw`, `pi-token-speed`,
      `pi-usage-extension`, `rpiv-ask-user-question`, `pi-llama-cpp` → must be
      empty. Only `pi-caveman` and `rtk` may appear.

Arm-complete:

- [ ] Each of L3, L4, L5 completes the pinned subset within budget; subproblem
      pass@1 + token totals recorded; ledger + `REPORT`.
- [ ] A combined `REPORT-scicode-ladder.md` tabulates L1,L2,L3,L4,L5 pass@1
      side by side with each arm's one-line summary (doc 00: `<URL> / <model>
      -> <pass@1>  (mean <mean_s>s/subproblem, n=<N>)`), plus L5 token cost,
      and narrates the deltas against the AA SciCode reference. No numeric
      gates (decision Q8).

Sanity (narrative):

- [ ] L3 ≈ L2 within a modest band is _plausible_; a large gap is reported as a
      prompt-sensitivity finding, not failed.
- [ ] If L4/L5 > L3, that is the hypothesized tool lift; if not, that is itself
      a reportable result (tools sending a 4-bit model down rabbit holes,
      context blowups against the q8_0 KV window, etc.).

## Notes

- Reuse doc 01's pin and scoring code paths verbatim; the only new code is the
  pi driver + extraction.
- Do not let pi fetch anything from the web — SciCode problems derive from
  published papers and are findable; web access would leak solutions and
  invalidate the arm.
