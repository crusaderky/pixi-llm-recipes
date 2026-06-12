# 02 — Terminal-Bench 2.0 via the canonical harness (Harbor + Terminus 2)

Status: DESIGN. Depends on: `00-common-infrastructure.md`.

Implements ladder arms **L1-remote-canonical** and **L2-local-canonical** for
Terminal-Bench 2.0, using Terminus 2 as the model-agnostic agent. (pi-adapter
arms are in doc 04.)

## What TB 2.0 is, precisely

89 tasks, each a Docker container with a natural-language instruction and an
in-container verification test suite. The agent acts by sending shell commands
and reading output; a task passes iff all its tests pass. Terminus 2 is the
benchmark's own minimal agent: it gives the model a tmux session and parses
terminal output as text — **no native tool/function calling** (consistent with
how Artificial Analysis runs Terminal-Bench Hard). Terminus drives the model
through LiteLLM, so any OpenAI-compatible endpoint works via `api_base`.

External reference (decision U4): the **tbench.ai `terminal-bench@2.0`
leaderboard**, Terminus-class entries. (Artificial Analysis's "Terminal-Bench
Hard" is the *old 1.x* 44-task subset and is NOT comparable to TB 2.0 — cite it
only as loose directional context if at all.)

Pin: **TB 2.0** (decision A), tasks via Harbor registry
`terminal-bench@2.0` → `laude-institute/terminal-bench-2` @ `69671fba…`.

## Why Docker is unavoidable here

TB tasks *are* containerized environments with in-container verifiers; there is
no no-Docker path (decision C3 accepts a host Docker daemon, scoped to tests).
The repo's no-Docker philosophy is preserved for everything else; doc 00's
scope-guard check enforces that `pixi run pi` still works with the daemon down.

## Setup

```bash
# uv-based, per Harbor/terminal-bench docs
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install harbor          # provides `harbor run`
# Terminus 2 ships with terminal-bench/harbor; no extra agent install needed.
```

The benchmark harness itself can live in its own pixi feature/environment (e.g.
`feature.bench` with `uv`, `harbor`) so it is pinned with the rest of the repo,
OR be installed ad hoc — implementer's choice, but if pixi-managed it must NOT
pull Docker into any default environment (scope guard).

### Host networking (verification item V2)

Terminus runs outside the task container but the model endpoint is the host
llama-server. The container/agent must reach `http://host.docker.internal:8080/v1`.
Resolve V2 at implementation time:

```bash
# one-off connectivity proof
docker run --rm --add-host host.docker.internal:host-gateway curlimages/curl \
  curl -s http://host.docker.internal:8080/health
```

Then ensure Harbor passes the same host mapping (and the agent's
`OPENAI_BASE_URL`/LiteLLM `api_base`) into the run. Document the exact flags
once verified.

### L1 — remote anchor (BF16/FP16)

```bash
harbor run \
  -d terminal-bench@2.0 \
  -a terminus-2 \
  -m openai/Qwen3.6-35B-A3B \
  --task-ids-file docs/benchmarks/pins/tb2-quick.txt \
  -n 1 \
  --jobs-dir docs/benchmarks/results/tb2-L1
```

- `-m` uses LiteLLM model routing; for an OpenAI-compatible vendor set
  `OPENAI_API_KEY` + the vendor base URL (LiteLLM `api_base`/env).
- Anchor may run the full 89 with `--k 3` if desired and time allows; for
  parity with L2 the default is the same pin + `-n 1`.
- Exact Harbor flag names (`-a`/`--agent`, task-id input form, jobs dir) must
  be confirmed against the installed Harbor version; the names above match the
  pi-terminal-bench README's `harbor run` usage and may need minor adjustment.

### L2 — local canonical (bench profile)

```bash
pixi r -e llamacpp-source-cuda start-server      # Qwen3.6-35B-A3B-bench, single slot
# point LiteLLM at the local server (OpenAI-compatible):
export OPENAI_API_KEY="sk-local"
export OPENAI_BASE_URL="http://host.docker.internal:8080/v1"
harbor run \
  -d terminal-bench@2.0 \
  -a terminus-2 \
  -m openai/Qwen3.6-35B-A3B-bench \
  --task-ids-file docs/benchmarks/pins/tb2-quick.txt \
  -n 1 \
  --agent-timeout 1200 \                          # runner-level per-task cap (doc 00)
  --jobs-dir docs/benchmarks/results/tb2-L2
```

- Single llama-server slot (no `--parallel`): agentic episodes need the full
  262144 context, and concurrent TB tasks would contend for one GPU anyway.
- TB tasks run sequentially on one 10GB GPU. Default **1 repeat**; `--k 3`
  toggle for headline.
- Use the **pinned subset** by default; uncomment the pin for the full 89.
- `--agent-timeout 1200` bounds the worst case; confirm the flag name/behavior
  in the installed Harbor (the intent is a downward per-task cap).

## Wall-time plan

- 12 pinned tasks × up to 20 min (1200s cap) = up to ~4h worst case at n=1 —
  the top of budget. Most tasks finish sooner on success; *failing* tasks tend
  to burn the full cap, and a 4-bit local 35B-A3B will fail a fair share of TB
  tasks, so expect to sit near the cap.
- Mandatory calibration: run 2–3 of the shortest pinned tasks
  (`overfull-hbox`, `fix-git`, `prove-plus-comm`), measure wall time, and if
  the per-task mean projects the 12-task set past 4h, cut the pin to ~8 tasks
  (comment out the heaviest-category mediums). Record the cut.
- Docker image pulls/builds are one-time but can be slow on first run; do a
  warm-up pull of the pinned tasks' images before timing (the build/pull time
  is not part of the model-capability signal).

## Pass criteria (optimized to minimize implementer run time)

Smoke-level (minutes, before any real run):

- [ ] Host→container connectivity proof (curl `/health`) passes (V2).
- [ ] A single short task (`overfull-hbox`) runs end-to-end against L2,
      produces a Harbor `result.json`, and the score parses.

Arm-complete:

- [ ] L2 pinned-subset run finishes within budget; per-task pass/fail + suite
      pass@1 recorded; ledger entry + `REPORT-<run_id>.md`.
- [ ] L1 pinned-subset (or full, if time allows) run finishes; pass@1 recorded.
- [ ] `REPORT` highlights obtained pass@1 and compares to the tbench.ai 2.0
      leaderboard (Terminus-class) and notes the subset/model differences. No
      numeric gate (decision Q8).

Sanity (narrative):

- [ ] L1 (BF16) on the pinned subset is plausibly in line with where a model
      of this class sits on the 2.0 board (directional only — subset ≠ full
      suite). A large local-vs-remote gap (L2 ≪ L1) is the expected, reportable
      effect of quantization + KV compression on long agentic episodes.

## Notes / gotchas

- Terminus 2 ≠ pi. This doc's harness delta vs doc 04 is "Terminus 2 (text
  tmux protocol, no native tools)" vs "pi (read/write/edit/bash tools)". Both
  use the identical pinned task set so the comparison is clean.
- `allow_internet` is per-task metadata baked into the benchmark; do not
  override it. (This is distinct from the model's own web tools, which are
  disabled — irrelevant here since Terminus gives the model no web tool.)
- Some pinned tasks (`qemu-*`) were deliberately excluded as borderline-heavy;
  if a different machine has spare time, uncomment and recalibrate.
- Keep `llama-server.log` for each run_id alongside the Harbor jobs dir.
