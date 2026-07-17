"""Resolve a model's training-data cutoff via docs/benchmarks/lookup_cutoff.py.

Mirrors the doc-00 contract exactly:
  - a MISSING model crashes (exit 2) with instructions to add a [[models]] block;
  - a found entry whose ``cutoff == "unknown"`` is returned normally — the caller
    must treat the contamination posture as void (warn), it is NOT a crash.
"""

import importlib.util
import sys

from . import CUTOFF_LOOKUP

_mod_cache = None


def _mod():
    global _mod_cache
    if _mod_cache is None:
        spec = importlib.util.spec_from_file_location("lookup_cutoff", CUTOFF_LOOKUP)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _mod_cache = m
    return _mod_cache


def resolve(model_id: str) -> dict:
    """Return the matching [[models]] entry dict, or crash (exit 2) if missing."""
    m = _mod()
    entry = m.lookup(model_id)
    if entry is None:
        norm = m.normalize(model_id)
        print(
            f"ERROR: model {model_id!r} (normalized {norm!r}) not found in "
            f"{CUTOFF_LOOKUP.parent / 'model-cutoffs.toml'}.",
            file=sys.stderr,
        )
        print(
            "To fix: append a [[models]] block (key, cutoff, released, "
            "confidence, source, optional aliases) — see the file header — and "
            "re-run. The lookup does NOT fall back silently (doc 00).",
            file=sys.stderr,
        )
        sys.exit(2)
    return entry


def is_posture_void(entry: dict) -> bool:
    """True if the cutoff is unusable (``unknown``) => contamination posture void."""
    return str(entry.get("cutoff")).lower() == "unknown"
