"""D1 endpoint plumbing (doc 05): OpenAI-compatible /v1 access via env vars.

Standard two-variable switch (identical for every harness in the panel):

    export OPENAI_BASE_URL="http://localhost:8080/v1"   # L2 local (default)
    export OPENAI_API_KEY="sk-local"                    # llama-server ignores it
    export BENCH_MODEL="Qwen3.6-35B-A3B"                # your models.ini preset

For L1 point OPENAI_BASE_URL/OPENAI_API_KEY at the reference endpoint. The
``openai`` import is lazy so this module can be imported without the dependency
present (only :func:`client`/:func:`smoke` need it).
"""

import os

DEFAULT_BASE = "http://localhost:8080/v1"
DEFAULT_KEY = "sk-local"


def env(default_base: str = DEFAULT_BASE, default_key: str = DEFAULT_KEY) -> dict:
    return {
        "base_url": os.environ.get("OPENAI_BASE_URL", default_base),
        "api_key": os.environ.get("OPENAI_API_KEY", default_key),
        "model": os.environ.get("BENCH_MODEL", ""),
    }


def deployment() -> str:
    """'local' if the endpoint is loopback, else 'remote' (ledger model.deployment)."""
    base = env()["base_url"]
    return "local" if ("localhost" in base or "127.0.0.1" in base) else "remote"


def precision() -> str:
    """Precision label for the ledger model.precision (doc 00), from BENCH_PRECISION.

    Any value is valid incl. 'unknown' — the point is that it is recorded. For an
    L1 remote arm set it to the reference endpoint's asserted precision.
    """
    return os.environ.get("BENCH_PRECISION", "unknown")


def client(base_url: str | None = None, api_key: str | None = None):
    from openai import OpenAI  # lazy: only harnesses that generate directly need it

    e = env()
    return OpenAI(base_url=base_url or e["base_url"], api_key=api_key or e["api_key"])


def smoke(
    model: str | None = None, prompt: str = "Reply with exactly: OK", **kw
) -> str:
    """One-shot completion proving connectivity (doc 05 D1 pass criterion)."""
    e = env()
    r = client().chat.completions.create(
        model=model or e["model"],
        messages=[{"role": "user", "content": prompt}],
        max_tokens=kw.get("max_tokens", 64),
        temperature=kw.get("temperature", 0.6),
    )
    return r.choices[0].message.content or ""


if __name__ == "__main__":
    e = env()
    print(f"endpoint: {e['base_url']}  model: {e['model'] or '(BENCH_MODEL unset)'}")
    print("reply:", smoke())
