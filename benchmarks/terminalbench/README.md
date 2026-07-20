# Terminal-Bench 2.1 (design docs 02 Terminus 2 + 04 pi adapter)

89 containerized tasks with in-container verifiers; a task passes iff its tests
pass. Metric: **task_pass@1**. All arms share the **identical pin**
(`pins/tb2-quick.txt`, 12 budget-safe mediums) so the comparisons are clean:

- **L1/L2 (doc 02)** — Harbor + **Terminus 2** (model-agnostic tmux agent, no
  native tool calling; how AA runs Terminal-Bench).
- **L4 (doc 04a)** — vanilla **pi** via badlogic's `pi-terminal-bench` adapter.
- **L5 (doc 04b)** — pi + ONLY `{pi-caveman, rtk}` (`install-extensions.sh`
  in-container); every other deployed extension is DROP.
- There is **no bare-pi L3** for TB (a pi with no bash scores zero); the
  harness-delta question is L2 (Terminus) vs L4 (pi) on the same pin.

Pins: **TB 2.1** — a more-verified iteration of 2.0 (same 89 tasks; 26 modified
to fix bugs, timeouts/resources, and reward-hacking, many from Z.ai's
"Terminal-Bench 2.0 Verified"). TB 2.1 ships as a **Harbor Hub package dataset**
under the slug `terminal-bench/terminal-bench-2-1` (source repo
`harbor-framework/terminal-bench-2-1`; the org rebranded from `laude-institute`).
It is pinned to the **immutable content digest**
`sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a` (the
package analogue of a git commit; resolved from `@latest` on 2026-07-20) and the
public dataset downloads **without `harbor auth login`**. Harbor pinned to
**0.19.0** (doc 02 V2): `-d terminal-bench/terminal-bench-2-1@<digest>`,
`-a terminus-2` / `-a <import:Class>`, `-i <task>` per pin, `--agent-env` to
inject the endpoint, `-k` attempts, `-o` jobs-dir. Task `agent_to` (900/750 for
the pinned set) binds via the default timeout multiplier.

## Requirements

- **Docker** at run time (doc 00 scope guard: pulled in ONLY here — `pixi run pi`
  must still work with the daemon stopped).
- Host↔container networking (doc 02 V2): the in-container agent reaches the host
  llama-server at `http://host.docker.internal:8080/v1`. Proof:
  ```bash
  docker run --rm --add-host host.docker.internal:host-gateway curlimages/curl \
    curl -s http://host.docker.internal:8080/health
  ```

## Setup

```bash
cd benchmarks/terminalbench
bash setup.sh          # isolated .venv + harbor + badlogic adapter (pinned)
```

## Run

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local
export BENCH_MODEL=Qwen3.6-35B-A3B BENCH_PRECISION="IQ4_XS+q8_0kv"

.venv/bin/python run.py --arm L2 --dry              # validate the JobConfig offline (no Docker)
.venv/bin/python run.py --arm L2                    # Terminus 2 (doc 02)
.venv/bin/python run.py --arm L4                    # vanilla pi (doc 04a)
.venv/bin/python run.py --arm L5                    # pi + {pi-caveman, rtk} (doc 04b)
```

The runner maps `localhost`→`host.docker.internal` for the in-container agent
endpoint (override with `BENCH_TB_AGENT_BASE_URL`), passes it via `--agent-env`,
filters to the pinned tasks with `-i`, runs single-concurrency (`-n 1`, one GPU),
parses per-task verdicts → `task_pass@1`, and writes a ledger entry + REPORT.
`REPORT-tb2-ladder.md` (after L1/L2/L4/L5) tabulates the ladder.

## Smoke checks

- **Offline JobConfig (no Docker):** `run.py --arm L2 --dry` prints the resolved
  Harbor JobConfig — validates dataset/agent/model/task-filter wiring. (Validated:
  `-d terminal-bench/terminal-bench-2-1@<digest> -a terminus-2 -i <task>` resolves
  correctly, and the digest-pinned public dataset downloads all 89 tasks.)
- **Connectivity proof** (above) before the first real run.
- **Live (L2):** one short task, e.g. `--arm L2 --limit 1` on `overfull-hbox`.

## Caveats (deferred to the operator — need Docker + hours)

- **Adapter ↔ Harbor version drift:** the badlogic adapter targets an older
  Harbor; its README Harbor patch + Docker `upload_dir` workaround must be
  verified/applied against Harbor 0.19 before the L4/L5 arms (record what was
  applied in the ledger). L1/L2 (Terminus 2, shipped with Harbor) are unaffected.
- **Result-schema parsing:** `parse_results` reads common Harbor trial-result
  fields defensively; confirm the exact field on the first real run.
- **L5 rtk:** `rtk` is conda-forge `rtk-cli`, **not** npm — `install-extensions.sh`
  installs it via conda/prebuilt binary inside the container, then `rtk init`.
- 12 tasks × up to 900 s ≈ ~3 h worst case at k=1 on one RTX 3080; a 4-bit local
  model will fail a fair share and sit near the cap. Calibrate (doc 00) first.
