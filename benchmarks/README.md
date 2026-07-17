# benchmarks/ — coding-benchmark harnesses

Executable implementations of the benchmark **design docs** in
[`docs/benchmarks/`](../docs/benchmarks/) (read `00` and `05` first). The design
docs own the _contracts_ (ladder arms, ledger schema, pins, cutoff table,
hygiene); this tree owns the _code_ that runs them.

```
benchmarks/
├── scripts/
│   ├── bench-sandbox.sh     # doc 05 D2 — untrusted-code exec sandbox (bwrap, no net)
│   └── smoke-endpoint.py    # doc 05 D1 — one-line OpenAI completion smoke
├── lib/benchlib/            # shared library imported by every runner (by path)
│   ├── pins.py     endpoint.py   ledger.py     report.py
│   ├── timing.py   cutoff.py     sandbox.py
├── scicode/                 # docs 01 (canonical/inspect_ai) + 03 (pi ladder L3–L5)
├── terminalbench/           # docs 02 (Terminus 2) + 04 (pi adapter)
├── livecodebench/           # doc 06
├── ifbench/                 # doc 07
├── evalplus/                # doc 08
└── cruxeval/                # doc 09
```

State the docs reference stays under `docs/benchmarks/`: the pins (`pins/`), the
run ledger + checker (`results/`), and the model-cutoff table
(`model-cutoffs.toml` + `lookup_cutoff.py`).

## Harness isolation (doc 05)

Each benchmark installs into its **own** venv — their pins conflict, so they are
never installed together and never into the shared pixi env. Every `setup.sh`
creates an isolated venv with [`uv`](https://docs.astral.sh/uv/) (install the
standalone build: `curl -LsSf https://astral.sh/uv/install.sh | sh` — the snap
build is unreliable on some hosts). The shared pixi `agents`/`pytools` env is
used only for the tiny OpenAI smoke calls and `check_ledger.py`.

## Endpoint plumbing (doc 05 D1)

Every runner talks to an OpenAI-compatible `/v1` endpoint via three env vars.
Copy `.env.example` to `.env` and fill it in, or export them:

```bash
export OPENAI_BASE_URL="http://localhost:8080/v1"   # L2 local (default)
export OPENAI_API_KEY="sk-local"                    # llama-server ignores it
export BENCH_MODEL="Qwen3.6-35B-A3B"                # your models.ini preset
python benchmarks/scripts/smoke-endpoint.py         # prove connectivity
```

For an **L1** reference arm, point the same vars at the reference endpoint and
record its precision label (doc 00). L1 arms are wired but require a
user-supplied endpoint + key.

## Per-benchmark usage

Each subdirectory has its own `README.md` with a `setup.sh` step (creates the
venv, fetches the pinned harness + data) and runner invocations for its arms.
Every scored run appends one entry to `docs/benchmarks/results/runs.jsonl`
(validated by `results/check_ledger.py`) and writes a `REPORT-<run_id>.md`.

## The shared library (`lib/benchlib`)

Imported by path (runners run in isolated venvs, so benchlib is not installed):

```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
from benchlib import ledger, report, pins, timing, endpoint, cutoff, sandbox
```

| module     | purpose                                                                     |
| ---------- | --------------------------------------------------------------------------- |
| `pins`     | parse `docs/benchmarks/pins/*.txt` (list + key=value shapes); pin sha256    |
| `endpoint` | D1 env plumbing; OpenAI client; one-shot connectivity smoke                 |
| `cutoff`   | resolve a model's training cutoff via `lookup_cutoff.py` (crash if missing) |
| `timing`   | per-item wall-time → `{n, mean_s, median_s, min_s, max_s}` (ledger)         |
| `ledger`   | build + **validate** + append a doc-00 ledger entry                         |
| `report`   | the doc-00 one-line arm summary + `REPORT-<run_id>.md` writer               |
| `sandbox`  | run graded code through `scripts/bench-sandbox.sh`                          |

Only `endpoint.client`/`endpoint.smoke` need `openai`; the rest is stdlib-only.
