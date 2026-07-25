#!/usr/bin/env python3
"""
Parse perplexity.log, extract -ctk / -ctv and per-chunk KL Divergence,
generate HTML and/or Markdown report with table + log-scale line plot.

HTML report: interactive Chart.js plot.
Markdown report: static SVG plot (via matplotlib) + cross-ref link to HTML.

Usage:

1. Modify kv-perplexity.yaml
2. pixi r kv-perplexity
3. pixi r kv-kld-report
"""

import argparse
import html as html_mod
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
#  CDN fetch helper
# ---------------------------------------------------------------------------
CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"


def _fetch_chart_js() -> str:
    """Fetch Chart.js from CDN for offline embedding. Falls back to CDN script tag on failure."""
    try:
        with urllib.request.urlopen(CHART_JS_CDN, timeout=10) as resp:
            content = resp.read().decode("utf-8")
        return f"<script>{content}</script>"
    except Exception as e:
        print(f"Warning: could not fetch Chart.js ({e}), using CDN", file=sys.stderr)
        return f'<script src="{CHART_JS_CDN}"></script>'


# ---------------------------------------------------------------------------
#  Bits-per-weight (bpw) conversion table.
#  Values are flat bpw.  The inline `=> N/M` derivations are the on-disk block
#  layout in bits (total block bits / block values), so they equal the bpw.
#  Includes block-overhead where relevant.
# ---------------------------------------------------------------------------
BPW = {
    "f32": 32.0,  # 32-bit float
    "f16": 16.0,  # 16-bit float
    "bf16": 16.0,  # bfloat16
    "q8_0": 8.5,  # d(fp16)=16 + qs(8b*32)=256 => 272/32
    "q5_1": 6.0,  # d(fp16)=16 + m(fp16)=16 + qh(1b*32)=32 + qs(4b*32)=128 => 192/32
    "q5_0": 5.5,  # d(fp16)=16 + qh(1b*32)=32 + qs(4b*32)=128 => 176/32
    "q4_1": 5.0,  # d(fp16)=16 + m(fp16)=16 + qs(4b*32)=128 => 160/32
    "q4_0": 4.5,  # d(fp16)=16 + qs(4b*32)=128 => 144/32
    "iq4_nl": 4.5,  # d(fp16)=16 + qs(4b*32)=128 => 144/32 (same size as q4_0)
    # beellama.cpp 0.4.0 low/high-bit KV quants (block of 32 unless noted;
    # d/m = fp16 scale/min).  Sizes verified against ggml-common.h static_asserts.
    "q2_0": 2.25,  # QK2_0=64: d(fp16)=16 + qs(2b*64)=128 => 144/64
    "q2_1": 3.0,  # d(fp16)=16 + m(fp16)=16 + qs(2b*32)=64 => 96/32
    "q3_0": 3.5,  # d(fp16)=16 + qs(3b*32)=96 => 112/32
    "q3_1": 4.0,  # d(fp16)=16 + m(fp16)=16 + qs(3b*32)=96 => 128/32
    "q6_0": 6.5,  # d(fp16)=16 + qs(6b*32)=192 => 208/32
    "q6_1": 7.0,  # d(fp16)=16 + m(fp16)=16 + qs(6b*32)=192 => 224/32
    # beellama.cpp KVarN N-bit cache: 128x128 tile, N-bit payload + per-row/col
    # fp16 scales.  Per element = (16384*N + 6144) / 16384 = N + 3/8 bpw (K==V).
    "kvarn2": 2.375,  # 2 + 3/8
    "kvarn3": 3.375,  # 3 + 3/8
    "kvarn4": 4.375,  # 4 + 3/8
    "kvarn5": 5.375,  # 5 + 3/8
    "kvarn6": 6.375,  # 6 + 3/8
    "kvarn8": 8.375,  # 8 + 3/8
    # TheTom's TurboQuant
    "turbo4": 4.25,  # 4.25 bits/val
    "turbo3": 3.5,  # 3.5 bits/val
    "turbo2": 2.5,  # 2.5 bits/val
    # K-quant types (block of 256) — not used for KV cache but kept for reference
    "q2_k": 2.5,
    "q3_k_s": 2.5,
    "q3_k_m": 3.0,
    "q3_k_l": 3.5,
    "q4_k_s": 4.0,
    "q4_k_m": 4.5,
    "q5_k_s": 5.0,
    "q5_k_m": 5.5,
    "q6_k": 6.0,
    "tq3_1s": 4.0,
    "tq4_1s": 5.0,
}

BPW_LOOKUP = {k.lower(): v for k, v in BPW.items()}


def resolve_bpw(name: str) -> float:
    key = name.strip().lower()
    if key in BPW_LOOKUP:
        return BPW_LOOKUP[key]
    print(f"WARNING: unknown quant '{name}', using 8.0 bpw", file=sys.stderr)
    return 8.0


# ---------------------------------------------------------------------------
#  Per-model KV-cache geometry -> "Context (MiB) @ 256k" column.
#
#  The KV-cache VRAM of a run does not follow from bpw alone. ModelKV captures
#  the model's layer geometry and models beellama v0.4.1's persistent KV-cache
#  allocation (commits cc71513 + e289bb8, "compact SWA precision-tail storage"):
#    * full-attention layers keep a shared quant body over the whole context
#      (kv_unified) plus a per-sequence f16 exact-tail overlay of (N + R) rows;
#    * a sliding-window group whose tail covers its window is a bodyless exact
#      f16 ring of (window + R) rows per sequence; otherwise a quant body over
#      the window plus a per-sequence (tail + R) f16 overlay.
#  N is the resolved tail, R the rollback horizon (=1 whenever a tail exists;
#  llama-kv-cache-tail.cpp: persistent rows = (N + R) * n_seq_max, sink = 0).
#  The pre-v0.4.1 per-stream sink / batch / ubatch / tile padding are now
#  graph-local (transient) and are NOT counted here. The overlay scales with the
#  tail and n_parallel, so the figure is deployment-dependent: the report
#  evaluates at a chosen context (--ctx-size, default 256k) and n_parallel
#  (--n-parallel, default 4 = llama-server's auto). Derived from reading the
#  v0.4.1 source; NOT yet re-validated against v0.4.1 llama-server logs.
#
#  Geometry values were read from the GGUF headers of the models pinned in
#  models.ini (via scripts/gguf-meta-extract.py).
# ---------------------------------------------------------------------------
DEFAULT_CTX_SIZE = 262144  # 256k tokens (256 * 1024); override with --ctx-size
BYTES_PER_MIB = 1 << 20
F16_BPW = 16  # bits per f16 cache element
# v0.4.1 compact precision-tail: persistent exact rows = (N + R) per sequence,
# R = rollback horizon (cparams kv_tail_rollback_tokens; defaults to 1 when a
# tail is present). The sink / ubatch / tile padding are graph-local, not here.
KV_TAIL_ROLLBACK = 1


def parse_ctx_size(text: str) -> int:
    """Parse a context size like ``256k``, ``1M`` or ``262144`` into tokens."""
    s = text.strip().lower()
    mult = 1
    if s.endswith("k"):
        mult, s = 1024, s[:-1]
    elif s.endswith("m"):
        mult, s = 1024 * 1024, s[:-1]
    return int(float(s) * mult)


def _fmt_ctx_label(n_ctx: int) -> str:
    """Compact label for a context size: 262144 -> ``256k``, 1048576 -> ``1M``."""
    if n_ctx % (1024 * 1024) == 0:
        return f"{n_ctx // (1024 * 1024)}M"
    if n_ctx % 1024 == 0:
        return f"{n_ctx // 1024}k"
    return str(n_ctx)


class ModelKV(NamedTuple):
    """KV-cache geometry of a model: enough to size its cache at any context.

    Attention layers fall into two groups. Full-attention layers cache the whole
    context; sliding-window (SWA) layers cache only the most recent
    ``sliding_window_size`` tokens. The KV-head count is uniform within a group
    but may differ between groups -- e.g. Gemma's global (full) layers use fewer
    KV heads than its local (sliding) layers -- so each group carries its own
    count. Layers with no KV cache (hybrid conv/recurrent layers) are left out of
    both counts.
    """

    full_attn_layers: int
    full_attn_kv_heads: int  # KV heads per full-attention layer
    sliding_window_layers: int
    sliding_window_kv_heads: int  # KV heads per sliding-window layer
    sliding_window_size: int  # tokens; 0 when the model has no SWA
    key_dim: int  # per-head key dimension
    value_dim: int  # per-head value dimension

    def _exact_rows(self, exact_tokens: int, n_parallel: int) -> int:
        """Persistent exact-tail rows for a group: (N + R) per sequence
        (kv_unified). In v0.4.1 the sink and current-ubatch padding are
        graph-local, so they add no persistent rows."""
        return (exact_tokens + KV_TAIL_ROLLBACK) * n_parallel

    def _full_group_bytes(
        self, ctx_size: int, bpw_k: float, bpw_v: float, tail: int, n_parallel: int
    ) -> float:
        """Unified full-attention group: a shared quant body over the whole
        context, plus a per-sequence f16 exact-tail overlay of (tail + R) rows
        (absent for an exact f16 body or a zero tail)."""
        layers, heads = self.full_attn_layers, self.full_attn_kv_heads

        def side(head_dim: int, bpw: float) -> float:
            elems = layers * heads * head_dim
            body = elems * ctx_size * bpw / 8
            if bpw >= F16_BPW or tail <= 0:  # exact body -> no overlay
                return body
            return body + elems * self._exact_rows(tail, n_parallel) * F16_BPW / 8

        return side(self.key_dim, bpw_k) + side(self.value_dim, bpw_v)

    def _swa_group_bytes(
        self, ctx_size: int, bpw_k: float, bpw_v: float, tail: int, n_parallel: int
    ) -> float:
        """Sliding-window group. When the tail covers the window (or the body is
        exact f16) it is a bodyless exact f16 ring of (window + R) rows per
        sequence; otherwise a quant body over the window plus a per-sequence
        (tail + R) f16 overlay."""
        layers, heads = self.sliding_window_layers, self.sliding_window_kv_heads
        window = min(ctx_size, self.sliding_window_size)

        def side(head_dim: int, bpw: float) -> float:
            elems = layers * heads * head_dim
            if tail >= window or bpw >= F16_BPW:  # bodyless native-exact ring
                return elems * self._exact_rows(window, n_parallel) * F16_BPW / 8
            body = elems * window * bpw / 8  # quant body over the window
            if tail <= 0:  # no exact tail -> body only
                return body
            return body + elems * self._exact_rows(tail, n_parallel) * F16_BPW / 8

        return side(self.key_dim, bpw_k) + side(self.value_dim, bpw_v)

    def get_total_kv_cache_size(
        self,
        ctx_size: int,
        bpw_k: float,
        bpw_v: float,
        kv_tail_tokens: int,
        n_parallel: int,
    ) -> float:
        """Total K+V cache bytes at ``ctx_size`` tokens for ``n_parallel``
        parallel sequences, modelling llama-server's actual allocation. K/V quant
        widths are ``bpw_k`` / ``bpw_v`` (bits per element)."""
        total = self._full_group_bytes(
            ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
        )
        if self.sliding_window_layers:
            total += self._swa_group_bytes(
                ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
            )
        return total

    def cache_breakdown(
        self,
        ctx_size: int,
        bpw_k: float,
        bpw_v: float,
        kv_tail_tokens: int,
        n_parallel: int,
    ):
        """Per-group derivation for the tooltip: a list of
        (name, layers, kv_heads, note, bytes)."""
        lossy = max(bpw_k, bpw_v) < F16_BPW

        full_note = (
            f"body {ctx_size} tok + f16 exact tail "
            f"{self._exact_rows(kv_tail_tokens, n_parallel)} rows"
            if lossy and kv_tail_tokens > 0
            else f"exact f16 body {ctx_size} tok"
        )
        rows = [
            (
                "full-attn",
                self.full_attn_layers,
                self.full_attn_kv_heads,
                full_note,
                self._full_group_bytes(
                    ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
                ),
            )
        ]
        if self.sliding_window_layers:
            window = min(ctx_size, self.sliding_window_size)
            if kv_tail_tokens >= window or not lossy:
                rows_n = self._exact_rows(window, n_parallel)
                swa_note = f"bodyless exact f16 ({window}+{KV_TAIL_ROLLBACK})x{n_parallel} = {rows_n} rows"
            elif kv_tail_tokens > 0:
                swa_note = (
                    f"body {window} tok + f16 exact tail "
                    f"{self._exact_rows(kv_tail_tokens, n_parallel)} rows"
                )
            else:
                swa_note = f"quant body {window} tok"
            rows.append(
                (
                    "sliding-window",
                    self.sliding_window_layers,
                    self.sliding_window_kv_heads,
                    swa_note,
                    self._swa_group_bytes(
                        ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
                    ),
                )
            )
        return rows


# KV heads within a group are uniform, so full/sliding counts are just
# layers * kv_heads. Matched case-insensitively as the first key (longest first,
# so "Ternary-Bonsai-27B" wins over "Bonsai-27B") that is a substring of the
# model reference on the llama-perplexity command line (-hf / -m / --model).
_MODEL_KV: dict[str, ModelKV] = {
    "Qwen3.6-35B-A3B": ModelKV(
        full_attn_layers=41,
        full_attn_kv_heads=2,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=256,
        value_dim=256,
    ),
    "Qwen3.6-27B": ModelKV(
        full_attn_layers=64,
        full_attn_kv_heads=4,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=256,
        value_dim=256,
    ),
    "Qwen3.5-9B": ModelKV(
        full_attn_layers=33,
        full_attn_kv_heads=4,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=256,
        value_dim=256,
    ),
    "Gemma4-E2B": ModelKV(
        full_attn_layers=7,
        full_attn_kv_heads=1,
        sliding_window_layers=28,
        sliding_window_kv_heads=1,
        sliding_window_size=512,
        key_dim=512,
        value_dim=512,
    ),
    "Gemma4-E4B": ModelKV(
        full_attn_layers=7,
        full_attn_kv_heads=2,
        sliding_window_layers=35,
        sliding_window_kv_heads=2,
        sliding_window_size=512,
        key_dim=512,
        value_dim=512,
    ),
    "Gemma4-12B": ModelKV(
        full_attn_layers=8,
        full_attn_kv_heads=1,
        sliding_window_layers=40,
        sliding_window_kv_heads=8,
        sliding_window_size=1024,
        key_dim=512,
        value_dim=512,
    ),
    "Gemma4-26B-A4B": ModelKV(
        full_attn_layers=5,
        full_attn_kv_heads=2,
        sliding_window_layers=25,
        sliding_window_kv_heads=8,
        sliding_window_size=1024,
        key_dim=512,
        value_dim=512,
    ),
    "Gemma4-31B": ModelKV(
        full_attn_layers=10,
        full_attn_kv_heads=4,
        sliding_window_layers=50,
        sliding_window_kv_heads=16,
        sliding_window_size=1024,
        key_dim=512,
        value_dim=512,
    ),
    "LFM2.5-230M": ModelKV(
        full_attn_layers=6,
        full_attn_kv_heads=8,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=64,
        value_dim=64,
    ),
    "LFM2.5-8B-A1B": ModelKV(
        full_attn_layers=6,
        full_attn_kv_heads=8,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=64,
        value_dim=64,
    ),
    # Laguna's hybrid SWA (3:1, window 512) lives in the arch code, not the GGUF
    # metadata; per models.ini: 36 sliding-window + 12 full layers, 8 KV heads.
    "Laguna-S-2.1": ModelKV(
        full_attn_layers=12,
        full_attn_kv_heads=8,
        sliding_window_layers=36,
        sliding_window_kv_heads=8,
        sliding_window_size=512,
        key_dim=128,
        value_dim=128,
    ),
}
_MODEL_KV["Ornith-1.0-35B"] = _MODEL_KV["Qwen3.6-35B-A3B"]
_MODEL_KV["Ternary-Bonsai-27B"] = _MODEL_KV["Qwen3.6-27B"]
_MODEL_KV["Bonsai-27B"] = _MODEL_KV["Qwen3.6-27B"]


def _match_model(model_ref: str):
    """Return (key, spec) for the first _MODEL_KV name that is a case-insensitive
    substring of ``model_ref`` (longest key first), or None."""
    if not model_ref:
        return None
    ref = model_ref.lower()
    for key in sorted(_MODEL_KV, key=len, reverse=True):
        if key.lower() in ref:
            return key, _MODEL_KV[key]
    return None


def _context_calc(
    model_key: str,
    spec: ModelKV,
    ctk: str,
    ctv: str,
    tail: int,
    n_parallel: int,
    n_ctx=DEFAULT_CTX_SIZE,
):
    """Total KV-cache size (MiB) at ``n_ctx`` tokens for the (ctk, ctv, tail)
    combo and ``n_parallel`` parallel sequences, plus a human-readable
    derivation string for the table tooltip. The quant bpw already includes
    KVarN's per-tile scale overhead."""
    bpw_k, bpw_v = resolve_bpw(ctk), resolve_bpw(ctv)
    mib = (
        spec.get_total_kv_cache_size(n_ctx, bpw_k, bpw_v, tail, n_parallel)
        / BYTES_PER_MIB
    )
    lines = [
        f"{model_key}: {n_ctx} tok, tail {tail}, n_parallel {n_parallel}",
        f"K {ctk} {bpw_k:g} bpw, V {ctv} {bpw_v:g} bpw",
    ]
    for name, layers, kv_heads, note, group_bytes in spec.cache_breakdown(
        n_ctx, bpw_k, bpw_v, tail, n_parallel
    ):
        lines.append(
            f"{name}: {layers} layers x{kv_heads} kv-heads x"
            f"{spec.key_dim}/{spec.value_dim} dim; {note} "
            f"-> {group_bytes / BYTES_PER_MIB:,.0f} MiB"
        )
    lines.append(f"total {mib:,.0f} MiB")
    return mib, "\n".join(lines)


# ---------------------------------------------------------------------------
#  Log parser
# ---------------------------------------------------------------------------
CMD_RE = re.compile(r"llama-perplexity\s+.*?-ctk\s+(\S+)\s+-ctv\s+(\S+)")
# --kv-tail-tokens 0 is never emitted (mainline-llama.cpp compat); its absence
# means the effective default tail: 128 for KVarN caches, 0 otherwise.
KV_TAIL_RE = re.compile(r"--kv-tail-tokens\s+(\d+)")
CTX_SIZE_RE = re.compile(r"--ctx-size\s+(\d+)")
# Model reference on the command line: -hf <repo>:<quant>, -m/--model <path>.
MODEL_REF_RE = re.compile(r"(?:^|\s)(?:-hf|--hf-repo|--model|-m)\s+(\S+)")
# Baseline logits dump logs "perplexity: … seconds per pass"; KLD runs log
# "kl_divergence: … seconds per pass". Match either so the baseline speed is
# derived from the per-pass time, not the (remaining-time) ETA fallback.
SECONDS_PER_PASS_RE = re.compile(
    r"(?:kl_divergence|perplexity): (\d+\.?\d*) seconds per pass"
)
TOTAL_MINUTES_RE = re.compile(r"(\d+\.?\d*)\s+minutes$")
FULL_CMD_RE = re.compile(r"^(llama-perplexity\s+.*)$", re.MULTILINE)
# Summary statistics from the "====== KL divergence statistics ======" block
SUMMARY_HDR = re.compile(r"^=+\s+KL divergence statistics\s+=+")
SUMMARY_LINE = re.compile(r"^\s*(Mean|Median|([\d.]+)%)\s+KLD:\s+([\d.-]+)")


def _common_params(text: str) -> str:
    """Extract CLI parameters (excluding -ctk/-ctv --kv-tail-tokens
    --kl-divergence --kl-divergence-base)."""
    skip_keys = {
        "--kl-divergence-base",
        "--kl-divergence",
        "-ctk",
        "-ctv",
        "--kv-tail-tokens",
    }
    param_sets = []
    for m in FULL_CMD_RE.finditer(text):
        parts = m.group(1).split()
        # Drop executable name
        parts = parts[1:]
        # Ignore --version lines (they have no meaningful params)
        if parts and parts[0] == "--version":
            continue
        # Drop --kl-divergence-base <path>, -ctk <type>, -ctv <type>, --kl-divergence
        i = 0
        filtered = []
        while i < len(parts):
            if parts[i] in skip_keys:
                if parts[i] == "--kl-divergence":
                    i += 1
                    continue
                # Skip key and its value
                i += 2
                continue
            filtered.append(parts[i])
            i += 1
        param_sets.append(filtered)

    if not param_sets:
        return ""

    # Intersect all param sequences
    common = param_sets[0]
    for s in param_sets[1:]:
        common = [v for v in common if v in s]
        if not common:
            break
    # Preserve order from first command
    common_ordered = []
    seen = set()
    for v in param_sets[0]:
        if v in common and v not in seen:
            common_ordered.append(v)
            seen.add(v)
    return " ".join(common_ordered) if common_ordered else ""


def _make_label(ctk: str, ctv: str, tail: int, show_tail: bool) -> str:
    """Compact display label for a (ctk, ctv, kv-tail-tokens) combo.

    * A symmetric pair ``qk == qv`` collapses to a single ``qk`` -- always for
      KVarN (which is symmetric by construction), and for other quants only when
      the tail suffix is hidden.
    * The `` tN`` suffix is shown on every label iff --kv-tail-tokens appears
      explicitly anywhere in the log (``show_tail``); otherwise it is omitted
      entirely. ``tail`` is the effective value: the explicit one, or the
      default when omitted (128 for KVarN caches, 0 otherwise).

    Examples: no explicit tail -> ``q8_0``, ``q8_0/q4_0``, ``kvarn4``; explicit
    tail somewhere -> ``q8_0/q8_0 t0``, ``kvarn4 t128``, ``q8_0/q4_0 t1024``.
    """
    symmetric = ctk == ctv
    is_kvarn = symmetric and ctk.startswith("kvarn")
    if is_kvarn or (symmetric and not show_tail):
        quant = ctk
    else:
        quant = f"{ctk}/{ctv}"
    return f"{quant} t{tail}" if show_tail else quant


def parse_log(
    path: str, n_parallel: int = 4, projected_ctx: int = DEFAULT_CTX_SIZE
) -> tuple[list[dict], str]:
    text = Path(path).read_text()
    sections = re.split(r"^-{30,}", text, flags=re.MULTILINE)
    common_params = _common_params(text)

    runs = []
    for sec in sections:
        # A section (between normal separators) may contain multiple runs
        # separated by ABORTED markers. Split internally on those.
        chunks = re.split(r"^-{3} ABORTED .+ -{3}$", sec, flags=re.MULTILINE)

        for i, chunk in enumerate(chunks):
            lines = chunk.strip().split("\n")
            if not lines:
                continue

            # Find command line (may not be first line)
            cmd_line = None
            for ln in lines:
                m = CMD_RE.search(ln)
                if m:
                    cmd_line = ln
                    break
            if not m:
                continue

            is_aborted = i < len(chunks) - 1  # ABORTED marker follows this chunk

            ctk, ctv = m.group(1), m.group(2)
            tail_m = KV_TAIL_RE.search(cmd_line)
            if tail_m:
                tail = int(tail_m.group(1))
            else:
                # Omitting --kv-tail-tokens defaults to 128 for KVarN caches
                # (KVarN keeps a 128-token f16 tail by default), 0 otherwise.
                is_kvarn = ctk.startswith("kvarn") or ctv.startswith("kvarn")
                tail = 128 if is_kvarn else 0
            has_kld = bool(re.search(r"(?:^|\s)--kl-divergence(?:\s|$)", cmd_line))

            # Extract ctx-size
            ctx_match = CTX_SIZE_RE.search(cmd_line)
            ctx_size = int(ctx_match.group(1)) if ctx_match else 0

            # Speed calculation
            seconds_per_pass = None
            total_minutes = None
            for ln in lines:
                spp_m = SECONDS_PER_PASS_RE.search(ln)
                if spp_m:
                    seconds_per_pass = float(spp_m.group(1))

                min_m = TOTAL_MINUTES_RE.search(ln)
                if min_m:
                    total_minutes = float(min_m.group(1))

            # Robust n_chunks detection
            n_chunks_tmp = 0
            for ln in reversed(lines):
                m_std = re.match(r"^\s*(\d+)", ln)
                if m_std and "±" in ln:
                    n_chunks_tmp = int(m_std.group(1))
                    break
                m_brackets = re.findall(r"\[(\d+)\]", ln)
                if m_brackets:
                    n_chunks_tmp = int(m_brackets[-1])
                    break

            speed = None
            if seconds_per_pass is not None:
                speed = ctx_size / seconds_per_pass
            elif total_minutes is not None:
                if n_chunks_tmp > 0:
                    speed = (n_chunks_tmp * ctx_size) / (total_minutes * 60)

            if not has_kld:
                # Baseline run — KLD = 0 by definition
                size = resolve_bpw(ctk) + resolve_bpw(ctv)
                runs.append(
                    {
                        "ctk": ctk,
                        "ctv": ctv,
                        "tail": tail,
                        "label": f"{ctk}/{ctv}",
                        "size": size,
                        "n_chunks": 0,
                        "mean": 0.0,
                        "median": 0.0,
                        "p90": 0.0,
                        "p999": 0.0,
                        "speed": speed,
                        "baseline": True,
                    }
                )
                continue

            # Count chunks for display; parse summary for statistics
            # Find last chunk's index (not count — chunks 2-575 may be collapsed to [...])
            n_chunks = 0
            for ln in reversed(lines):
                m = re.match(r"^\s*(\d+)", ln)
                if m and "±" in ln:
                    n_chunks = int(m.group(1))
                    break
            stats = {"mean": 0.0, "median": 0.0, "p90": 0.0, "p999": 0.0}
            in_summary = False
            for ln in lines:
                if SUMMARY_HDR.match(ln):
                    in_summary = True
                    continue
                if not in_summary:
                    continue
                m = SUMMARY_LINE.match(ln)
                if not m:
                    continue
                key = m.group(1)
                pct = m.group(2)
                val = float(m.group(3))
                if pct is not None:
                    if pct == "99.9":
                        stats["p999"] = val
                    elif pct == "90.0":
                        stats["p90"] = val
                elif key == "Mean":
                    stats["mean"] = val
                elif key == "Median":
                    stats["median"] = val

            if not in_summary:
                # Aborted run — no KLD stats. Still list in table with blank metrics.
                if is_aborted:
                    runs.append(
                        {
                            "ctk": ctk,
                            "ctv": ctv,
                            "tail": tail,
                            "label": f"{ctk}/{ctv}",
                            "size": resolve_bpw(ctk) + resolve_bpw(ctv),
                            "n_chunks": 0,
                            "mean": None,
                            "median": None,
                            "p90": None,
                            "p999": None,
                            "speed": speed,
                            "aborted": True,
                        }
                    )
                    continue
                print(
                    f"WARNING: --kl-divergence flag found but no summary stats for {ctk}/{ctv}",
                    file=sys.stderr,
                )
                continue

            runs.append(
                {
                    "ctk": ctk,
                    "ctv": ctv,
                    "tail": tail,
                    "label": f"{ctk}/{ctv}",
                    "size": resolve_bpw(ctk) + resolve_bpw(ctv),
                    "n_chunks": n_chunks,
                    "mean": stats["mean"],
                    "median": stats["median"],
                    "p90": stats["p90"],
                    "p999": stats["p999"],
                    "speed": speed,
                }
            )

    # Labels depend on the whole log: the `` tN`` suffix appears on every run
    # iff --kv-tail-tokens is passed explicitly somewhere. Its absence is a
    # default (128 for KVarN, 0 otherwise), so keying off non-zero tails would
    # wrongly show suffixes for a KVarN-only log where the flag never appears.
    show_tail = bool(KV_TAIL_RE.search(text))
    for r in runs:
        r["label"] = _make_label(r["ctk"], r["ctv"], r["tail"], show_tail)

    # Total KV-cache size at the projected context (--ctx-size), if recognised.
    ref_m = MODEL_REF_RE.search(text)
    model_ref = ref_m.group(1) if ref_m else ""
    match = _match_model(model_ref)
    if match is None:
        print(
            f"WARNING: no KV-cache model matched '{model_ref or '(none)'}' -- "
            "Context (MiB) column left empty, plot unchanged",
            file=sys.stderr,
        )
    for r in runs:
        if match is not None:
            model_key, spec = match
            r["ctx_mib"], r["ctx_note"] = _context_calc(
                model_key,
                spec,
                r["ctk"],
                r["ctv"],
                r["tail"],
                n_parallel,
                projected_ctx,
            )
        else:
            r["ctx_mib"], r["ctx_note"] = None, None

    return runs, common_params


def _cost_axis(runs: list[dict], ctx_label: str = "256k") -> tuple[str, str, str]:
    """Pick the frontier / plot-x cost metric. When the model is recognised
    (``ctx_mib`` present on the runs) the Pareto frontier and the plot x-axis
    use the total KV-cache size at ``ctx_label`` -- so tail variants that share a
    bpw separate out -- and frontier points are drawn solid black. Otherwise both
    fall back to bpw. Returns (cost_key, x_axis_label, frontier_marker)."""
    if any(r.get("ctx_mib") is not None for r in runs):
        return "ctx_mib", f"Context size (MiB) @ {ctx_label}", "⚫"  # black circle
    return "size", "Size (bits / weight)", "\U0001f7e2"  # green circle


# ---------------------------------------------------------------------------
#  HTML / Chart.js report (self-contained)
# ---------------------------------------------------------------------------
def _esc(s):
    return html_mod.escape(str(s))


def _fmt(v):
    """Format KLD value to 6 decimal places, or blank if None (aborted run)."""
    if v is None:
        return ""
    return f"{v:.6f}"


def _fmt_size(v):
    return f"{v:.2f}"


def generate_html(
    runs: list[dict],
    common_params: str = "",
    chart_js_src: str = "",
    speed_cutoff_factor: float = 0.33,
    n_parallel: int = 4,
    ctx_label: str = "256k",
) -> str:
    # Cost metric for the frontier and x-axis: context MiB @<ctx_label> if the
    # model is recognised, else bpw.
    cost_key, x_axis_label, _ = _cost_axis(runs, ctx_label)

    # Baseline is the KLD-0 reference: it stays in the table but is never
    # plotted (its zero KLD would sit off the bottom and drag the frontier
    # line into the corner).
    sorted_runs = sorted(
        (r for r in runs if not r.get("baseline")), key=lambda r: r[cost_key]
    )

    # ---- Pareto frontiers (separate per stat; group by cost so equal-cost runs compete) ----
    def _frontier(key: str, candidate_runs: list[dict]) -> set[int]:
        from collections import defaultdict

        by_cost: dict[float, list[dict]] = defaultdict(list)
        for r in candidate_runs:
            by_cost[r[cost_key]].append(r)
        ids = set()
        best = float("inf")
        for s in sorted(by_cost):
            min_kld = min(r[key] for r in by_cost[s])
            if min_kld < best:
                for r in by_cost[s]:
                    if r[key] == min_kld:
                        ids.add(id(r))
                best = min_kld
        return ids

    baseline_run = next((r for r in runs if r.get("baseline")), None)
    speed_cutoff = (
        baseline_run["speed"] * speed_cutoff_factor
        if baseline_run and baseline_run["speed"] is not None
        else None
    )
    eligible_runs = [
        r
        for r in runs
        if not r.get("aborted")
        and not r.get("baseline")
        and (speed_cutoff is None or r["speed"] is None or r["speed"] >= speed_cutoff)
    ]

    frontier_mean = _frontier("mean", eligible_runs)
    frontier_p999 = _frontier("p999", eligible_runs)

    # A run is suboptimal if it's NOT on either frontier, OR if it's too slow.
    # The baseline is excluded: it is not a frontier candidate but must not be
    # greyed out in the table either.
    suboptimal_ids = {
        id(r)
        for r in runs
        if not r.get("baseline")
        and id(r) not in frontier_mean
        and id(r) not in frontier_p999
    }
    if speed_cutoff is not None:
        for r in runs:
            if r["speed"] is not None and r["speed"] < speed_cutoff:
                suboptimal_ids.add(id(r))

    # ---- common params block ----
    common_html = ""
    if common_params:
        common_html = (
            '<div class="common-params">'
            "<h2>Common Parameters</h2>"
            f"<code>llama-perplexity {_esc(common_params)}</code>"
            "</div>\n"
        )

    # ---- table sorted by size descending ----
    tbl_rows = ""
    for r in sorted(runs, key=lambda r: r[cost_key], reverse=True):
        label = r["label"]
        if r.get("baseline"):
            label += " (baseline)"
        elif r.get("aborted"):
            label += " (aborted)"
        cls = ' class="suboptimal"' if id(r) in suboptimal_ids else ""
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}" if r["speed_pct"] is not None else "N/A"
        if r.get("ctx_mib") is not None:
            ctx_cell = (
                f'<td class="ctx" title="{_esc(r["ctx_note"])}">'
                f"{r['ctx_mib']:,.0f}</td>"
            )
        else:
            ctx_cell = "<td></td>"
        tbl_rows += (
            f"<tr{cls}>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_fmt_size(r['size'])}</td>"
            f"{ctx_cell}"
            f"<td>{_fmt(r['mean'])}</td>"
            f"<td>{_fmt(r['median'])}</td>"
            f"<td>{_fmt(r['p90'])}</td>"
            f"<td>{_fmt(r['p999'])}</td>"
            f"<td>{speed_fmt}</td>"
            f"<td>{pct_fmt}</td>"
            f"</tr>\n"
        )

    # ---- chart datasets ----
    # Split each stat into frontier (connected) + non-frontier (floating)
    # Each stat uses its own frontier
    stat_specs = [
        ("Mean KLD", "mean", "#e74c3c", frontier_mean),
        ("99.9% KLD", "p999", "#9b59b6", frontier_p999),
    ]

    ds_parts = []
    for label, key, color, frontier in stat_specs:
        best_pts = []
        sub_pts = []
        for r in sorted_runs:
            if r.get("aborted"):
                continue
            y = r[key] if r[key] > 0 else 1e-10
            pt = {
                "x": r[cost_key],
                "y": y,
                "_label": r["label"],
                "_suboptimal": id(r) in suboptimal_ids,
                "_speed": r["speed"],
                "_speed_pct": r["speed_pct"],
            }
            # A point is on the "best line" only if it's in the calculated frontier
            # AND it's not too slow.
            if id(r) in frontier and id(r) not in suboptimal_ids:
                best_pts.append(pt)
            else:
                sub_pts.append(pt)

        # Best line
        ds_parts.append(
            "{ label: '%s', data: %s,"
            " borderColor: '%s', backgroundColor: '%s',"
            " showLine: true, fill: false, tension: 0,"
            " pointRadius: 6, pointHoverRadius: 8 }"
            % (label, json.dumps(best_pts), color, color)
        )
        # Suboptimal floating points (hidden from legend via filter)
        ds_parts.append(
            "{ label: '%s', data: %s,"
            " borderColor: '%s', backgroundColor: '%s',"
            " showLine: false, fill: false,"
            " pointRadius: 4, pointHoverRadius: 6,"
            " pointBackgroundColor: '%s44', _sub: true }"
            % (label, json.dumps(sub_pts), color, color, color)
        )
    # ---- extra lines: k=f16 (dashed) and v=f16 (dotted) ----
    for subset_name, dash_pat, subset_runs in [
        (
            "k=f16",
            [6, 3],
            [r for r in sorted_runs if r["ctk"] == "f16" and not r.get("aborted")],
        ),
        (
            "v=f16",
            [2, 3],
            [r for r in sorted_runs if r["ctv"] == "f16" and not r.get("aborted")],
        ),
    ]:
        if not subset_runs:
            continue
        for stat_label, key, base_color, _ in stat_specs:
            r_, g_, b_ = (
                int(base_color[1:3], 16),
                int(base_color[3:5], 16),
                int(base_color[5:7], 16),
            )
            faint = f"rgba({r_}, {g_}, {b_}, 0.35)"
            pts = sorted(
                [
                    {
                        "x": r[cost_key],
                        "y": r[key] if r[key] > 0 else 1e-10,
                        "_label": r["label"],
                        "_suboptimal": id(r) in suboptimal_ids,
                    }
                    for r in subset_runs
                ],
                key=lambda p: p["x"],
            )
            ds_parts.append(
                "{ label: '%s %s', data: %s,"
                " borderColor: '%s', backgroundColor: '%s',"
                " showLine: true, fill: false, tension: 0,"
                " pointRadius: 4, pointHoverRadius: 6,"
                " borderDash: %s }"
                % (
                    subset_name,
                    stat_label,
                    json.dumps(pts),
                    faint,
                    faint,
                    json.dumps(dash_pat),
                )
            )

    datasets_js = ",\n      ".join(ds_parts)

    # Compute y-axis range so smallest non-zero point sits at 1/3 from bottom
    # (baseline at y=1e-10 shoots out of plot)
    max_y = max(
        r[key]
        for key in ["mean", "p999"]
        for r in sorted_runs
        if r[key] is not None and r[key] > 0
    )
    min_nonzero = min(
        r[key]
        for key in ["mean", "p999"]
        for r in sorted_runs
        if r[key] is not None and r[key] > 0
    )
    import math

    # Position min_nonzero at 1/10 from bottom of log scale (90% down):
    # (log(min_nonzero) - log(y_min)) / (log(max_y) - log(y_min)) = 0.1
    log_max = math.log10(max_y)
    log_min = math.log10(min_nonzero)
    log_y_min = (10 * log_min - log_max) / 9
    y_min = 10**log_y_min
    y_max = max_y * 1.15  # 15% headroom above max
    x_max = max(r[cost_key] for r in sorted_runs) * 1.05  # 5% headroom past widest

    baseline_label = next((r["label"] for r in sorted_runs if r.get("baseline")), None)
    baseline_label_json = json.dumps(baseline_label) if baseline_label else "null"

    # Chunk count — same for all non-baseline runs
    n_chunks = next(
        (r["n_chunks"] for r in sorted_runs if r.get("n_chunks", 0) > 0), None
    )
    chunks_html = (
        (f'<div class="common-params"><h2>Chunks per run</h2>{n_chunks}</div>\n')
        if n_chunks
        else ""
    )

    html = HTML_HEAD.replace("{chart_js_src}", chart_js_src).replace(
        "{ctx_label}", _esc(ctx_label)
    )
    html += common_html
    html += chunks_html
    html += tbl_rows
    html += HTML_MID % (
        y_min,
        y_max,
        x_max,
        baseline_label_json,
        datasets_js,
        x_axis_label,
    )
    if speed_cutoff is not None:
        pct = speed_cutoff_factor * 100
        html += f'<p class="note">Runs with speed &lt; {pct:.0f}% of baseline excluded from frontier determination.</p>\n'
    if any(r.get("ctx_mib") is not None for r in runs):
        html += (
            f'<p class="note">Context (MiB) @{_esc(ctx_label)} is the estimated '
            f"beellama v0.4.1 llama-server KV-cache VRAM at a {_esc(ctx_label)} "
            f"context with n_parallel={n_parallel} (source-modelled, not yet "
            "log-validated); hover a cell "
            "for the per-layer-group breakdown.</p>\n"
        )
    html += HTML_TAIL
    return html


HTML_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KLD Effect of Context Quantization</title>
{chart_js_src}
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f8f9fa; color: #222; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 0.9rem; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 30px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 6px; overflow: hidden; }
  th, td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #eee; }
  th { background: #f0f0f0; font-weight: 600; white-space: nowrap; }
  td:first-child, th:first-child { text-align: left; }
  tr:hover { background: #f5f5f5; }
  .chart-container { background: #fff; padding: 20px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 30px; }
  .common-params { background: #fff; padding: 12px 16px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }
  .common-params h2 { font-size: 1rem; margin: 0 0 6px 0; }
  .common-params code { font-size: 0.82rem; word-break: break-all; color: #333; }
  tr.suboptimal { color: #888; }
  tr.suboptimal td { color: inherit; }
  td.ctx { cursor: help; text-decoration: underline dotted; text-underline-offset: 2px; }
  .label-toggle { display: block; margin-bottom: 8px; font-size: 0.9rem; cursor: pointer; user-select: none; }
  .label-toggle input { margin-right: 6px; }
  .note { color: #888; font-size: 0.8rem; margin-top: 10px; }
</style>
</head>
<body>
<h1>KLD Effect of Context Quantization</h1>
<p class="subtitle">Generated by kld-report.py</p>
<table>
<thead>
<tr>
  <th>ctk / ctv</th>
  <th>Size (bpw)</th>
  <th>Context (MiB) @{ctx_label}</th>
  <th>Mean KLD</th>
  <th>Median KLD</th>
  <th>90.0% KLD</th>
  <th>99.9% KLD</th>
  <th>Speed (tok/s)</th>
  <th>Speed (%)</th>
</tr>
</thead>
<tbody>
"""

HTML_MID = """\
</tbody>
</table>
<div class="chart-container">
  <label class="label-toggle">
    <input type="checkbox" id="hideNonFrontierLabels" checked>
    Hide labels of non-frontier points
  </label>
  <canvas id="kldChart" width="1000" height="600"></canvas>
</div>
<p class="note">Y-axis: log scale.  Size = bpw(ctk) + bpw(ctv).  Each point labelled with its ctk/ctv pair.  Scroll wheel to zoom, drag to pan, double-click to reset.</p>
<script>
var Y_MIN  = %s;
var Y_MAX  = %s;
var X_MAX  = %s;
var BASELINE_LABEL = %s;

var ctx = document.getElementById('kldChart').getContext('2d');
var chart = new Chart(ctx, {
  type: 'scatter',
  data: {
    datasets: [%s]
  },
  options: {
    responsive: true,
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          filter: function(item, data) {
            return !data.datasets[item.datasetIndex]._sub;
          }
        }
      },
      tooltip: {
        callbacks: {
          label: function(ctx) {
            var lbl = ctx.raw._label || '';
            var speed = ctx.raw._speed;
            var pct = ctx.raw._speed_pct;
            var speedStr = '';
            if (speed !== null && speed !== undefined) {
              speedStr = '  ' + speed.toFixed(1) + ' tok/s';
              if (pct !== null && pct !== undefined) {
                speedStr += ' (' + pct.toFixed(1) + '%%)';
              }
            }
            return lbl + '  ' + ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(6) + speedStr;
          }
        }
      },
    },
    scales: {
      x: {
        title: { display: true, text: '%s' },
        type: 'linear',
        min: 0,
        max: X_MAX
      },
      y: {
        title: { display: true, text: 'KL Divergence' },
        type: 'logarithmic',
        min: Y_MIN,
        max: Y_MAX
      }
    },
    elements: {
      point: {
        radius: 6,
        hoverRadius: 8
      }
    }
  },
  plugins: [{
    afterDraw: function(chart) {
      var ctx = chart.ctx;
      var xScale = chart.scales.x;
      var yScale = chart.scales.y;
      var hideNonFrontier = document.getElementById('hideNonFrontierLabels') &&
                            document.getElementById('hideNonFrontierLabels').checked;
      ctx.save();
      ctx.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = '#333';
      // Label every data point at its own position
      for (var d = 0; d < chart.data.datasets.length; d++) {
        var pts = chart.data.datasets[d].data;
        for (var p = 0; p < pts.length; p++) {
          var pt = pts[p];
          if (hideNonFrontier && pt._suboptimal) continue;
          var xPos = xScale.getPixelForValue(pt.x);
          if (xPos < 0 || xPos > chart.width) continue;
          var y = yScale.getPixelForValue(pt.y) - 10;
          ctx.fillText(pt._label, xPos, y);
        }
      }
      ctx.restore();
      // Baseline annotation — bottom-right corner
      if (BASELINE_LABEL) {
        ctx.save();
        ctx.font = '12px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillStyle = '#888';
        var ax = chart.width - 16;
        var ay = chart.height - 40;
        ctx.fillText(BASELINE_LABEL + ' (baseline) ' + String.fromCharCode(8595), ax, ay);
        ctx.restore();
      }
    }
  }]
});

// ---- Zoom / Pan (native, no external plugins) ----
var origXMin = 0, origXMax = X_MAX, origYMin = Y_MIN, origYMax = Y_MAX;

(function() {
  var canvas = document.getElementById('kldChart');

  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var mouseX = e.clientX - rect.left;
    var mouseY = e.clientY - rect.top;
    var xScale = chart.scales.x;
    var yScale = chart.scales.y;
    if (!xScale || !yScale) return;
    var factor = e.deltaY > 0 ? 1.15 : 0.85;
    // X zoom (linear)
    var xRange = (xScale.max - xScale.min) * factor;
    var xCenter = xScale.getValueForPixel(mouseX);
    chart.options.scales.x.min = xCenter - xRange / 2;
    chart.options.scales.x.max = xCenter + xRange / 2;
    // Y zoom (log scale — operate in log space)
    var logMin = Math.log10(yScale.min);
    var logMax = Math.log10(yScale.max);
    var logRange = (logMax - logMin) * factor;
    var logCenter = Math.log10(yScale.getValueForPixel(mouseY));
    chart.options.scales.y.min = Math.pow(10, logCenter - logRange / 2);
    chart.options.scales.y.max = Math.pow(10, logCenter + logRange / 2);
    chart.update();
  }, { passive: false });

  // Drag to pan
  var isPanning = false;
  var panStartX, panStartY, panXMin, panXMax, panYMin, panYMax;

  canvas.addEventListener('mousedown', function(e) {
    isPanning = true;
    panStartX = e.clientX;
    panStartY = e.clientY;
    panXMin = chart.options.scales.x.min;
    panXMax = chart.options.scales.x.max;
    panYMin = chart.options.scales.y.min;
    panYMax = chart.options.scales.y.max;
    canvas.style.cursor = 'grabbing';
  });

  document.addEventListener('mousemove', function(e) {
    if (!isPanning) return;
    var xScale = chart.scales.x;
    var yScale = chart.scales.y;
    if (!xScale || !yScale) return;
    var dx = e.clientX - panStartX;
    var dy = e.clientY - panStartY;
    // X pan
    var xPixRange = xScale.right - xScale.left;
    if (xPixRange > 0) {
      var xDataRange = panXMax - panXMin;
      chart.options.scales.x.min = panXMin - (dx / xPixRange) * xDataRange;
      chart.options.scales.x.max = panXMax - (dx / xPixRange) * xDataRange;
    }
    // Y pan (log)
    var yPixRange = yScale.bottom - yScale.top;
    if (yPixRange > 0) {
      var yLogMin = Math.log10(panYMin);
      var yLogMax = Math.log10(panYMax);
      var yLogRange = yLogMax - yLogMin;
      var yNewLogMin = yLogMin + (dy / yPixRange) * yLogRange;
      var yNewLogMax = yLogMax + (dy / yPixRange) * yLogRange;
      chart.options.scales.y.min = Math.pow(10, yNewLogMin);
      chart.options.scales.y.max = Math.pow(10, yNewLogMax);
    }
    chart.update();
  });

  document.addEventListener('mouseup', function() {
    if (isPanning) {
      isPanning = false;
      canvas.style.cursor = '';
    }
  });

  // Double-click to reset
  canvas.addEventListener('dblclick', function() {
    chart.options.scales.x.min = origXMin;
    chart.options.scales.x.max = origXMax;
    chart.options.scales.y.min = origYMin;
    chart.options.scales.y.max = origYMax;
    chart.update();
  });
})();

document.getElementById('hideNonFrontierLabels').addEventListener('change', function() {
  chart.draw();
});
</script>
"""

HTML_TAIL = """\
</body>
</html>
"""


# ---------------------------------------------------------------------------
#  SVG plot (matplotlib)
# ---------------------------------------------------------------------------
def _frontier_groups(
    runs: list[dict],
    key: str,
    speed_cutoff_factor: float = 0.33,
    exclude_baseline: bool = False,
    cost_key: str = "size",
) -> tuple[set[int], set[int]]:
    """Return (frontier_ids, suboptimal_ids) for a given stat key.

    ``exclude_baseline`` drops the baseline from the frontier candidates (used
    for the plot, which never shows the baseline) while still deriving the
    speed cutoff from it. ``cost_key`` is the run field ranked along the x-axis
    (``size`` bpw, or ``ctx_mib`` when the model is recognised).
    """
    from collections import defaultdict

    # Only consider runs that are not too slow
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    speed_cutoff = None
    if baseline_run and baseline_run["speed"] is not None:
        speed_cutoff = baseline_run["speed"] * speed_cutoff_factor

    eligible_runs = [r for r in runs if not r.get("aborted")]
    if exclude_baseline:
        eligible_runs = [r for r in eligible_runs if not r.get("baseline")]
    if speed_cutoff is not None:
        eligible_runs = [
            r for r in eligible_runs if r["speed"] is None or r["speed"] >= speed_cutoff
        ]

    by_cost: dict[float, list[dict]] = defaultdict(list)
    for r in eligible_runs:
        by_cost[r[cost_key]].append(r)
    frontier = set()
    best = float("inf")
    for s in sorted(by_cost):
        min_kld = min(r[key] for r in by_cost[s])
        if min_kld < best:
            for r in by_cost[s]:
                if r[key] == min_kld:
                    frontier.add(id(r))
            best = min_kld
    return frontier, {id(r) for r in runs if id(r) not in frontier}


def generate_plot_svg(
    runs: list[dict],
    width=1000,
    height=600,
    dpi=100,
    speed_cutoff_factor: float = 0.33,
    ctx_label: str = "256k",
) -> str:
    """Generate an SVG plot of KLD vs size using matplotlib."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cost_key, x_axis_label, _ = _cost_axis(runs, ctx_label)

    sorted_runs = sorted(runs, key=lambda r: r[cost_key])
    sorted_runs = [r for r in sorted_runs if not r.get("aborted")]

    # Frontiers exclude the baseline as a candidate but still need it in the
    # input to derive the speed cutoff.
    frontier_mean, _ = _frontier_groups(
        sorted_runs, "mean", exclude_baseline=True, cost_key=cost_key
    )
    frontier_p999, _ = _frontier_groups(
        sorted_runs, "p999", exclude_baseline=True, cost_key=cost_key
    )

    # Baseline is the KLD-0 reference: shown in the table but never plotted.
    sorted_runs = [r for r in sorted_runs if not r.get("baseline")]
    suboptimal_ids = {
        id(r)
        for r in sorted_runs
        if id(r) not in frontier_mean and id(r) not in frontier_p999
    }

    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax.set_xlabel(x_axis_label, fontsize=11)
    ax.set_ylabel("KL Divergence", fontsize=11)
    ax.set_yscale("log")
    ax.set_xlim(0, max(r[cost_key] for r in sorted_runs) * 1.05)

    # Y range: same logic as chart.js
    max_y = max(r[key] for key in ["mean", "p999"] for r in sorted_runs if r[key] > 0)
    min_nonzero = min(
        r[key] for key in ["mean", "p999"] for r in sorted_runs if r[key] > 0
    )
    import math

    log_max = math.log10(max_y)
    log_min = math.log10(min_nonzero)
    log_y_min = (10 * log_min - log_max) / 9
    y_min = 10**log_y_min
    ax.set_ylim(y_min, max_y * 1.5)

    ax.tick_params(labelsize=9)
    ax.grid(True, which="both", ls=":", alpha=0.3)

    stat_specs = [
        ("Mean KLD", "mean", "#e74c3c", frontier_mean),
        ("99.9% KLD", "p999", "#9b59b6", frontier_p999),
    ]

    for label, key, color, frontier in stat_specs:
        best_pts = [
            (r[cost_key], r[key] if r[key] > 0 else 1e-10, r)
            for r in sorted_runs
            if id(r) in frontier and id(r) not in suboptimal_ids
        ]
        sub_pts = [
            (r[cost_key], r[key] if r[key] > 0 else 1e-10, r)
            for r in sorted_runs
            if id(r) not in frontier or id(r) in suboptimal_ids
        ]

        # Sort by size for connected line
        best_pts.sort(key=lambda p: p[0])

        if best_pts:
            xs = [p[0] for p in best_pts]
            ys = [p[1] for p in best_pts]
            ax.plot(
                xs,
                ys,
                color=color,
                linewidth=1.5,
                marker="o",
                markersize=6,
                label=label,
                zorder=5,
            )

        if sub_pts:
            xs = [p[0] for p in sub_pts]
            ys = [p[1] for p in sub_pts]
            ax.scatter(xs, ys, color=color, alpha=0.25, s=20, zorder=3)

        # Labels
        for pt_list, alpha in [(best_pts, 1.0), (sub_pts, 0.4)]:
            for x, y, r in pt_list:
                if id(r) in suboptimal_ids and alpha < 1:
                    ax.annotate(
                        r["label"],
                        (x, y),
                        textcoords="offset points",
                        xytext=(0, -12),
                        fontsize=6,
                        ha="center",
                        va="top",
                        alpha=alpha,
                        zorder=10,
                    )
                else:
                    ax.annotate(
                        r["label"],
                        (x, y),
                        textcoords="offset points",
                        xytext=(0, -12),
                        fontsize=6.5,
                        ha="center",
                        va="top",
                        alpha=alpha,
                        zorder=10,
                    )

    # Extra lines: k=f16 (dashed), v=f16 (dotted)
    for subset_name, dash_style, subset_runs_list in [
        ("k=f16", (0, (6, 3)), [r for r in sorted_runs if r["ctk"] == "f16"]),
        ("v=f16", (0, (2, 3)), [r for r in sorted_runs if r["ctv"] == "f16"]),
    ]:
        if not subset_runs_list:
            continue
        for stat_label, key, base_color, _ in stat_specs:
            r_, g_, b_ = (
                int(base_color[1:3], 16),
                int(base_color[3:5], 16),
                int(base_color[5:7], 16),
            )
            pts = sorted(
                [
                    (r[cost_key], r[key] if r[key] > 0 else 1e-10, r)
                    for r in subset_runs_list
                ],
                key=lambda p: p[0],
            )
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(
                xs,
                ys,
                color=(r_ / 255, g_ / 255, b_ / 255, 0.35),
                linewidth=1,
                linestyle=dash_style,
                marker="o",
                markersize=4,
                label=f"{subset_name} {stat_label}",
                zorder=4,
            )

    # Baseline annotation
    baseline_label = next((r["label"] for r in sorted_runs if r.get("baseline")), None)
    if baseline_label:
        ax.annotate(
            f"{baseline_label} (baseline) \u2193",
            xy=(0.98, 0.04),
            xycoords="axes fraction",
            fontsize=10,
            ha="right",
            va="bottom",
            color="#888",
            zorder=20,
        )

    ax.legend(fontsize=8, loc="upper left", framealpha=0.9)
    fig.tight_layout()

    from io import StringIO

    buf = StringIO()
    fig.savefig(buf, format="svg", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Markdown report
# ---------------------------------------------------------------------------
def generate_markdown(
    runs: list[dict],
    common_params: str = "",
    html_path: str | None = None,
    plot_path: str | None = None,
    repo: str | None = None,
    branch: str = "main",
    speed_cutoff_factor: float = 0.33,
    n_parallel: int = 4,
    ctx_label: str = "256k",
) -> str:
    """Generate a Markdown report with table and SVG plot (image ref + xref)."""
    cost_key, _, frontier_marker = _cost_axis(runs, ctx_label)
    sorted_runs = sorted(runs, key=lambda r: r["size"], reverse=True)

    frontier_mean, _ = _frontier_groups(
        sorted_runs, "mean", speed_cutoff_factor, cost_key=cost_key
    )
    frontier_p999, _ = _frontier_groups(
        sorted_runs, "p999", speed_cutoff_factor, cost_key=cost_key
    )
    suboptimal_ids = {
        id(r) for r in runs if id(r) not in frontier_mean and id(r) not in frontier_p999
    }

    lines = []
    lines.append("# KLD Effect of Context Quantization")
    lines.append("")
    lines.append("Generated by kld-report.py")
    lines.append("")

    if common_params:
        lines.append("## Common Parameters")
        lines.append("")
        lines.append(f"`llama-perplexity {common_params}`")
        lines.append("")

    n_chunks = next(
        (r["n_chunks"] for r in sorted_runs if r.get("n_chunks", 0) > 0), None
    )
    if n_chunks:
        lines.append(f"**Chunks per run:** {n_chunks}")
        lines.append("")

    lines.append(
        f"| ctk / ctv | Size (bpw) | Context (MiB) @{ctx_label} | Mean KLD | Median KLD |"
        " 90.0% KLD | 99.9% KLD | Speed (tok/s) | Speed (%) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for r in sorted(runs, key=lambda r: r[cost_key], reverse=True):
        label = r["label"]
        if r.get("baseline"):
            label += " (baseline)"
        elif r.get("aborted"):
            label += " (aborted)"
        frontier_mark = f" {frontier_marker}" if id(r) not in suboptimal_ids else ""
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}" if r["speed_pct"] is not None else "N/A"
        ctx_fmt = f"{r['ctx_mib']:,.0f}" if r.get("ctx_mib") is not None else ""
        lines.append(
            f"| {label}{frontier_mark} | {_fmt_size(r['size'])} | {ctx_fmt} |"
            f" {_fmt(r['mean'])} | {_fmt(r['median'])} | {_fmt(r['p90'])} |"
            f" {_fmt(r['p999'])} | {speed_fmt} | {pct_fmt} |"
        )

    lines.append("")

    # SVG image + xref links
    if repo and plot_path:
        owner, repo_name = repo.split("/", 1)
        # plot_path is a forward-slash path relative to the git repo root
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}"
        # htmlpreview proxies raw github; passes through with correct mime
        html_preview = f"https://htmlpreview.github.io/?{raw_base}/{html_path}"
        lines.append(f"![KLD Plot]({raw_base}/{plot_path})")
        lines.append("")
        lines.append(f"[Interactive report]({html_preview})")
        lines.append("")
    elif plot_path:
        lines.append(f"![KLD Plot]({plot_path})")
        lines.append("")
        xref_parts = []
        if html_path:
            xref_parts.append(f"[Interactive report]({html_path})")
        if xref_parts:
            lines.append(" | ".join(xref_parts))
            lines.append("")

    # Note about speed cutoff
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    if baseline_run and baseline_run["speed"] is not None:
        pct = speed_cutoff_factor * 100
        lines.append(
            f"> Runs with speed < {pct:.0f}% of baseline excluded from frontier determination."
        )
        lines.append("")

    if any(r.get("ctx_mib") is not None for r in runs):
        lines.append(
            f"> Context (MiB) @{ctx_label} is the estimated beellama v0.4.1 "
            f"llama-server KV-cache VRAM at a {ctx_label} context with "
            f"n_parallel={n_parallel} (source-modelled, not yet log-validated)."
        )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Generate KLD report from kv-perplexity output"
    )
    ap.add_argument(
        "log",
        nargs="?",
        default="kv-perplexity.log",
        help="Input log (default: kv-perplexity.log)",
    )
    ap.add_argument(
        "-o",
        "--output",
        default="kv-kld-report",
        help="Output basename (no extension). Generates BASENAME.html, BASENAME.md, BASENAME.svg (default: kv-kld-report)",
    )
    ap.add_argument(
        "--whitelist",
        nargs="+",
        metavar="CTK/CTV",
        help="Only include these combos, matched against the display label "
        "(e.g. q4_0, q8_0/q4_0, kvarn4 t1024). "
        "The baseline is always included even if not listed.",
    )
    ap.add_argument(
        "--repo",
        metavar="owner/repo",
        help="GitHub repository (e.g. crusaderky/pixi-llm-recipes). "
        "Auto-detected from git remote if omitted.  Generates "
        "raw.githubusercontent.com URLs for SVG and "
        "htmlpreview.github.io link for HTML report.",
    )
    ap.add_argument(
        "--branch",
        default=None,
        help="GitHub branch (default: auto-detect from git, fallback main; "
        "used only with --repo or auto-detected repo)",
    )
    ap.add_argument(
        "--speed-cutoff",
        type=float,
        default=0.33,
        help="Fraction of baseline speed; runs slower than this are excluded "
        "from frontier determination (default: 0.33)",
    )
    ap.add_argument(
        "--n-parallel",
        type=int,
        default=4,
        help="Parallel sequences (llama-server --parallel / n_seq_max) assumed "
        "when sizing the Context (MiB) column; it scales the KVarN f16 "
        "exact-tail overlay. Default 4 (llama-server auto for these models).",
    )
    ap.add_argument(
        "--ctx-size",
        type=parse_ctx_size,
        default=DEFAULT_CTX_SIZE,
        metavar="N[k|M]",
        help="Projected context size for the Context (MiB) column, independent "
        "of the run's own --ctx-size; accepts k/M suffixes (default: 256k).",
    )
    args = ap.parse_args()
    ctx_label = _fmt_ctx_label(args.ctx_size)

    # Auto-detect GitHub repo from git remote
    repo = args.repo
    branch = args.branch or "main"
    repo_root: Path | None = None
    if not repo:
        try:
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if remote.returncode == 0:
                m = re.match(
                    r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/.]+?)(?:\.git)?$",
                    remote.stdout.strip(),
                )
                if m:
                    repo = m.group(1)
                    # Auto-detect branch too
                    br = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if br.returncode == 0 and br.stdout.strip() != "HEAD":
                        branch = br.stdout.strip()
        except Exception:
            pass

    # Find git repo root to compute repo-relative paths for embedded URLs
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if toplevel.returncode == 0:
            repo_root = Path(toplevel.stdout.strip())
    except Exception:
        repo_root = None

    if repo:
        print(f"GitHub repo detected: {repo} (branch: {branch})")

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"ERROR: {log_path} not found", file=sys.stderr)
        sys.exit(1)

    runs, common_params = parse_log(args.log, args.n_parallel, args.ctx_size)

    # Normalize speed to baseline (baseline = 100%)
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    b_speed = (
        baseline_run["speed"]
        if baseline_run and baseline_run["speed"] is not None
        else None
    )
    for r in runs:
        if b_speed is not None and r["speed"] is not None:
            r["speed_pct"] = (r["speed"] / b_speed) * 100.0
        else:
            r["speed_pct"] = None

    runs_unfiltered = runs
    if args.whitelist:
        whitelisted = set(args.whitelist)
        runs = [r for r in runs if r["label"] in whitelisted or r.get("baseline")]
    if not runs:
        print(
            "No KLD runs found" + (" (none match whitelist)" if args.whitelist else ""),
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Parsed {len(runs)} KLD runs:"
        + (
            " (filtered by whitelist)"
            if args.whitelist and len(runs) != len(runs_unfiltered)
            else ""
        )
    )
    for r in runs:
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}%" if r["speed_pct"] is not None else "N/A"
        mean_fmt = f"{r['mean']:.6f}" if r["mean"] is not None else "       -"
        median_fmt = f"{r['median']:.6f}" if r["median"] is not None else "       -"
        p90_fmt = f"{r['p90']:.6f}" if r["p90"] is not None else "       -"
        p999_fmt = f"{r['p999']:.6f}" if r["p999"] is not None else "       -"
        aborted_tag = " (aborted)" if r.get("aborted") else ""
        ctx_fmt = f"{r['ctx_mib']:>8,.0f} MiB" if r.get("ctx_mib") is not None else ""
        print(
            f"  {r['label']:25s}{aborted_tag}  size={r['size']:6.2f} bpw  "
            f"ctx@{ctx_label}={ctx_fmt:>12s}  "
            f"mean={mean_fmt}  median={median_fmt}  "
            f"p90={p90_fmt}  p999={p999_fmt}  "
            f"speed={speed_fmt:>7s}  {pct_fmt:>7s}  ({r['n_chunks']} chunks)"
        )

    # Append extensions to -o value as-is (don't strip any existing extension)
    prefix = str(args.output)
    html_path = Path(prefix + ".html")
    md_path = Path(prefix + ".md")
    plot_path = Path(prefix + ".svg")

    # Compute repo-relative POSIX paths for embedded URLs (so raw.githubusercontent.com
    # URLs include any subdirectory the .md lives in, e.g. "perplexity/").
    def _repo_rel(p: Path) -> str:
        abs_p = p.resolve()
        if repo_root is not None:
            try:
                return abs_p.relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                pass
        return abs_p.name

    html_repo_rel = _repo_rel(html_path)
    plot_repo_rel = _repo_rel(plot_path)

    # Generate HTML
    chart_js_src = _fetch_chart_js()
    speed_cutoff_factor = args.speed_cutoff
    html = generate_html(
        runs,
        common_params,
        chart_js_src,
        speed_cutoff_factor,
        args.n_parallel,
        ctx_label,
    )
    html_path.write_text(html)
    print(f"\nHTML -> {html_path}")

    # Generate Markdown + SVG
    svg = generate_plot_svg(
        runs, speed_cutoff_factor=speed_cutoff_factor, ctx_label=ctx_label
    )
    plot_path.write_text(svg)
    print(f"SVG  -> {plot_path}")

    md = generate_markdown(
        runs,
        common_params,
        html_path=html_repo_rel,
        plot_path=plot_repo_rel,
        repo=repo,
        branch=branch,
        speed_cutoff_factor=speed_cutoff_factor,
        n_parallel=args.n_parallel,
        ctx_label=ctx_label,
    )
    md_path.write_text(md)
    print(f"MD   -> {md_path}")


if __name__ == "__main__":
    main()
