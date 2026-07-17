# 04 — Terminal-Bench 2.0 through pi (Harbor adapter)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00` (bench profile, Docker scope guard, ledger, hygiene),
  `02` (TB 2.0 pin, Docker setup, host networking V2).
- **Unblocks:** nothing (leaf).

Implements ladder arms **L4-local-pi-tools** and **L5-local-pi-ext** for TB 2.0,
using badlogic's `pi-terminal-bench` Harbor adapter. (There is no meaningful
"L3 bare pi" for TB — see correction U2 below.)

## External dependencies (require user input)

- **Docker daemon on the host** — shared with doc 02.
- **`pi-terminal-bench` adapter commit pin** — clone
  github.com/badlogic/pi-terminal-bench and pin a commit at implementation
  time; record it in the ledger. The adapter's Harbor patch / `upload_dir`
  workaround must be applied per its current README and recorded.
- **Harbor flag confirmation (V2)** — exact `harbor run` flag names and the
  adapter's mechanism for injecting the model endpoint config into the
  container must be confirmed at implementation time.
- **rtk in-container install** — `rtk` is a **conda-forge package
  (`rtk-cli`)**, not an npm plugin, so the L5 in-container `install-extensions.sh`
  cannot install it via `pi install npm:...`; it needs a conda/pip/binary
  install inside the container (or a prebuilt `rtk` binary downloaded in the
  image build). Implementation item, called out here because it is the one
  KEEP extension that is not npm.

## Design intent and two corrections

1. **No bare-pi arm for TB (correction U2).** TB tasks _are_ terminal
   interaction; a pi with no bash cannot act and scores zero by construction.
   So the minimum viable pi here already has tools. The harness-delta question
   for TB is answered by comparing **doc 02 L2 (Terminus 2)** against **doc 04
   L4 (pi default tools)** on the identical pinned task set — both are doc 02's
   pin, same model, same Docker. That is the clean Terminus-vs-pi comparison.

2. **Two deliverables, per decision C4:**
   - **04a — vanilla pi**: the pi-terminal-bench adapter as shipped, which
     installs vanilla pi _inside each task container_. This is L4 for TB.
   - **04b — repo-deployed extension set in-container**: a custom install
     script run inside the container that reproduces the repo's pi extension
     set reduced to **`{pi-caveman, rtk}` only** (see L5 section), giving the
     pi-augmented arm L5.

   Note the unavoidable consequence (raised earlier as C4): the adapter runs pi
   _inside the container_, so it is NOT your conda-pinned pi, NOT your bwrap
   sandbox (the container replaces it), and it must reach the host
   llama-server over the Docker bridge. "pi as deployed by the repo" is
   therefore reproduced by replicating the _extension set_, not by reusing the
   repo's launch path.

## Setup

```bash
# uv + harbor as in doc 02
uv tool install harbor
# adapter (pin a commit, record in ledger)
git clone https://github.com/badlogic/pi-terminal-bench.git
cd pi-terminal-bench
uv venv && source .venv/bin/activate
uv pip install -e .
```

The adapter exposes `PiAgent` via `--agent-import-path pi_terminal_bench:PiAgent`.
Known operational notes from the adapter's docs:

- Local Docker and Daytona-cloud execution are both supported; we use **local
  Docker**.
- The adapter README documents a **Harbor patch** ("Critical: Harbor Patch")
  and a Docker `upload_dir` bug workaround — apply per the adapter's current
  README at implementation time and record what was applied.
- Leaderboard submission flow exists (`--jobs-dir`, `--k 5`) but we are NOT
  submitting; we use it only to produce parseable results.

### Pointing pi at the local model

The adapter takes `-m <litellm-model>`. For the local bench server use an
OpenAI-compatible route to `http://host.docker.internal:8080/v1` (same V2
networking as doc 02). Because pi runs _inside_ the container, the base URL/env
must be injected into the container environment by the adapter — confirm the
adapter's mechanism for passing model/endpoint config into the container and
document it. Connectivity proof (doc 02) is a prerequisite.

### L4 — vanilla pi (deliverable 04a)

```bash
pixi r -e llamacpp-source-cuda start-server     # your bench preset (models.ini), single slot
export BENCH_MODEL="Qwen3.6-35B-A3B"             # your models.ini preset; record in ledger model.preset
harbor run \
  -d terminal-bench@2.0 \
  --agent-import-path pi_terminal_bench:PiAgent \
  -m openai/$BENCH_MODEL \
  --task-ids-file docs/benchmarks/pins/tb2-quick.txt \
  -n 1 \
  --agent-timeout 900 \
  --jobs-dir docs/benchmarks/results/tb2-L4
```

- Vanilla pi → default tools (read/write/edit/bash), no extra extensions, no
  web tools. This is the pi-augmented-tools arm.
- Same pin, same `-n 1` default, `--k 3` toggle, same per-task cap as doc 02.

### L5 — repo extension set in-container (deliverable 04b)

The adapter installs pi in the container; extend its install step to also
install the repo's allowed extensions. Source of truth is
`pixi-recipes/pi-extensions/recipe.yaml`; the deployed set is reduced to the
token-economy output-shaping pair **`{pi-caveman, rtk}` only** — everything
else is DROP for benchmark validity:

| package                                    | L5       | reason                                                                   |
| ------------------------------------------ | -------- | ------------------------------------------------------------------------ |
| `pi-caveman` (npm)                         | **KEEP** | terse-output token economy (the behavior under test)                     |
| `rtk` (**conda-forge `rtk-cli`**, not npm) | **KEEP** | CLI proxy that filters ls/read/git/gh output to save tokens              |
| `pi-web-access`                            | DROP     | web search/fetch (brave/exa/openai-search) → solution leakage; hygiene   |
| `pi-subagents`                             | DROP     | spawns child agents → extra compute / fidelity change                    |
| `pi-autoresearch`                          | DROP     | autonomous experiment loop → fidelity change                             |
| `pi-intercom`                              | DROP     | cross-session messaging → non-deterministic external input               |
| `pi-btw`                                   | DROP     | context-injection extension; not the L5 axis under test                  |
| `pi-token-speed`                           | DROP     | TUI display only; no model-facing effect                                 |
| `@tmustier/pi-usage-extension`             | DROP     | TUI token display; token totals come from the llama-server log instead   |
| `@juicesharp/rpiv-ask-user-question`       | DROP     | blocks unmanned runs (decision C2)                                       |
| `pi-llama-cpp`                             | DROP     | zero-config local provider; could override the configured bench endpoint |
| `pi-ollama-cloud` (not in recipe)          | n/a      | ghost reference removed — not installed, nothing to drop                 |
| `rpiv-advisor` (not in recipe)             | n/a      | L6 arm reserved; not installed, nothing to drop                          |

Implementation: a `install-extensions.sh` baked into the adapter's container
build. **`pi-caveman`** installs via `pi install npm:pi-caveman@<ver>`. **`rtk`**
is conda-forge (`rtk-cli`), not npm — install it inside the container via a
prebuilt binary download or `pip install` of an equivalent wheel, then run
`rtk init -g --agent pi` (mirroring `pixi-recipes/pi-extensions/build.sh`). Pin
the same versions as the recipe for reproducibility; record them in the ledger.
Token totals are captured from the **llama-server log** (retained per doc 00),
not a TUI extension — `pi-usage-extension` is DROP.

```bash
harbor run \
  -d terminal-bench@2.0 \
  --agent-import-path pi_terminal_bench:PiAgent \
  -m openai/$BENCH_MODEL \
  --task-ids-file docs/benchmarks/pins/tb2-quick.txt \
  -n 1 --agent-timeout 900 \
  --jobs-dir docs/benchmarks/results/tb2-L5
  # + adapter configured to run install-extensions.sh in the container build
```

## Wall-time plan

- Same envelope as doc 02 (12 pinned tasks, 900s cap, ~3h worst case, n=1),
  plus container-build time for the extension install in L5 (one-time per image
  build; warm up before timing).
- L5 extension overhead (caveman/rtk reshape context — token economy, not
  extra turns) mainly affects token counts, not wall time. Capture token totals
  from the **llama-server log** into the ledger (`pi-usage-extension` is DROP)
  — the cost/benefit of the `{pi-caveman, rtk}` set is the deliverable's payload.
- Mandatory calibration before each arm (shortest pinned tasks first); cut the
  pin to ~8 tasks if projection >4h.

## Pass criteria (minimize implementer run time)

Smoke-level:

- [ ] Adapter installs; `PiAgent` import path resolves; required Harbor
      patch / upload_dir workaround applied and recorded.
- [ ] One short task (`overfull-hbox`) runs through L4 against the local
      server, produces Harbor `result.json`, score parses.
- [ ] For L5: container build includes the extension install; a one-task run's
      transcript shows **only `pi-caveman` and `rtk`** present and the DROP set
      absent / never invoked (grep transcript for `pi-web-access` / web tool
      names, `pi-subagents`, `pi-autoresearch`, `pi-intercom`, `pi-btw`,
      `pi-token-speed`, `pi-usage-extension`, `rpiv-ask-user-question`,
      `pi-llama-cpp` → empty). This is a hard hygiene gate.

Arm-complete:

- [ ] L4 and L5 each complete the pinned subset within budget; per-task
      pass/fail + suite pass@1 + token totals recorded; ledger + `REPORT`.
- [ ] `REPORT-tb2-ladder.md` tabulates L1(02), L2(02), L4(04a), L5(04b)
      pass@1 on the identical pin with each arm's one-line summary (doc 00:
      `<URL> / <model> -> <pass@1>  (mean <mean_s>s/task, n=<N>)`), plus L5
      token cost, and narrates:
      - Terminus 2 vs pi (L2 vs L4): the harness delta.
      - pi vs pi+extensions (L4 vs L5): the extension delta and its token cost.
      Compared against the tbench.ai 2.0 leaderboard for context. No numeric
      gates (decision Q8).

Sanity (narrative):

- [ ] L4 (pi tools) vs L2 (Terminus 2) on the same local model isolates harness
      effect; either direction is a legitimate, reportable result.
- [ ] L5 vs L4 tests whether the repo's extension set helps a constrained local
      model; the token cost (from the llama-server log, since `pi-usage-extension`
      is DROP) makes the trade explicit.

## Notes / gotchas

- The adapter's in-container pi is independent of the repo's bwrap sandbox; do
  not expect `bwrap-pi.sh` semantics here. Sandboxing is provided by the task
  container itself.
- Keep the model endpoint identical across L2/L4/L5 (same bench profile, same
  toggles) so only the harness/extension axis varies.
- `pi-llama-cpp` (a zero-config local llama provider) would silently override
  the configured `OPENAI_BASE_URL` if installed — it is DROP in L5 for exactly
  this reason; the arm must hit the local bench server, not an auto-discovered
  provider. (The old `pi-ollama-cloud` reference is removed — that package is
  not in the recipe; the actual web tool is `pi-web-access`, also DROP.)
- TB 2.1 migration (future): bump `-d terminal-bench@2.1` and re-verify the
  adapter; out of scope now (decision A).
