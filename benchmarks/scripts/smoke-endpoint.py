#!/usr/bin/env python3
"""D1 smoke: a one-line OpenAI completion against the configured endpoint.

    python benchmarks/scripts/smoke-endpoint.py

Reads OPENAI_BASE_URL / OPENAI_API_KEY / BENCH_MODEL (doc 05 D1). Prints the
endpoint, model, and the model's reply. Exit 0 on a completion, non-zero on
failure. Requires `openai` on the path (any harness venv, or the pixi `agents`
env). Proves generation plumbing before a full run.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
from benchlib import endpoint  # noqa: E402


def main() -> int:
    e = endpoint.env()
    if not e["model"]:
        print(
            "BENCH_MODEL is unset — set it to your models.ini preset.", file=sys.stderr
        )
        return 2
    print(f"endpoint: {e['base_url']}")
    print(f"model:    {e['model']}  ({endpoint.deployment()})")
    try:
        reply = endpoint.smoke()
    except Exception as exc:  # noqa: BLE001 — smoke wants the raw failure
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"reply:    {reply!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
