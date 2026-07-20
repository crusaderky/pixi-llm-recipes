# 02 — Terminal-Bench 2.1 via the canonical harness (Harbor + Terminus 2)

Status: DESIGN.

## Dependency chain

- **Depends on:** `00-common-infrastructure.md` (Docker scope guard, bench
  profile, ledger, calibration, hygiene).
- **Shares with `04`:** the TB 2.1 pin (`pins/tb2-quick.txt`), Docker setup,
  and host-networking (V2). Doc 04's pi-adapter arms run on the **identical
  pinned task set** so the Terminus-vs-pi comparison is clean.
- **Unblocks:** nothing (leaf); doc 04 inherits this doc's Docker/networking.

Implements ladder arms **L1-remote-canonical** and **L2-local-canonical** for
Terminal-Bench 2.1, using Terminus 2 as the model-agnostic agent. (pi-adapter
arms are in doc 04.)

## External dependencies (require user input)

- **Docker daemon on the host** — required for TB task containers (decision C3
  scopes it to the Harbor harness only; doc 00's scope guard ensures
  day-to-day `pixi run pi` still works with the daemon stopped).
- **Reference endpoint + API key** (L1 only) — per doc 00; user supplies
  endpoint URL + model id + explicit precision label (no vendor certification).
- **Harbor flag confirmation** — exact `harbor run` flag names
  (`-a`/`--agent`, task-id input form, `--agent-timeout` behavior, jobs dir)
  must be confirmed against the installed Harbor version at implementation
  time; the names below match the pi-terminal-bench README and may need minor
  adjustment. (Implementation item V2.)

## What TB 2.1 is, precisely

89 tasks, each a Docker container with a natural-language instruction and an
in-container verification test suite. The agent acts by sending shell commands
and reading output; a task passes iff all its tests pass. Terminus 2 is the
benchmark's own minimal agent: it gives the model a tmux session and parses
terminal output as text — **no native tool/function calling** (consistent with
how Artificial Analysis runs Terminal-Bench Hard). Terminus drives the model
through LiteLLM, so any OpenAI-compatible endpoint works via `api_base`.

External reference (decision U4): the **tbench.ai Terminal-Bench 2.1
leaderboard**, Terminus-class entries. (Artificial Analysis's "Terminal-Bench
Hard" is the _old 1.x_ 44-task subset and is NOT comparable to TB 2.1 — cite it
only as loose directional context if at all.)

Pin: **TB 2.1** (decision A) — a more-verified iteration of 2.0 (same 89 tasks;
26 modified to fix bugs, timeouts/resources, and reward-hacking). TB 2.1 is a
**Harbor Hub package dataset** `terminal-bench/terminal-bench-2-1` (source repo
`harbor-framework/terminal-bench-2-1`; the org rebranded from `laude-institute`),
pinned to the immutable content digest
`sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a`
(resolved from `@latest` on 2026-07-20). The public dataset resolves and
downloads with **no `harbor auth login`**.

## Why Docker is unavoidable here

TB tasks _are_ containerized environments with in-container verifiers; there is
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

### L1 — reference endpoint (precision labelled)

```bash
harbor run \
  -d terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a \
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
pixi r -e llamacpp-source-cuda start-server      # your bench preset (models.ini), single slot
# point LiteLLM at the local server (OpenAI-compatible):
export OPENAI_API_KEY="sk-local"
export OPENAI_BASE_URL="http://host.docker.internal:8080/v1"
export BENCH_MODEL="Qwen3.6-35B-A3B"             # your models.ini preset; record in ledger model.preset
harbor run \
  -d terminal-bench/terminal-bench-2-1@sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a \
  -a terminus-2 \
  -m openai/$BENCH_MODEL \
  --task-ids-file docs/benchmarks/pins/tb2-quick.txt \
  -n 1 \
  --agent-timeout 900 \                           # runner-level per-task cap = default pin max task agent_to (doc 00 budget)
  --jobs-dir docs/benchmarks/results/tb2-L2
```

- Single llama-server slot (no `--parallel`): agentic episodes need the full
  262144 context, and concurrent TB tasks would contend for one GPU anyway.
- TB tasks run sequentially on one 10GB GPU. Default **1 repeat**; `--k 3`
  toggle for headline.
- Use the **pinned subset** by default; uncomment the pin for the full 89.
- `--agent-timeout 900` bounds the worst case; it equals the default pin's
  max task `agent_to` (all included tasks are 900/750), so it binds as a true
  ceiling. Confirm the flag name/behavior in the installed Harbor.

## Wall-time plan

- 12 pinned tasks × up to 15 min (900s cap) = up to ~3 h worst case at n=1 —
  inside the 1–4 h budget. Most tasks finish sooner on success; _failing_
  tasks tend to burn the full cap, and a 4-bit local 35B-A3B will fail a fair
  share of TB tasks, so expect to sit near the cap.
- The 900 s runner cap equals the default pin's maximum task `agent_to` (all
  included tasks are 900 or 750), so it binds as a true ceiling for the default
  pin. If you uncomment heavier tasks with larger `agent_to`, raise the runner
  cap to match or drop it and let the task's own `agent_to` bind — record the
  choice in the ledger.
- Mandatory calibration: run 2–3 of the shortest pinned tasks
  (`overfull-hbox`, `fix-git`, `prove-plus-comm`), measure wall time, and if
  the per-task mean projects the 12-task set past 4 h, cut the pin to ~8 tasks
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
- [ ] `REPORT` prints the one-line summary
      `<URL> / <model> -> <task_pass@1>  (mean <mean_s>s/task, n=<N>)`
      (doc 00) and compares the score to the tbench.ai 2.1 leaderboard
      (Terminus-class), noting the subset/model differences. No numeric gate
      (decision Q8).

Sanity (narrative):

- [ ] L1 (at the labelled precision) on the pinned subset is plausibly in
      line with where a model of this class sits on the 2.1 board (directional
      only — subset ≠ full suite, and only meaningful when L1 is the same
      model at a comparable precision). A large local-vs-remote gap (L2 ≪ L1)
      is the expected, reportable effect of quantization + KV compression on
      long agentic episodes.

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
