# 03 — SciCode through pi

Status: DESIGN. Depends on: `00`, and shares scoring with `01`.

Implements ladder arms **L3-local-pi-bare**, **L4-local-pi-tools**,
**L5-local-pi-ext** for SciCode, all on the local bench profile. Goal: isolate
the harness change (Terminus/inspect → pi), then the effect of giving the model
tools, then the effect of the repo's pi extensions.

## Design intent (and a correction to the original framing)

The original plan had a "bare pi, no tools" step expecting it to score "the
same as L2". Two honest caveats baked into this doc:

1. **Bare pi ≠ canonical harness even at identical model.** Different system
   prompt, message framing, and no AA SciCode prompt template. Expect a small
   nonzero delta L2↔L3 in either direction; a large one is a *finding* about
   prompt sensitivity, not a bug.
2. SciCode is single-shot by construction; "tools" only matter if pi is allowed
   to *run code before answering*. So the pi ladder for SciCode is:
   - **L3 bare**: pi with tools disabled — model emits the function, no
     execution. Closest analogue to canonical single-shot. Measures pure
     harness/prompt delta.
   - **L4 tools**: pi with default tools (read/write/edit/bash) — model MAY
     write and test-run candidate code against its own scratch tests before
     submitting. This is "agentic SciCode" and is **deliberately not protocol-
     comparable to L2/L3**; it is compared on the same axis (subproblem pass
     rate) to show the lift from letting the model iterate. Web tools stay OFF.
   - **L5 ext**: L4 plus the repo's token-economy extensions (caveman, rtk,
     token/usage accounting). Measures whether the extension set helps or hurts
     and at what token cost. (advisor / ask-user / web tools remain OFF —
     decision C2; advisor deferred as L6.)

Expectation calibration (your Q9 hypothesis, recorded as hypothesis not gate):
bare pi at BF16 ≈ AA; degrade with local quant; possibly recover with tools.
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
arm), and the repo's deployed pi (extensions, sandbox) for L5. Concretely:

- L3/L4 use pi with a controlled extension set (none / none, respectively) —
  launch via a benchmark-specific wrapper rather than the full
  `bwrap-pi.sh` extension injection, so the tool/extension state is exactly
  known. Sandboxing (bwrap) MAY still wrap it; the workspace is the per-
  subproblem dir.
- L5 uses the repo's actual deployed extension set from
  `pixi-recipes/pi-extensions/recipe.yaml`, MINUS the disallowed ones
  (rpiv-ask-user-question; any web search/fetch via pi-ollama-cloud;
  rpiv-advisor). The wrapper must assert these are inert (see hygiene checks).

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

| Arm | pi tools | pi extensions | web tools | repeats (default) | pin |
|-----|----------|---------------|-----------|-------------------|-----|
| L3 bare | none | none | off | 1 | scicode-subset |
| L4 tools | read/write/edit/bash | none | off | 1 | scicode-subset |
| L5 ext | read/write/edit/bash | caveman, rtk, token-speed, usage-extension | off | 1 | scicode-subset |

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
- Token accounting: for L5, capture pi's token-usage/usage-extension output per
  subproblem into the ledger `tokens` field — this is part of the deliverable's
  value (cost of the extensions vs the pass-rate change).

## Pass criteria (minimize implementer run time)

Smoke-level:

- [ ] Driver runs ONE subproblem through pi (L3), extracts the function, scores
      it with the SciCode evaluator — proves the generation→scoring bridge.
- [ ] L3 transcript shows zero tool calls (V3 satisfied) — else arm renamed and
      impurity noted.
- [ ] Hygiene assert: in L5, rpiv-ask-user-question / web tools / rpiv-advisor
      are absent or provably never invoked (grep the transcript for their tool
      names → must be empty).

Arm-complete:

- [ ] Each of L3, L4, L5 completes the pinned subset within budget; subproblem
      pass@1 + token totals recorded; ledger + `REPORT`.
- [ ] A combined `REPORT-scicode-ladder.md` tabulates L1,L2,L3,L4,L5 pass@1
      side by side, plus L5 token cost, and narrates the deltas against the AA
      SciCode reference. No numeric gates (decision Q8).

Sanity (narrative):

- [ ] L3 ≈ L2 within a modest band is *plausible*; a large gap is reported as a
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
