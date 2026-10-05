#!/usr/bin/env python3
"""Benchmark whatever Strata is serving right now.

The model name is read off the live server's ``/v1/models``, so the run measures the model
that is actually loaded instead of one written down in ``pixi.toml``: change ``model`` in
``strata.ini``, restart, and this follows it. The port comes from ``strata.ini`` too
(``STRATA_PORT`` or ``--port`` override it; failing both, the port an installed run config was
prepared with).  A port that answers nothing is not the end of it: every other port an
installed run config records is tried too, so a server left running on the port strata.ini
had before the last edit is still the one that gets measured.

    pixi run llama-benchy-strata
    pixi run llama-benchy-strata -- --pp 4096 8192 --runs 5

Anything after ``--`` goes to llama-benchy itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

from strata_common import flag_value, installed_ports, resolve_port, strata_args

#: what this project asks llama-benchy for: no prefix cache to hit, 7 runs, 128 generated
#: tokens. Everything else is llama-benchy's own default.
BASE_ARGS = [
    "--no-cache",
    "--runs",
    "7",
    "--tg",
    "128",
    "--tokenizer",
    "Qwen/Qwen3.8-Flash-Next",
]


def models_at(base: str) -> list[dict]:
    """What the server lists at /v1/models."""
    with urllib.request.urlopen(f"{base}/models", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data")
    return (
        [model for model in data if isinstance(model, dict)]
        if isinstance(data, list)
        else []
    )


def pick(models: list[dict]) -> dict | None:
    """The model itself, preferring the one the server says is loaded.

    Strata lists an alias as its own entry beside the model it stands for (#297), each with
    an `alias_of`, and marks the model `loaded` or `unloaded` the way llama-server's router
    does. The aliases answer under their own name, so benchmarking one measures the same
    model with a worse label.
    """
    real = [model for model in models if not model.get("alias_of")] or models
    for model in real:
        if (model.get("status") or {}).get("value") == "loaded":
            return model
    return real[0] if real else None


def main() -> int:
    # pixi hands the `--` separator through, and llama-benchy has no use for it
    extra = [a for a in sys.argv[1:] if a != "--"]
    pairs = strata_args()

    ports: list[str] = []
    for port in [resolve_port(pairs), *installed_ports()]:
        if port and port not in ports:
            ports.append(str(port))
    if not ports:
        print(
            "no port to ask: set `port` in strata.ini, or start the server with "
            "pixi run start-strata",
            file=sys.stderr,
        )
        return 1

    tried: list[str] = []
    base = ""
    model: dict | None = None
    for port in ports:
        candidate = f"http://127.0.0.1:{port}/v1"
        try:
            models = models_at(candidate)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            tried.append(f"{candidate}: {reason}")
            continue
        found = pick(models)
        if found is None:
            tried.append(f"{candidate}: no model listed")
            continue
        if not any("meta" in m for m in models):
            # Strata's entries carry meta.n_ctx; a server without them is not Strata
            print(
                f"note: {candidate} does not answer like Strata (no meta.n_ctx)",
                file=sys.stderr,
            )
        base, model = candidate, found
        break

    if model is None:
        print("no Strata server answered:", file=sys.stderr)
        for line in tried:
            print(f"  {line}", file=sys.stderr)
        print(
            "start it with pixi run start-strata (the port is `port` in strata.ini, and "
            "STRATA_PORT or --port override it)",
            file=sys.stderr,
        )
        return 1

    model_id = str(model.get("id") or "")
    n_ctx = (model.get("meta") or {}).get("n_ctx")
    print(f"Strata at {base}: {model_id}" + (f", {n_ctx} tokens" if n_ctx else ""))

    argv = [*BASE_ARGS, "--base-url", base, "--model", model_id]
    api_key = flag_value(pairs, "--api-key")
    if api_key:
        argv += ["--api-key", api_key]
    argv += extra
    return subprocess.run(
        [sys.executable, "-m", "llama_benchy", *argv], check=False
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
