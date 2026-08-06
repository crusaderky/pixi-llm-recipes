"""Shared KV-cache sizing primitives.

Used by ``perplexity-report.py`` (KLD sweep report), ``perplexity.py`` (quant
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


# `-ctk kvarnN` selects a structured KVarN cache *and* a plain ggml fallback type
# (`kvarn_fallback_cache_type` in common/arg.cpp), which is what actually gets
# stored by a cache that never receives the KVarN params -- e.g. every component
# of DeepSeek-V4's bespoke cache (llama-kv-cache-dsv4.cpp passes
# `llama_kvarn_default_params()`, i.e. DISABLED).
KVARN_FALLBACK = {
    "kvarn2": "q2_0",
    "kvarn3": "q3_0",
    "kvarn4": "q4_0",
    "kvarn5": "q5_0",
    "kvarn6": "q6_0",
    "kvarn8": "q8_0",
}

# Head dims KVarN can quantize (`llama_kvarn_head_slices` in src/llama-kvarn.cpp):
# the CUDA kernels rotate each head through a Walsh-Hadamard transform in fixed
# 128-element slices (`KVAR_N_DIM = 128`, 128-thread launches,
# `GGML_ASSERT(head_width == 128 || 256 || 512)`), so a head must be exactly 1, 2
# or 4 slices wide. See ModelKV.support_kvarn.
KVARN_HEAD_DIMS = (128, 256, 512)


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
#      the window plus a per-sequence (tail + R) f16 overlay;
#    * a `compressed` group (DeepSeek-V4's CSA / HCA / lightning-indexer caches)
#      keeps one row per `ratio` tokens per sequence, tail-less, padded to 256
#      cells -- see CompressedKV.
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


class CacheGroupSize(NamedTuple):
    """One layer group's contribution to the cache, as returned by
    ``ModelKV.cache_breakdown``. ``key_dim`` / ``value_dim`` are that group's own
    head dims (``value_dim == 0`` for a K-only group), so a caller never has to
    reach back into ``ModelKV`` for them."""

    name: str
    layers: int  # cache layers, loop expansion included
    kv_heads: float
    key_dim: int
    value_dim: int
    note: str  # human-readable row derivation
    nbytes: float


class CompressedKV(NamedTuple):
    """A cache group that stores one row per ``ratio`` tokens, not one per token.

    DeepSeek-V4 keeps its whole history in three of these alongside a tiny raw
    sliding-window cache (llama-kv-cache-dsv4.cpp): CSA (one row per 4 tokens),
    HCA (one row per 128) and the lightning-indexer LID (one row per 4, at the
    indexer head dim). Each is a plain ``llama_kv_cache`` of
    ``GGML_PAD(ceil(n_ctx / ratio), 256)`` cells *per sequence*, K-only, stored at
    the run's K quant and with no exact tail.

    ``ratio = 1`` is therefore also meaningful -- one row per token, i.e. a plain
    full-context side cache such as the DeepSeek-V3.2 / GLM-DSA lightning-indexer
    key cache (llama-kv-cache-dsa.cpp), which is tail-less and so cannot be
    expressed as a full-attention group.

    ``ratio = 0`` instead describes a buffer that does not scale with the context
    at all, of ``fixed_rows`` rows -- DSV4's per-cache compressor ring state, two
    f32 tensors (kv + score) of ``n_embd_state`` per layer, hence ``key_dim ==
    value_dim == n_embd_state`` and ``elem_bpw = 32``.
    """

    name: str
    layers: int  # physical blocks in the group, before loop expansion
    kv_heads: float
    key_dim: int  # per-head key dim (the whole row width when kv_heads == 1)
    value_dim: int  # 0 for a K-only cache
    ratio: int = 0  # tokens per stored row; 0 => `fixed_rows`
    fixed_rows: int = 0  # rows when ratio == 0
    pad: int = 1  # row count is padded up to a multiple of this
    elem_bpw: float = 0.0  # 0 => the run's K/V quant; >0 => forced (f32 state)
    # One set of rows per sequence, as for a `unified = false` cache. False means
    # a single shared allocation, which is how the full-attention body above is
    # modelled (kv_unified).
    per_seq: bool = True


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

    ``compressed`` holds any cache groups that are not one row per token -- see
    ``CompressedKV``. They are additive to the two token groups above: a
    DeepSeek-V4 layer holds a raw sliding-window row *and* a compressed row, so
    the same layer is counted in both.
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
    # Sub-token-rate cache groups (DeepSeek-V4's CSA / HCA / LID + their
    # compressor state); empty for every ordinary transformer.
    compressed: tuple[CompressedKV, ...] = ()

    @property
    def full_attn_layers_all(self) -> int:
        """Full-attention cache layers, loop expansion included."""
        return self.full_attn_layers * self.n_loops

    @property
    def sliding_window_layers_all(self) -> int:
        """Sliding-window cache layers, loop expansion included."""
        return self.sliding_window_layers * self.n_loops

    @property
    def support_kvarn(self) -> bool:
        """Whether this geometry admits a ``kvarn*`` cache type at all.

        KVarN quantizes a 128x128 tile, so every cached head dim must be one of
        ``KVARN_HEAD_DIMS``. llama-context.cpp checks that per layer before
        allocating anything, and because ``fail_if_unsupported`` defaults to true
        with no CLI override, a run that asks for ``-ctk kvarnN`` on a narrower
        head *fails to start* -- it does not fall back. So a report must not
        quote KVarN figures for such a model: the whole LFM2 family, for one, has
        64-dim heads.

        A zero ``value_dim`` is not checked: it encodes the absence of a V cache
        (MLA / fused-latent / K-only), not a head dim.

        Head dims are all the geometry can answer. The arch-level exclusions --
        MLA, DSA, DFlash, and the architectures whose bespoke cache is never
        handed the KVarN params (see ``KVARN_FALLBACK``) -- are the caller's to
        apply.
        """
        dims = (self.key_dim, self.value_dim) if self.value_dim else (self.key_dim,)
        return all(dim in KVARN_HEAD_DIMS for dim in dims)

    @property
    def elems_per_token(self) -> int:
        """K+V cache elements per token over all *token-rate* cache layers, loop
        expansion included and *ignoring* the SWA window cap (so it is the no-SWA
        baseline, not the allocation). The quant-independent half of the sizing:
        multiply by a context length for elems, by bpw/8 for bytes.
        ``compressed`` groups are excluded -- their rows are not per-token, so
        they have no elems/token; use ``compressed_rows`` for their geometry."""
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

    def compressed_rows(self, group: CompressedKV, ctx_size: int) -> int:
        """Rows a compressed group holds per sequence at ``ctx_size`` tokens:
        ``GGML_PAD(ceil(ctx / ratio), pad)``, or its fixed row count when the
        group does not scale with the context."""
        rows = group.fixed_rows if group.ratio <= 0 else -(-ctx_size // group.ratio)
        pad = max(1, group.pad)
        return -(-rows // pad) * pad

    def _comp_group_bytes(
        self,
        group: CompressedKV,
        ctx_size: int,
        bpw_k: float,
        bpw_v: float,
        n_parallel: int,
    ) -> float:
        """A compressed group: one row per ``ratio`` tokens (or a fixed row
        count), with no exact-tail overlay."""
        rows = self.compressed_rows(group, ctx_size)
        if group.per_seq:
            rows *= n_parallel
        layers = group.layers * self.n_loops
        width = group.key_dim * (group.elem_bpw or bpw_k) + group.value_dim * (
            group.elem_bpw or bpw_v
        )
        return layers * group.kv_heads * width / 8 * rows

    def compressed_note(
        self, group: CompressedKV, ctx_size: int, n_parallel: int
    ) -> str:
        rows = self.compressed_rows(group, ctx_size)
        if group.ratio == 1:
            note = f"1 row per tok -> {rows} rows"
        elif group.ratio > 1:
            note = f"1 row per {group.ratio} tok -> {rows} rows"
        else:
            note = f"{rows} fixed rows"
        if group.elem_bpw:
            note += f" @ {group.elem_bpw:g} bpw"
        if n_parallel > 1 and group.per_seq:
            note += f" x{n_parallel} seq"
        return note

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
        total = 0.0
        if self.full_attn_layers:
            total += self._full_group_bytes(
                ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
            )
        if self.sliding_window_layers:
            total += self._swa_group_bytes(
                ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
            )
        for group in self.compressed:
            total += self._comp_group_bytes(group, ctx_size, bpw_k, bpw_v, n_parallel)
        return total

    def cache_breakdown(
        self,
        ctx_size: int,
        bpw_k: float,
        bpw_v: float,
        kv_tail_tokens: int,
        n_parallel: int,
    ) -> list[CacheGroupSize]:
        """Per-group derivation for the tooltip. Groups with no layers are
        omitted, so a model whose every layer is sliding-window (DeepSeek-V4)
        yields no full-attention row."""
        lossy = max(bpw_k, bpw_v) < F16_BPW
        rows: list[CacheGroupSize] = []

        if self.full_attn_layers:
            full_note = (
                f"body {ctx_size} tok + f16 exact tail "
                f"{self._exact_rows(kv_tail_tokens, n_parallel)} rows"
                if lossy and kv_tail_tokens > 0
                else f"exact f16 body {ctx_size} tok"
            )
            rows.append(
                CacheGroupSize(
                    "full-attn",
                    self.full_attn_layers_all,
                    self.full_attn_kv_heads,
                    self.key_dim,
                    self.value_dim,
                    full_note,
                    self._full_group_bytes(
                        ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
                    ),
                )
            )
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
                CacheGroupSize(
                    "sliding-window",
                    self.sliding_window_layers_all,
                    self.sliding_window_kv_heads,
                    self.key_dim,
                    self.value_dim,
                    swa_note,
                    self._swa_group_bytes(
                        ctx_size, bpw_k, bpw_v, kv_tail_tokens, n_parallel
                    ),
                )
            )
        for group in self.compressed:
            rows.append(
                CacheGroupSize(
                    group.name,
                    group.layers * self.n_loops,
                    group.kv_heads,
                    group.key_dim,
                    group.value_dim,
                    self.compressed_note(group, ctx_size, n_parallel),
                    self._comp_group_bytes(group, ctx_size, bpw_k, bpw_v, n_parallel),
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
    # Layer counts are the non-zero entries of the per-layer `attention.head_count_kv`
    # array, not `block_count`: the remaining blocks are shortconv layers with no KV
    # cache.
    "LFM2.5-230M": ModelKV(
        full_attn_layers=6,
        full_attn_kv_heads=8,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=64,  # kvarn not supported
        value_dim=64,
    ),
    "LFM2.5-8B-A1B": ModelKV(
        full_attn_layers=6,
        full_attn_kv_heads=8,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=64,  # kvarn not supported
        value_dim=64,
    ),
    # block_count=30; head_count_kv is 8 on blocks 2, 5, 9, 13, 17, 21, 24, 27.
    "LFM2.5-2.6B": ModelKV(
        full_attn_layers=8,
        full_attn_kv_heads=8,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=64,  # kvarn not supported
        value_dim=64,
    ),
    # `bailingmoe3` hybrid: of its 42 blocks, `attention.head_count_kv` is 1 on
    # blocks 5, 11, 17, 23, 29, 35, 41 (MLA) and 0 on the other 35, which are KDA
    # (Kimi delta-net) linear-attention blocks holding a fixed-size recurrent state
    # instead of a KV cache -- so they contribute nothing here, and ModelKV does not
    # model them (llama_hparams::n_embd_r/n_embd_s: 3*(ssm.conv_kernel-1)*n_head*
    # kda.head_dim + kda.head_dim^2*n_head = 561152 f32 elems per layer per
    # sequence, ~0.07 GiB over the 35 blocks -- context-independent).
    #
    # `attention.key_length` = 576 is already the cached latent (kv_lora_rank 512 +
    # rope.dimension_count 64), and `key_length_mla`/`value_length_mla` (192/128)
    # are present, so `is_mla()` holds and llama.cpp allocates no V cache:
    # `attention.value_length` (512) must not be counted. KVarN is doubly out here
    # -- the MLA cache path is rejected outright and 576 is not a KVarN head dim.
    #
    # `nextn_predict_layers = 1`, but the GGUF ships no `nextn.*` tensors and no
    # 43rd block, so all 7 MLA blocks are real cached layers.
    "Ling-3.0-flash": ModelKV(
        full_attn_layers=7,
        full_attn_kv_heads=1,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=576,  # kvarn not supported
        value_dim=0,
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
    # The one entry whose token cache is NOT where the context lives. `deepseek4`
    # calls set_swa_pattern(0), so all 43 blocks are sliding-window at the
    # 128-token `attention.sliding_window` -- ~5 MiB, a rounding error -- and the
    # history sits in the three compressed side caches below, which
    # `llama_kv_cache_dsv4` allocates next to it. K-only throughout:
    # `dsv4_make_k_only()` fakes is_mla() on every one of its cache hparam copies
    # to get `has_v = false`, so `attention.value_length` (512) is never allocated.
    #
    # The groups follow `attention.compress_ratios` (2 layers at 0, i.e. no
    # compressed cache; 21 at 4 -> CSA; 20 at 128 -> HCA) and, on the CSA layers,
    # `attention.indexer.key_length` = 128 -> LID with head_count_kv forced to 1.
    # Each compressed cache owns a context-independent f32 compressor ring state of
    # `state_size` rows x two tensors (kv + score) of `n_embd_state`.
    #
    # Two caveats for a sweep on this model, both arch facts rather than geometry:
    # KVarN never reaches any of these caches (see KVARN_FALLBACK), and the exact
    # tail reaches only the raw window -- the compressed caches are built with
    # tail 0, and llama-context.cpp clamps the SWA tail to the window, so every
    # `--kv-tail-tokens >= 128` is the same allocation.
    "DeepSeek-V4-Flash": ModelKV(
        full_attn_layers=0,
        full_attn_kv_heads=0,
        sliding_window_layers=43,
        sliding_window_kv_heads=1,
        sliding_window_size=128,
        key_dim=512,
        value_dim=0,
        compressed=(
            CompressedKV("csa", 21, 1, 512, 0, ratio=4, pad=256),
            CompressedKV("csa state", 21, 1, 1024, 1024, fixed_rows=8, elem_bpw=32.0),
            CompressedKV("hca", 20, 1, 512, 0, ratio=128, pad=256),
            CompressedKV("hca state", 20, 1, 512, 512, fixed_rows=128, elem_bpw=32.0),
            CompressedKV("lid (indexer)", 21, 1, 128, 0, ratio=4, pad=256),
            CompressedKV("lid state", 21, 1, 256, 256, fixed_rows=8, elem_bpw=32.0),
        ),
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
