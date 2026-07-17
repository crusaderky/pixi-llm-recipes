#!/usr/bin/env python3
"""Look up a model's training-data cutoff in model-cutoffs.toml.

Usage:
  python lookup_cutoff.py <model_id>          # CLI: prints cutoff<TAB>confidence<TAB>source
  from lookup_cutoff import lookup            # programmatic: returns the entry dict or None

- Exit 0 with the cutoff line if the model is found.
- Exit 2 with update instructions if the model is NOT in the table (CRASH, no
  silent fallback).
- A found entry with cutoff == "unknown" is NOT a crash: it means the vendor
  did not publish a cutoff. The caller must treat the contamination posture as
  void (warn the user); only a MISSING model crashes.

Normalization strips the HF repo prefix, a trailing ":quant" GGUF tag, and
common suffixes (-bench, -iq4_xs, -q4_k_m, -it, ...) so that
"Qwen3.6-35B-A3B-bench", "Qwen3.6-35B-A3B", and
"byteshape/Qwen3.6-35B-A3B-MTP-GGUF:Qwen3.6-35B-A3B-IQ4_XS-3.97bpw" all resolve
to the same entry.
"""

import pathlib
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

TABLE = pathlib.Path(__file__).with_name("model-cutoffs.toml")

_SUFFIXES = [
    "-bench",
    "-gguf",
    "-mtp-gguf",
    "-mtp",
    "-iq4_xs",
    "-iq4_nl",
    "-q2_k",
    "-q3_k_m",
    "-q4_k_m",
    "-q4_k_s",
    "-q5_k_m",
    "-q6_k",
    "-q8_0",
    "-f16",
    "-bf16",
    "-3.97bpw",
    "-it",
    "-instruct",
    "-reasoning",
    "-base",
]


def normalize(mid: str) -> str:
    mid = mid.lower()
    mid = mid.split("/")[-1]  # strip HF repo prefix
    mid = mid.split(":")[0]  # strip GGUF ":quant" tag
    changed = True
    while changed:  # strip stacked suffixes
        changed = False
        for tok in _SUFFIXES:
            if mid.endswith(tok):
                mid = mid[: -len(tok)]
                changed = True
    return mid.strip()


def _load():
    with open(TABLE, "rb") as f:
        return tomllib.load(f)


def lookup(mid: str):
    """Return the matching [[models]] dict, or None if not found."""
    norm = normalize(mid)
    for m in _load()["models"]:
        keys = {normalize(m["key"])} | {normalize(a) for a in m.get("aliases", [])}
        if norm in keys:
            return m
    return None


def main():
    if len(sys.argv) != 2:
        print("usage: lookup_cutoff.py <model_id>", file=sys.stderr)
        sys.exit(2)
    mid = sys.argv[1]
    m = lookup(mid)
    if m is None:
        norm = normalize(mid)
        print(
            f"ERROR: model {mid!r} (normalized {norm!r}) not found in {TABLE.name}.",
            file=sys.stderr,
        )
        print(
            f"To fix: append a [[models]] block to {TABLE} with:\n"
            f'  key = "{norm}"\n'
            f'  cutoff = <YYYY-MM-DD | "unknown">   # training-data cutoff\n'
            f'  released = <YYYY-MM-DD | "unknown">\n'
            f'  confidence = "documented" | "year-level" | "proxy" | "inherited" | "unknown"\n'
            f'  source = "<url or note>"\n'
            f"  aliases = [...]   # optional alternate spellings\n"
            f"Then re-run.",
            file=sys.stderr,
        )
        sys.exit(2)
    print(f"{m['cutoff']}\t{m['confidence']}\t{m.get('source', '')}")


if __name__ == "__main__":
    main()
