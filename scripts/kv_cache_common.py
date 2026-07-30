"""Shared KV-cache sizing primitives.

Used by ``kv-kld-report.py`` (KLD sweep report), ``kv-perplexity.py`` (quant
precision ordering) and ``gguf-meta-extract.py`` (VRAM estimate from a GGUF
header). Keeping the bits-per-weight table and the cache-geometry model in one
place stops the three from drifting apart.

The scripts that import this live in the same directory and are invoked as
``python scripts/<name>.py``, so ``sys.path[0]`` is ``scripts/`` and a plain
``import kv_cache_common`` resolves. (Their own file names are hyphenated and
therefore not importable, which is why this module carries the shared code.)
"""

import sys
from typing import NamedTuple

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
    # beellama.cpp low/high-bit KV quants (block of 32 unless noted;
    # d/m = fp16 scale/min).  Sizes verified against ggml-common.h static_asserts.
    "q6_1": 7.0,  # d(fp16)=16 + m(fp16)=16 + qs(6b*32)=192 => 224/32
    "q6_0": 6.5,  # d(fp16)=16 + qs(6b*32)=192 => 208/32
    "q3_1": 4.0,  # d(fp16)=16 + m(fp16)=16 + qs(3b*32)=96 => 128/32
    "q3_0": 3.5,  # d(fp16)=16 + qs(3b*32)=96 => 112/32
    "q2_1": 3.0,  # d(fp16)=16 + m(fp16)=16 + qs(2b*32)=64 => 96/32
    "q2_0": 2.25,  # QK2_0=64: d(fp16)=16 + qs(2b*64)=128 => 144/64
    # beellama.cpp KVarN N-bit cache: 128x128 tile, N-bit payload + per-row/col
    # fp16 scales.  Per element = (16384*N + 6144) / 16384 = N + 3/8 bpw (K==V).
    "kvarn8": 8.375,  # 8 + 3/8
    "kvarn6": 6.375,  # 6 + 3/8
    "kvarn5": 5.375,  # 5 + 3/8
    "kvarn4": 4.375,  # 4 + 3/8
    "kvarn3": 3.375,  # 3 + 3/8
    "kvarn2": 2.375,  # 2 + 3/8
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
    # TheTom's TurboQuant K-quant types — not used for KV cache but kept for reference
    "tq3_1s": 4.0,
    "tq4_1s": 5.0,
}


def resolve_bpw(name: str) -> float:
    key = name.strip().lower()
    try:
        return BPW[key]
    except KeyError:
        print(f"WARNING: unknown quant '{name}', using 32.0 bpw", file=sys.stderr)
        return 32.0


# ---------------------------------------------------------------------------
#  Per-model KV-cache geometry.
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
#  tail and n_parallel, so the figure is deployment-dependent. Derived from
#  reading the v0.4.1 source; NOT yet re-validated against v0.4.1 llama-server
#  logs.
# ---------------------------------------------------------------------------
F16_BPW = 16  # bits per f16 cache element
# v0.4.1 compact precision-tail: persistent exact rows = (N + R) per sequence,
# R = rollback horizon (cparams kv_tail_rollback_tokens; defaults to 1 when a
# tail is present). The sink / ubatch / tile padding are graph-local, not here.
KV_TAIL_ROLLBACK = 1


class ModelKV(NamedTuple):
    """KV-cache geometry of a model: enough to size its cache at any context.

    Attention layers fall into two groups. Full-attention layers cache the whole
    context; sliding-window (SWA) layers cache only the most recent
    ``sliding_window_size`` tokens. The KV-head count is uniform within a group
    but may differ between groups -- e.g. Gemma's global (full) layers use fewer
    KV heads than its local (sliding) layers -- so each group carries its own
    count. Layers with no KV cache (hybrid conv/recurrent layers) are left out of
    both counts.

    Only the product ``layers * kv_heads`` of a group enters the arithmetic, so a
    model whose per-layer KV-head count varies *within* a group can be described
    by passing the group average as a fractional ``*_kv_heads`` -- which is what
    ``gguf-meta-extract.py`` does when a GGUF carries a per-layer
    ``head_count_kv`` array.

    ``value_dim`` may be 0, which is how an MLA / latent cache (DeepSeek, GLM-4.6+)
    is expressed: a single fused entry per token per layer, whose width goes in
    ``key_dim`` and which is stored at the K cache type.

    The two layer counts are **physical blocks**, i.e. `{arch}.block_count`
    straight out of the GGUF. A looped / recursive transformer runs those blocks
    ``n_loops`` times and *every pass gets its own KV-cache layer*, so the cache
    spans ``block_count * num_loops`` layers; set ``n_loops`` and the arithmetic
    below expands both groups for you. Never pre-multiply the layer counts by
    hand -- ``layers_all`` / ``elems_per_token`` are the only loop-aware
    quantities and every caller must go through them.
    """

    full_attn_layers: int  # physical blocks, before loop expansion
    full_attn_kv_heads: float  # KV heads per full-attention layer
    sliding_window_layers: int  # physical blocks, before loop expansion
    sliding_window_kv_heads: float  # KV heads per sliding-window layer
    sliding_window_size: int  # tokens; 0 when the model has no SWA
    key_dim: int  # per-head key dimension
    value_dim: int  # per-head value dimension
    # `{arch}.num_loops`: passes over the physical blocks, each with its own
    # cache layer (llama.cpp: `n_layer_all = block_count * num_loops`). 1 for
    # every ordinary transformer.
    n_loops: int = 1

    @property
    def full_attn_layers_all(self) -> int:
        """Full-attention cache layers, loop expansion included."""
        return self.full_attn_layers * self.n_loops

    @property
    def sliding_window_layers_all(self) -> int:
        """Sliding-window cache layers, loop expansion included."""
        return self.sliding_window_layers * self.n_loops

    @property
    def elems_per_token(self) -> int:
        """K+V cache elements per token over all cache layers, loop expansion
        included and *ignoring* the SWA window cap (so it is the no-SWA
        baseline, not the allocation). The quant-independent half of the sizing:
        multiply by a context length for elems, by bpw/8 for bytes."""
        width = self.key_dim + self.value_dim
        return int(
            self.full_attn_layers_all * self.full_attn_kv_heads * width
            + self.sliding_window_layers_all * self.sliding_window_kv_heads * width
        )

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
        layers, heads = self.full_attn_layers_all, self.full_attn_kv_heads

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
        layers, heads = self.sliding_window_layers_all, self.sliding_window_kv_heads
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
                self.full_attn_layers_all,
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
                    self.sliding_window_layers_all,
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
#
# Geometry values were read from the GGUF headers of the models pinned in
# models.ini (via scripts/gguf-meta-extract.py). Transcribe each hparam as it
# appears in the header -- layer counts are `block_count`, and a `num_loops`
# goes in `n_loops`. Do NOT fold one field into another: every derived quantity
# is a ModelKV method, so a curated entry and a GGUF-derived one always agree.
MODEL_KV: dict[str, ModelKV] = {
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
    # Looped transformer: block_count=22, num_loops=2 -> 44 cache layers.
    # Both values go in as-is; ModelKV does the multiplication.
    "Nanbeige4.2-3B": ModelKV(
        full_attn_layers=22,
        full_attn_kv_heads=8,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=128,
        value_dim=128,
        n_loops=2,
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
MODEL_KV["Ornith-1.0-35B"] = MODEL_KV["Qwen3.6-35B-A3B"]
MODEL_KV["Kat-Coder-V2.5-Dev"] = MODEL_KV["Qwen3.6-35B-A3B"]
MODEL_KV["Ternary-Bonsai-27B"] = MODEL_KV["Qwen3.6-27B"]
MODEL_KV["Bonsai-27B"] = MODEL_KV["Qwen3.6-27B"]


def resolve_model(model_ref: str):
    """Return (key, spec) for the first MODEL_KV name that is a case-insensitive
    substring of ``model_ref`` (longest key first), or None."""
    if not model_ref:
        return None
    ref = model_ref.lower()
    for key, value in MODEL_KV.items():
        if key.lower() in ref:
            return key, value
    return None
