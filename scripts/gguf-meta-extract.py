#!/usr/bin/env python

"""
Point this at a Hugging Face repo directory (or whole repo), or at a single
.gguf file (a blob/resolve URL), containing split/single GGUF files. It finds
every .gguf file (or just the one named), downloads ONLY the header of each
(via HTTP Range requests -- a few MB, never the multi-GB tensor payload), parses
the tensor-info table, and writes a CSV with one row per tensor:

    layer, tensor_name, geometry, n_points, quant, bytes_per_point, total_bytes

Usage
-----
    pip install huggingface_hub requests        # gguf is optional (nicer names)

    # whole directory:
    python gguf_tensor_csv.py \
        https://huggingface.co/unsloth/GLM-5.2-GGUF/tree/main/UD-IQ1_S \
        -o glm52_iq1s.csv

    # single file (when several variant GGUFs share one folder):
    python gguf_tensor_csv.py \
        https://huggingface.co/owner/repo/blob/main/model-IQ2XXS.gguf \
        -o model.csv

    # glob pattern (match multiple .gguf files by name):
    python gguf_tensor_csv.py \
        'https://huggingface.co/owner/repo/blob/main/Hy3-UD128-*' \
        -o hy3_ud128.csv

    # private/gated repos:
    HF_TOKEN=hf_xxx python gguf_tensor_csv.py <url> -o out.csv

Why we parse the header ourselves instead of gguf.GGUFReader
------------------------------------------------------------
gguf.GGUFReader eagerly materialises a numpy view of every tensor's *data* at
construction time, so it raises (e.g. "cannot reshape array of size N") on a
header-only / truncated file -- it has no header-only mode. The GGUF header,
however, is fully self-describing (name + shape + dtype for every tensor live
before the data section), so a tiny direct parser reads everything we need from
the first few MB. Validated to produce byte-identical name/shape/dtype output to
GGUFReader on complete files.
"""

import argparse
import csv
import fnmatch
import math
import re
import struct
import sys
from urllib.parse import unquote, urlparse

import requests
from kv_cache_common import KVARN_FALLBACK, CompressedKV, ModelKV, resolve_bpw

try:
    from huggingface_hub import HfApi, hf_hub_url
except ImportError:
    sys.exit("Need huggingface_hub:  pip install huggingface_hub")

# Optional: only used to give a human-readable name to an UNKNOWN ggml type id.
try:
    from gguf import GGMLQuantizationType as _GGUFType
except ImportError:
    _GGUFType = None


# --------------------------------------------------------------------------- #
# Hardcoded ggml type table: ggml_type_id ->
#   (name, block_size, bytes_per_block)  -- mainline ggml
#   (name, block_size, bytes_per_block, row_meta_size)  -- ik-llama.cpp / DFlash
# block_size = number of weights per block; bytes_per_block = on-disk size of
# one block. bytes_per_point = bytes_per_block / block_size.
# row_meta_size = extra per-row bytes (ik-llama's "+ f16/f32 per row" scale);
# total_bytes adds row_meta_size * nrows (nrows = product of dims[1:]).
# Mainline values verified against gguf.constants.GGML_QUANT_SIZES (gguf 0.19.0).
# ik-llama/DFlash values (ids >= 133) verified against ik_llama.cpp ggml.h
# enum + ggml.c type_traits[] + ggml-common.h block struct sizeofs
# (ikawrakow/ik_llama.cpp, Jul 2026).
# --------------------------------------------------------------------------- #
GGML_SIZES = {
    0: ("F32", 1, 4),
    1: ("F16", 1, 2),
    2: ("Q4_0", 32, 18),
    3: ("Q4_1", 32, 20),
    6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24),
    8: ("Q8_0", 32, 34),
    9: ("Q8_1", 32, 40),
    10: ("Q2_K", 256, 84),
    11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144),
    13: ("Q5_K", 256, 176),
    14: ("Q6_K", 256, 210),
    15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66),
    17: ("IQ2_XS", 256, 74),
    18: ("IQ3_XXS", 256, 98),
    19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18),
    21: ("IQ3_S", 256, 110),
    22: ("IQ2_S", 256, 82),
    23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1),
    25: ("I16", 1, 2),
    26: ("I32", 1, 4),
    27: ("I64", 1, 8),
    28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56),
    30: ("BF16", 1, 2),
    34: ("TQ1_0", 256, 54),
    35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17),
    40: ("NVFP4", 64, 36),
    41: ("Q1_0", 128, 18),
    # --- ik-llama.cpp / DFlash quant types (ids >= 133) ---
    # Internal q8 vec-dot types (97-99, 136, 147-151) are included for
    # completeness; they normally never appear as a stored tensor ttype.
    97: ("Q8_0_X4", 32, 34, 0),
    98: ("Q8_1_X4", 32, 36, 0),
    99: ("Q8_2_X4", 32, 36, 0),
    133: ("Q6_0", 32, 26, 0),
    134: ("IQ1_BN", 64, 13, 2),
    135: ("IQ2_BN", 64, 16, 4),
    136: ("Q8_K64", 64, 68, 0),
    137: ("IQ2_K", 256, 76, 0),
    138: ("IQ3_K", 256, 110, 0),
    139: ("IQ4_K", 256, 144, 0),
    140: ("IQ5_K", 256, 176, 0),
    141: ("IQ6_K", 256, 212, 0),
    144: ("IQ4_KS", 256, 136, 4),
    145: ("IQ2_KS", 256, 70, 2),
    146: ("IQ4_KSS", 256, 128, 4),
    147: ("Q8_K16", 64, 64, 20),
    148: ("Q8_K32", 256, 296, 0),
    149: ("Q8_KR8", 256, 296, 0),
    150: ("Q8_K128", 128, 140, 0),
    151: ("Q8_KV", 32, 32, 8),
    152: ("IQ5_KS", 256, 168, 4),
    153: ("IQ2_KT", 256, 68, 4),
    154: ("IQ3_KT", 256, 100, 4),
    155: ("IQ4_KT", 256, 128, 4),
    156: ("IQ3_KS", 256, 102, 2),
    157: ("IQ2_KL", 256, 86, 2),
    158: ("IQ1_KT", 256, 56, 4),
    # --- ik-llama row-interleaved (rN) repacks; same bytes-per-point as base ---
    202: ("Q4_0_R8", 32, 18, 0),
    206: ("Q5_0_R4", 32, 22, 0),
    208: ("Q8_0_R8", 32, 34, 0),
    210: ("Q2_K_R4", 256, 84, 0),
    211: ("Q3_K_R4", 256, 110, 0),
    212: ("Q4_K_R4", 256, 144, 0),
    213: ("Q5_K_R4", 256, 176, 0),
    214: ("Q6_K_R4", 256, 210, 0),
    216: ("IQ2_XXS_R4", 256, 66, 0),
    217: ("IQ2_XS_R4", 256, 74, 0),
    218: ("IQ3_XXS_R4", 256, 98, 0),
    219: ("IQ1_S_R4", 32, 6, 2),
    220: ("IQ4_NL_R4", 32, 18, 0),
    221: ("IQ3_S_R4", 256, 110, 0),
    222: ("IQ2_S_R4", 256, 82, 0),
    223: ("IQ4_XS_R8", 256, 136, 0),
    229: ("IQ1_M_R4", 32, 7, 2),
    230: ("BF16_R16", 1, 2, 0),
    233: ("Q6_0_R4", 32, 26, 0),
    335: ("IQ2_BN_R4", 64, 16, 4),
    337: ("IQ2_K_R4", 256, 76, 0),
    338: ("IQ3_K_R4", 256, 110, 0),
    339: ("IQ4_K_R4", 256, 144, 0),
    340: ("IQ5_K_R4", 256, 176, 0),
    344: ("IQ4_KS_R4", 256, 136, 4),
    352: ("IQ5_KS_R4", 256, 168, 4),
    397: ("Q8_K_R16", 256, 258, 0),
    398: ("Q8_KV_R8", 32, 32, 4),
    399: ("Q8_K_R8", 256, 258, 0),
}

_warned_unknown = set()


def quant_info(ttype):
    """Return (name, block_size, bytes_per_block, row_meta_size) for a ggml
    type id. Warns once per unknown type and returns (name, None, None, 0).
    row_meta_size is ik-llama's per-row scale overhead (0 for mainline types)."""
    if ttype in GGML_SIZES:
        entry = GGML_SIZES[ttype]
        if len(entry) == 3:  # legacy mainline 3-tuple
            return entry + (0,)
        return entry
    if ttype not in _warned_unknown:
        _warned_unknown.add(ttype)
        nm = "?"
        if _GGUFType:
            try:
                nm = _GGUFType(ttype).name
            except ValueError:
                pass
        sys.stderr.write(
            f"WARNING: unknown ggml type id {ttype} ({nm}); "
            f"sizes left blank for these tensors\n"
        )
    name = "?"
    if _GGUFType:
        try:
            name = _GGUFType(ttype).name
        except ValueError:
            pass
    return (f"UNKNOWN_{ttype}_{name}", None, None, 0)


# --------------------------------------------------------------------------- #
# Minimal GGUF header parser (header-only safe). Raises _Truncated if the buffer
# ends before the tensor-info table is fully read, signalling "fetch more".
# --------------------------------------------------------------------------- #
class _Truncated(Exception):
    pass


class _Cur:
    def __init__(self, b):
        self.b = b
        self.i = 0

    def take(self, n):
        if self.i + n > len(self.b):
            raise _Truncated()
        d = self.b[self.i : self.i + n]
        self.i += n
        return d

    def u32(self):
        return struct.unpack_from("<I", self.take(4))[0]

    def u64(self):
        return struct.unpack_from("<Q", self.take(8))[0]

    def gstr(self):
        return self.take(self.u64())


# GGUF metadata scalar value-type id -> (struct format, byte width)
_SCALAR = {
    0: ("<B", 1),  # UINT8
    1: ("<b", 1),  # INT8
    2: ("<H", 2),  # UINT16
    3: ("<h", 2),  # INT16
    4: ("<I", 4),  # UINT32
    5: ("<i", 4),  # INT32
    6: ("<f", 4),  # FLOAT32
    7: ("<?", 1),  # BOOL
    10: ("<Q", 8),  # UINT64
    11: ("<q", 8),  # INT64
    12: ("<d", 8),  # FLOAT64
}


def _read_value(c, vtype):
    """Decode one GGUF metadata value, advancing the cursor past it.

    Large arrays (the tokenizer vocab/merges) and string arrays are consumed to
    keep the cursor aligned but returned as None -- we only ever need small
    numeric arrays such as per-layer ``attention.head_count_kv``.
    """
    if vtype in _SCALAR:
        fmt, width = _SCALAR[vtype]
        return struct.unpack_from(fmt, c.take(width))[0]
    if vtype == 8:  # STRING
        return c.gstr().decode("utf-8", "replace")
    if vtype == 9:  # ARRAY
        atype = c.u32()
        n = c.u64()
        keep = atype != 8 and n <= 100000
        vals = [] if keep else None
        for _ in range(n):
            v = _read_value(c, atype)
            if keep:
                vals.append(v)
        return vals
    raise ValueError(f"unknown gguf metadata value type {vtype}")


def parse_header(buf):
    """Parse a GGUF header from bytes. Returns (metadata_dict, tensor_list)
    where tensor_list is a list of (name, dims, ttype).
    Raises _Truncated if more bytes are needed."""
    c = _Cur(buf)
    if c.take(4) != b"GGUF":
        raise ValueError("not a GGUF file (bad magic)")
    _version = c.u32()
    n_tensors = c.u64()
    n_kv = c.u64()
    metadata = {}
    for _ in range(n_kv):  # walk metadata to reach tensor table
        key = c.gstr().decode("utf-8", "replace")
        metadata[key] = _read_value(c, c.u32())
    out = []
    for _ in range(n_tensors):
        name = c.gstr().decode("utf-8", "replace")
        ndim = c.u32()
        dims = [c.u64() for _ in range(ndim)]
        ttype = c.u32()
        c.u64()  # data offset -- unused for our needs
        out.append((name, dims, ttype))
    return metadata, out


# --------------------------------------------------------------------------- #
# Networking: list files, fetch a header prefix with a hard download cap.
# --------------------------------------------------------------------------- #
def parse_hf_url(url):
    """-> (repo_id, revision, subdir_prefix). Accepts tree/blob/bare URLs."""
    p = urlparse(url)
    parts = [unquote(x) for x in p.path.strip("/").split("/") if x]
    if len(parts) < 2:
        raise ValueError(f"cannot parse repo from URL: {url}")
    repo_id = "/".join(parts[:2])
    revision, subdir = "main", ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob", "resolve"):
        revision = parts[3]
        subdir = "/".join(parts[4:])
    return repo_id, revision, subdir


def fetch_prefix(url, n, headers, timeout=120):
    """Download at most n bytes from the start of url. Streams and stops at n,
    so it is safe even if the server ignores the Range header."""
    h = dict(headers)
    h["Range"] = f"bytes=0-{n - 1}"
    with requests.get(
        url, headers=h, stream=True, timeout=timeout, allow_redirects=True
    ) as r:
        r.raise_for_status()
        buf = bytearray()
        for chunk in r.iter_content(1 << 16):
            buf.extend(chunk)
            if len(buf) >= n:
                break
    return bytes(buf[:n])


def read_gguf_tensors(
    url, headers, start=8 << 20, growth=4, cap=512 << 20, quiet=False
):
    """Fetch increasing prefixes until the header parses.
    Returns (metadata_dict, tensor_list)."""
    n = start
    while True:
        buf = fetch_prefix(url, n, headers)
        try:
            metadata, tensors = parse_header(buf)
            if not quiet:
                sys.stderr.write(
                    f"    header parsed from first {len(buf) / 1e6:.1f} MB "
                    f"({len(tensors)} tensors)\n"
                )
            return metadata, tensors
        except _Truncated:
            if len(buf) < n:  # got the whole file, still short
                raise ValueError("file ended before header finished parsing")
            if n >= cap:
                raise ValueError(f"header exceeds {cap} byte cap; raise --max-bytes")
            n = min(n * growth, cap)
            if not quiet:
                sys.stderr.write(
                    f"    need more bytes -> retrying with {n / 1e6:.0f} MB\n"
                )


# --------------------------------------------------------------------------- #
# Row building
# --------------------------------------------------------------------------- #
_BLK = re.compile(r"^blk\.(\d+)\.(.+)$")


def tensor_nbytes(dims, ttype):
    """On-disk byte size of a tensor -- also the size staged to VRAM, since the
    weight is copied at its native quant. Returns None if the quant is unknown.
    Adds ik-llama per-row scale overhead (row_meta_size * nrows) when present."""
    n_points = 1
    for d in dims:
        n_points *= d
    _, block, tsize, row_meta = quant_info(ttype)
    if block is None:
        return None
    nbytes = (
        math.ceil(n_points / block) * tsize
    )  # block payload (exact for real tensors)
    if row_meta:
        # nrows = all dims except the innermost (dim 0); 1 for a 1-D tensor.
        nrows = 1
        for d in dims[1:]:
            nrows *= d
        nbytes += row_meta * nrows
    return nbytes


def build_row(name, dims, ttype):
    m = _BLK.match(name)
    if m:
        layer, tname = m.group(1), m.group(2)
    else:
        layer, tname = "", name

    geometry = "x".join(str(d) for d in dims)
    n_points = 1
    for d in dims:
        n_points *= d

    qname, block, tsize, _row_meta = quant_info(ttype)
    if block is None:  # unknown type: no size info
        bpp, total = "", ""
    else:
        # bytes-per-point is the block payload only; the per-row scale overhead
        # (ik-llama row_meta) is folded into total_bytes, not expressible per-point.
        bpp = f"{tsize / block:.6g}"
        total = tensor_nbytes(dims, ttype)
    return [layer, tname, geometry, n_points, qname, bpp, total]


# --------------------------------------------------------------------------- #
# Context / scratch VRAM estimate
# --------------------------------------------------------------------------- #
# Fixed CUDA context + driver/handle overhead per device. This is the part of
# VRAM usage that does NOT scale with context length, batch size, or KV-cache
# quantization. ~400 MiB is a rough but typical figure for the llama.cpp CUDA
# backend; adjust for your driver/GPU.
CUDA_CTX_OVERHEAD = 400 << 20


# Match the per-layer KV-cache-defining tensors by their exact (blk.N-stripped)
# name. Exact matching matters: a substring test would wrongly catch tensors
# like `indexer.attn_k.weight` (the sparse-attention lightning indexer in
# DeepSeek/GLM-style models), which is NOT part of the KV cache.
_RE_ATTN_K = re.compile(r"^blk\.\d+\.attn_k\.weight$")
_RE_ATTN_V = re.compile(r"^blk\.\d+\.attn_v\.weight$")
# MLA (Multi-head Latent Attention, e.g. DeepSeek / GLM-4.6+): llama.cpp caches
# the compressed latent produced by the kv down-projection, whose output width
# (kv_lora_rank + rope dims) IS the per-layer-per-token cache size.
_RE_MLA = re.compile(r"^blk\.\d+\.attn_kv_a_mqa\.weight$")


def _norm_per_layer(val, n_layer):
    """Coerce a scalar-or-list hparam into a length-n_layer list, or None."""
    if val is None:
        return None
    if isinstance(val, list):
        return val[:n_layer] if len(val) >= n_layer else None
    return [val] * n_layer


def _n_loops(md):
    """Loop count of a looped / recursive transformer, >= 1.

    An arch that carries `{arch}.num_loops` (nanbeige) runs its `block_count`
    physical blocks that many times and gives **every pass its own KV-cache
    layer**: its loader tiles the per-layer hparam arrays and sets
    `n_layer_all = block_count * num_loops` (`src/models/nanbeige.cpp`), and
    the cache is allocated over `n_layer()` == `n_layer_all`. Sizing on
    `block_count` alone understates the cache by exactly this factor.

    This function only *reads* the key: the expansion itself is `ModelKV`'s
    `n_loops` field, so it applies to the hand-curated `MODEL_KV` table and the
    KLD report too.
    """
    arch = md.get("general.architecture")
    n = md.get(f"{arch}.num_loops") if arch else None
    return int(n) if isinstance(n, (int, float)) and int(n) > 1 else 1


def _swa_flags(pattern, n_layer, dense_first=False):
    """Per-layer SWA bool list, mirroring `llama_hparams::set_swa_pattern`.

    * Array form (e.g. gemma4, lfm2, mimo2): one bool per layer, True => SWA.
    * Scalar period `n`: with ``dense_first=False`` layer il is SWA iff
      `il % n < n-1` (the full-attention layer sits at the END of each group of
      n); with ``dense_first=True`` iff `il % n != 0` (at the START). `n == 0`
      means every layer is SWA, `n == 1` that none is.

    ``dense_first`` is never carried by the GGUF -- it is baked into each
    architecture's loader -- so it comes from `_ARCH_SWA_PATTERN`.

    Returns a list[bool] of length n_layer, or None if there is no pattern.
    """
    if pattern is None:
        return None
    if isinstance(pattern, list):
        return [bool(x) for x in pattern[:n_layer]] if len(pattern) >= n_layer else None
    n = int(pattern)
    if n <= 0:
        return [True] * n_layer
    if dense_first:
        return [(il % n) != 0 for il in range(n_layer)]
    return [(il % n) < (n - 1) for il in range(n_layer)]


# Per-architecture sliding-window pattern, mirroring the
# `hparams.set_swa_pattern(period, dense_first)` call in each arch's loader in
# llama.cpp (`src/models/<arch>.cpp`). arch name -> (default period, dense_first);
# period 0 => every layer is SWA, period 1 => none is.
#
# This table is not redundant with the GGUF: `attention.sliding_window_pattern`
# is optional and, when present, only overrides the *period* -- the dense-first
# phase lives exclusively in the arch code, as does the period itself for the
# many GGUFs that omit the key. Without it a model that declares
# `attention.sliding_window` but no pattern (every Laguna and Gemma-3 GGUF, for
# two) is sized as if all its layers were full-attention, overstating the cache
# several-fold.
#
# Architectures that read the pattern as a per-layer bool array (gemma4, lfm2,
# mimo2, step35, nanbeige, dflash) need no entry -- `_swa_flags` takes the array
# straight from the GGUF. cohere2moe and mellum are deliberately absent: their
# loaders apply a period only when the key is present, and fall back to no SWA.
_ARCH_SWA_PATTERN = {
    "afmoe": (4, False),
    "cohere2": (4, False),
    "deepseek4": (0, False),
    "exaone-moe": (4, False),
    "exaone4": (4, False),
    "gemma-embedding": (6, False),
    "gemma2": (2, False),
    "gemma3": (6, False),
    "gemma3n": (5, False),
    "gpt-oss": (2, False),
    "laguna": (4, True),
    "llama4": (4, False),
    "modern-bert": (3, True),
    "olmo2": (4, False),
    "phi3": (1, False),
    "plamo3": (8, False),
    "smallthinker": (4, True),
}

# Architectures whose loader forces K-only cache storage without the GGUF saying
# so. DeepSeek-V4 fakes `is_mla()` on all four of its cache hparam copies
# (`dsv4_make_k_only` in llama-kv-cache-dsv4.cpp) purely to get `has_v = false`
# storage, so `attention.value_length` is never allocated -- exactly as for a real
# MLA model, but without `key_length_mla` / `value_length_mla` in the header.
_ARCH_K_ONLY_CACHE = {"deepseek4"}

# Architectures that keep their lightning-indexer keys in the *main* KV cache, as
# an extra f32 stream of `n_embd_k_idx(il)` next to K/V (`hparams.indexer_kv =
# true`, allocated in llama-kv-cache.cpp). MiniMax-M3's MSA is the only one.
# The other three architectures that ship `.indexer.` tensors -- deepseek32,
# glm-dsa and deepseek4 -- put those keys in a side cache of their own instead, at
# the run's K quant and (for deepseek4) at one row per 4 tokens; `_compressed_groups`
# models those, so charging them a full-context f32 stream as well would both
# double-count and overstate the rate.
_ARCH_INDEXER_KV = {"minimax-m3"}

# Architectures whose bespoke cache never receives the KVarN params, so
# `-ctk kvarnN` silently stores the plain fallback type instead
# (`kv_cache_common.KVARN_FALLBACK`). llama-model.cpp branches to these caches
# before the `params.kvarn.type != DISABLED` test that everything else goes
# through: `llama_kv_cache_dsa` (deepseek32 / glm-dsa) has no KVarN parameter at
# all and builds both its children as plain `llama_kv_cache`, and
# `llama_kv_cache_dsv4` passes `llama_kvarn_default_params()` (DISABLED) to its raw
# cache and nothing to its compressed ones.
_ARCH_NO_KVARN = {"deepseek32", "deepseek4", "glm-dsa"}

# DeepSeek-V4 compressed caches, mirroring llama-kv-cache-dsv4.cpp: the only two
# `attention.compress_ratios` values the loader accepts (0 = no compressed cache
# on that layer), the 256-cell padding every compressed cache gets, and each
# cache's compressor ring state (`state_size` rows of two f32 tensors, kv+score,
# of `n_embd_state` each, per layer). CSA layers additionally drive the
# lightning-indexer (LID) cache, which shares their ratio at the indexer head dim.
_DSV4_CSA_RATIO = 4
_DSV4_HCA_RATIO = 128
_DSV4_COMP_PAD = 256
_DSV4_STATE_BPW = 32.0  # compressor state is f32 regardless of the cache quant


# KV-cache configurations reported on: (symmetric K/V cache quant, beellama
# --kv-tail-tokens). Bits-per-element come from kv_cache_common.BPW, which folds
# in each format's block/tile overhead (so kvarn4 is 4.375 bpw, not a flat 4)
# and is shared with the KLD sweep tooling. The tails pair each quant with the
# exact-tail size that makes it usable in practice: the lossier the body, the
# longer the f16 tail needed to hold recent tokens exactly.
KV_QUANTS = (
    ("f16", 0),
    ("q8_0", 0),
    ("kvarn5", 128),
    ("kvarn4", 1024),
    ("kvarn3", 2048),
)

# One sequence: the estimate is for a single llama-cli / llama-server slot. The
# f16 exact-tail overlay ModelKV adds is per-sequence, so raise this to size a
# multi-slot deployment.
KV_N_PARALLEL = 1


def _kv_label(quant, tail):
    """Display label for a (quant, tail) pair: ``q8_0``, ``kvarn4 t1024``."""
    return f"{quant} t{tail}" if tail else quant


def _kv_display_label(quant, tail, arch):
    """Headline label for a (quant, tail) pair, naming the type actually stored
    when it differs from the one asked for: ``kvarn5 -> q5_0 t128``."""
    effective = _effective_quant(quant, arch)
    name = quant if effective == quant else f"{quant} -> {effective}"
    return f"{name} t{tail}" if tail else name


def _effective_quant(quant, arch):
    """The quant `arch` actually stores when the run asks for `quant`.

    Identity everywhere except the `_ARCH_NO_KVARN` architectures, whose caches
    never see the KVarN params: there `-ctk kvarnN` falls through to the plain
    `qN_0` type the CLI pairs it with, which is a slightly *wider* body.
    """
    if arch in _ARCH_NO_KVARN:
        return KVARN_FALLBACK.get(quant, quant)
    return quant


# Width of the widest label, so the byte columns line up.
_KV_LABEL_W = max(len(_kv_label(q, t)) for q, t in KV_QUANTS)


def _group_kv_heads(head_kv, layers):
    """KV heads per layer for a group of layer indices.

    Returns the shared count when the group is uniform (the usual case) and the
    group average otherwise -- exact either way, because only ``layers *
    kv_heads`` enters ModelKV's arithmetic.
    """
    if not layers:
        return 0
    counts = {head_kv[il] for il in layers}
    if len(counts) == 1:
        return counts.pop()
    return sum(head_kv[il] for il in layers) / len(layers)


def _compressed_groups(md, arch, head_kv, key_length, cached, n_layer):
    """Cache groups an architecture allocates *besides* its token KV cache.

    Empty for all but the sparse-attention architectures, which cache their
    lightning-indexer keys in a side cache of their own instead of in the main one
    (`_ARCH_INDEXER_KV`):

      * `deepseek32` / `glm-dsa` -- `llama_kv_cache_dsa` builds one extra
        full-context K-only cache with `head_count_kv` forced to 1 and the head
        dim replaced by `attention.indexer.key_length`, at the run's K quant and
        with no exact tail;
      * `deepseek4` -- three *compressed* caches plus compressor state, see
        `_dsv4_compressed_groups`, and the raw token cache holds only a
        128-token window.
    """
    if arch == "deepseek4":
        return _dsv4_compressed_groups(md, arch, head_kv, key_length, n_layer)
    if arch not in ("deepseek32", "glm-dsa"):
        return ()

    indexer_dim = md.get(f"{arch}.attention.indexer.key_length")
    if not indexer_dim:
        sys.stderr.write(
            f"WARNING: '{arch}' carries no attention.indexer.key_length; its "
            "lightning-indexer key cache is not counted below.\n"
        )
        return ()
    return (
        CompressedKV(
            "lid (indexer)",
            len(cached),
            1,  # hparams_lid.n_head_kv_arr is filled with 1
            indexer_dim,
            0,  # K-only (these archs are MLA, so `has_v` is already false)
            ratio=1,
            per_seq=False,  # follows the main cache's kv_unified
        ),
    )


def _dsv4_compressed_groups(md, arch, head_kv, key_length, n_layer):
    """DeepSeek-V4's compressed cache groups, as a tuple of CompressedKV.

    DSV4's raw token cache is a 128-token sliding window -- by itself a rounding
    error. The history lives in three compressed caches that
    `llama_kv_cache_dsv4` allocates next to it, each a plain `llama_kv_cache` of
    `GGML_PAD(ceil(n_ctx / ratio), 256)` K-only cells per sequence:

      * CSA -- the `attention.compress_ratios == 4` layers, at the ordinary
        `n_embd_k_gqa` width (`attention.key_length` x `head_count_kv`);
      * HCA -- the `compress_ratios == 128` layers, same width;
      * LID -- the lightning indexer, on the *CSA* layers at the CSA ratio, but
        with `head_count_kv` forced to 1 and the head dim replaced by
        `attention.indexer.key_length`.

    Each also owns a fixed-size f32 compressor ring state, two tensors (kv and
    score) of `n_embd_state` per layer over `state_size` rows: CSA and LID use
    `2 x ratio` rows of `2 x head_dim`, HCA `ratio` rows of `head_dim`.

    Returns () when the metadata does not describe the compressed layout, after
    warning -- the caller then reports the raw sliding-window cache alone, which
    is a large *under*estimate.
    """
    ratios = _norm_per_layer(md.get(f"{arch}.attention.compress_ratios"), n_layer)
    indexer_dim = md.get(f"{arch}.attention.indexer.key_length")
    if ratios is None:
        sys.stderr.write(
            f"WARNING: '{arch}' carries no per-layer attention.compress_ratios; "
            "its compressed KV caches -- which hold the entire context -- cannot "
            "be sized. The KV-cache figures below cover only the "
            "sliding-window cache and are a drastic UNDERESTIMATE.\n"
        )
        return ()
    unknown = sorted({r for r in ratios} - {0, _DSV4_CSA_RATIO, _DSV4_HCA_RATIO})
    if unknown:
        # llama.cpp itself refuses to load these ("only supports compression
        # ratios 0, 4, and 128"), so they are sized but not trusted.
        sys.stderr.write(
            f"WARNING: '{arch}' declares compression ratios {unknown}, which "
            "llama.cpp does not support; sizing them as ordinary compressed "
            "caches.\n"
        )

    groups = []
    csa_layers = [il for il in range(n_layer) if ratios[il] == _DSV4_CSA_RATIO]
    other = sorted({r for r in ratios if r and r != _DSV4_CSA_RATIO})
    for ratio in [_DSV4_CSA_RATIO] + other:
        layers = [il for il in range(n_layer) if ratios[il] == ratio]
        if not layers:
            continue
        name = {_DSV4_CSA_RATIO: "csa", _DSV4_HCA_RATIO: "hca"}.get(
            ratio, f"comp/{ratio}"
        )
        groups.append(
            CompressedKV(
                name,
                len(layers),
                _group_kv_heads(head_kv, layers),
                key_length,
                0,  # K-only: dsv4_make_k_only()
                ratio=ratio,
                pad=_DSV4_COMP_PAD,
            )
        )
        # Compressor state: `2 x ratio` rows of `2 x head_dim` for CSA (and LID
        # below), `ratio` rows of `head_dim` for HCA.
        wide = ratio == _DSV4_CSA_RATIO
        state_dim = 2 * key_length if wide else key_length
        groups.append(
            CompressedKV(
                f"{name} state",
                len(layers),
                1,
                state_dim,
                state_dim,  # two f32 tensors per layer: kv + score
                fixed_rows=2 * ratio if wide else ratio,
                elem_bpw=_DSV4_STATE_BPW,
            )
        )

    if csa_layers and indexer_dim:
        groups.append(
            CompressedKV(
                "lid (indexer)",
                len(csa_layers),
                1,  # hparams_lid.n_head_kv_arr is filled with 1
                indexer_dim,
                0,
                ratio=_DSV4_CSA_RATIO,
                pad=_DSV4_COMP_PAD,
            )
        )
        groups.append(
            CompressedKV(
                "lid state",
                len(csa_layers),
                1,
                2 * indexer_dim,
                2 * indexer_dim,
                fixed_rows=2 * _DSV4_CSA_RATIO,
                elem_bpw=_DSV4_STATE_BPW,
            )
        )
    elif csa_layers:
        sys.stderr.write(
            f"WARNING: '{arch}' has compressed layers but no "
            "attention.indexer.key_length; its lightning-indexer cache is not "
            "counted below.\n"
        )
    return tuple(groups)


def _model_kv_from_metadata(md):
    """KV-cache geometry derived from GGUF hparams, as a ModelKV.

    Handles fused QKV (no separate attn_k/attn_v tensors), per-layer GQA
    (`head_count_kv` as an array), distinct key/value head dims, and
    sliding-window attention. Layers whose `head_count_kv` is 0 -- the conv /
    recurrent blocks of a hybrid model -- hold no KV cache and are counted in
    neither group, per the ModelKV contract. Layer counts stay *physical*: a
    looped / recursive architecture is expanded by ModelKV via `n_loops` (see
    `_n_loops`), never here.

    MLA models are handled here too: `attention.key_length` is already the
    cached latent width (kv_lora_rank + rope), and llama.cpp allocates no V
    cache for them at all, so the V side is zeroed. See the `is_mla` block
    below.

    Returns (spec, info) or None if the metadata is insufficient.
    """
    arch = md.get("general.architecture")
    if not arch:
        return None

    def g(key):
        return md.get(f"{arch}.{key}")

    n_layer = g("block_count")
    if not n_layer:
        return None
    n_blocks = n_layer  # physical blocks, before any loop expansion
    n_loops = _n_loops(md)
    n_head = g("attention.head_count")
    n_embd = g("embedding_length")
    key_length = g("attention.key_length")
    value_length = g("attention.value_length")
    head_kv = g("attention.head_count_kv")

    if key_length is None and n_head and n_embd:
        key_length = n_embd // n_head  # default head dim
    if value_length is None:
        value_length = key_length
    if head_kv is None:
        head_kv = n_head  # MHA: kv heads == attention heads
    if key_length is None or value_length is None or head_kv is None:
        return None

    # MLA (DeepSeek-V2+, Kimi, GLM-DSA): only the compressed latent is cached.
    # `llama_hparams::is_mla()` is "both MLA head dims present and non-zero",
    # and llama-kv-cache.cpp then allocates K only (`has_v = !is_mla`). The K
    # width is the ordinary `n_embd_head_k * n_head_kv` -- for these GGUFs
    # `attention.key_length` is already kv_lora_rank + rope, and
    # `key_length_mla` / `value_length_mla` are the inner per-head dims used by
    # the attention math, never by the cache. `attention.value_length` is dead
    # weight here (Kimi-K3 ships a nonsensical 74) and must NOT be counted.
    is_mla = bool(g("attention.key_length_mla")) and bool(
        g("attention.value_length_mla")
    )
    # `_ARCH_K_ONLY_CACHE` archs get the same K-only storage without declaring
    # MLA head dims, so their `attention.value_length` must be dropped too.
    k_only = arch in _ARCH_K_ONLY_CACHE
    if is_mla or k_only:
        value_length = 0

    head_kv = _norm_per_layer(head_kv, n_layer)
    if head_kv is None:
        return None

    swa = g("attention.sliding_window")
    swa_flags = None
    if swa:
        # The GGUF pattern key overrides only the period; the phase, and the
        # period itself when the key is absent, come from the arch's loader.
        period, dense_first = _ARCH_SWA_PATTERN.get(arch, (None, False))
        pattern = g("attention.sliding_window_pattern")
        swa_flags = _swa_flags(
            period if pattern is None else pattern, n_layer, dense_first
        )
        if swa_flags is None:
            sys.stderr.write(
                f"WARNING: '{arch}' declares attention.sliding_window={swa} but "
                "carries no sliding-window pattern, and none is known for this "
                "architecture (add it to _ARCH_SWA_PATTERN). Sizing every layer "
                "at the full context -- the KV-cache figures below are an UPPER "
                "BOUND, potentially several times the real allocation.\n"
            )
        elif (g("attention.key_length_swa") or key_length) != key_length or (
            g("attention.value_length_swa") or value_length
        ) != value_length:
            # ModelKV carries one key/value head dim for both layer groups.
            sys.stderr.write(
                f"WARNING: '{arch}' gives its sliding-window layers different "
                "key/value head dims than its full-attention layers; the "
                "KV-cache figures below use the full-attention dims for both "
                "groups and are therefore wrong.\n"
            )

    # Groups are split over the *physical* blocks; the loop expansion is
    # ModelKV's job (`n_loops`). Tiling the per-layer arrays here as the arch
    # loader does would be equivalent -- tiling preserves both the per-group
    # layer ratio and the `_group_kv_heads` average -- but it would put the
    # multiplication in this script instead of in the shared model.
    cached = [il for il in range(n_layer) if head_kv[il]]
    swa_layers = [il for il in cached if swa_flags and swa_flags[il]]
    swa_set = set(swa_layers)
    full_layers = [il for il in cached if il not in swa_set]

    # Side caches allocated *in addition* to the token cache above; () for all
    # but the sparse-attention architectures.
    compressed = _compressed_groups(md, arch, head_kv, key_length, cached, n_layer)

    spec = ModelKV(
        full_attn_layers=len(full_layers),
        full_attn_kv_heads=_group_kv_heads(head_kv, full_layers),
        sliding_window_layers=len(swa_layers),
        sliding_window_kv_heads=_group_kv_heads(head_kv, swa_layers),
        sliding_window_size=swa if swa_layers else 0,
        key_dim=key_length,
        value_dim=value_length,
        n_loops=n_loops,
        compressed=compressed,
    )

    gqa = isinstance(n_head, int) and any(head_kv[il] < n_head for il in cached)
    attn_kind = "MLA" if is_mla else ("GQA" if gqa else "MHA")
    if swa_layers:
        attn_kind += "+SWA"
    if k_only and not is_mla:
        attn_kind += " (K-only)"
    # Name the side caches rather than counting them; their fixed-size compressor
    # state buffers (ratio 0) are an implementation detail of the named ones.
    side_caches = [grp.name for grp in compressed if grp.ratio]
    if side_caches:
        attn_kind += " + " + ", ".join(side_caches)
    if len(cached) < n_layer:
        # Hybrid: the remaining layers are conv / recurrent / linear-attention
        # blocks that keep a fixed-size state instead of a KV cache.
        attn_kind += f" ({n_layer - len(cached)} recurrent)"
    # Layer counts for display come back out of the spec, so they carry the loop
    # expansion without this script ever multiplying anything.
    info = {
        "attn_kind": attn_kind,
        "n_attn_layers": spec.full_attn_layers_all + spec.sliding_window_layers_all,
        "n_full_layers": spec.full_attn_layers_all,
        "n_swa_layers": spec.sliding_window_layers_all,
        "swa_window": swa if swa_layers else None,
        "n_blocks": n_blocks,
        "n_loops": n_loops,
        "source": "metadata",
        "n_head": n_head,
        "head_kv_counts": [head_kv[il] for il in cached],
        "key_length": key_length,
        "value_length": value_length,
    }
    return spec, info


def _model_kv_from_tensors(tensors, n_loops=1):
    """Fallback: derive the KV geometry from per-layer tensor shapes when the
    hparams are missing. Handles separate attn_k/attn_v tensors and MLA
    (attn_kv_a_mqa); no SWA awareness. `n_loops` > 1 scales the result for a
    looped transformer, whose tensors describe only one pass (see `_n_loops`).
    Returns (spec, info); the geometry is empty (zero bytes at any context) if
    no KV tensors are found.

    * MLA (attn_kv_a_mqa present) -- one compressed latent is cached per layer
      and llama.cpp allocates no V cache (`has_v = !is_mla`):
          k_width += dims[1] of blk.N.attn_kv_a_mqa.weight  (kv_lora_rank + rope)
      MLA is checked FIRST: a hybrid MLA model (e.g. Kimi-K3) also carries
      attn_k/attn_v, but on its linear-attention layers, which cache nothing.
    * Standard attention -- K and V are separate tensors; sum their output
      widths over all layers (no extra x2):
          k_width += dims[1] of blk.N.attn_k.weight   (n_embd_k_gqa)
          v_width += dims[1] of blk.N.attn_v.weight   (n_embd_v_gqa)
    """
    mla = [d for n, d, _ in tensors if len(d) >= 2 and _RE_MLA.match(n)]
    if mla:
        attn_kind = "MLA"
        k_width, v_width = sum(d[1] for d in mla), 0
        n_attn_layers = len(mla)
    else:
        attn_kind = "GQA/MHA"
        k_width = sum(
            d[1] for n, d, _ in tensors if len(d) >= 2 and _RE_ATTN_K.match(n)
        )
        v_width = sum(
            d[1] for n, d, _ in tensors if len(d) >= 2 and _RE_ATTN_V.match(n)
        )
        n_attn_layers = sum(
            1 for n, d, _ in tensors if len(d) >= 2 and _RE_ATTN_K.match(n)
        )

    # Tensor shapes only recover the per-model totals, not the per-layer
    # `layers x heads x dim` factorisation, so fold both into the dims of a
    # single notional one-KV-head layer: ModelKV only ever uses the product.
    # `n_loops` then scales that one notional layer, which is exactly right --
    # the tensors describe a single pass over the blocks.
    spec = ModelKV(
        full_attn_layers=1,
        full_attn_kv_heads=1,
        sliding_window_layers=0,
        sliding_window_kv_heads=0,
        sliding_window_size=0,
        key_dim=k_width,
        value_dim=v_width,
        n_loops=n_loops,
    )
    info = {
        "attn_kind": attn_kind,
        # Display only, and the one count the spec cannot supply: its layer field
        # is the notional 1 above, so `full_attn_layers_all` is just `n_loops`.
        "n_attn_layers": n_attn_layers * n_loops,
        "n_blocks": n_attn_layers,
        "n_loops": n_loops,
        "kv_width": spec.elems_per_token,
        "source": "tensors",
    }
    return spec, info


def estimate_context_vram(tensors, metadata, n_ctx=262144):
    """Educated guess of the non-weight VRAM llama.cpp needs.

    Returns (kv_by_quant, overhead_bytes, info_dict).

    kv_by_quant is a dict {label: bytes}, keyed by `_kv_label`, with one entry
    per KV_QUANTS (quant, tail) pair. The cache geometry is derived from GGUF
    hparams when available (`_model_kv_from_metadata`), which reflects fused
    QKV, per-layer GQA, distinct K/V head dims, and sliding-window attention.
    For MLA models, or when the hparams are missing, it falls back to per-layer
    tensor shapes (`_model_kv_from_tensors`). Either way the bytes are computed
    by kv_cache_common.ModelKV -- the same model the KLD report's
    "Context (MiB)" column uses -- for KV_N_PARALLEL sequences.

    Overhead is the fixed CUDA context plus the logits/output buffer
    (n_vocab * 4 bytes for one decoded token) -- neither scales with context
    length or KV quantization.
    """
    n_vocab = 0
    for name, dims, _ in tensors:
        if name == "output.weight" and len(dims) >= 2:
            n_vocab = dims[1]
            break
    if not n_vocab:
        for name, dims, _ in tensors:
            if name == "token_embd.weight" and len(dims) >= 2:
                n_vocab = dims[1]
                break

    # hparams first -- `n_embd_k_gqa(il) = key_length * head_count_kv(il)` is
    # llama.cpp's own formula, and it is the only source that knows which layers
    # of a hybrid hold a KV cache at all (head_count_kv == 0 marks the conv /
    # recurrent / linear-attention blocks). Tensor shapes are the fallback for
    # GGUFs whose hparams are missing.
    arch = metadata.get("general.architecture")
    result = _model_kv_from_metadata(metadata)
    if result is None:
        result = _model_kv_from_tensors(tensors, _n_loops(metadata))
    spec, info = result
    quant_bpw = {q: resolve_bpw(_effective_quant(q, arch)) for q, _ in KV_QUANTS}
    kv_by_quant = {
        _kv_label(q, tail): int(
            spec.get_total_kv_cache_size(
                n_ctx, quant_bpw[q], quant_bpw[q], tail, KV_N_PARALLEL
            )
        )
        for q, tail in KV_QUANTS
    }

    # Lightning-indexer key cache (MiniMax-M3 MSA, llama.cpp PR #24908).
    # Block-sparse attention stores one extra key per token per indexer layer -- a
    # single MQA head of `indexer.key_length` dims, kept in f32 *alongside* the
    # full K/V cache (`n_embd_k_idx` in llama-kv-cache.cpp). Sparsity cuts
    # attention compute (a constant top-k blocks per query), not cache size, so
    # this is purely additive. Only the `_ARCH_INDEXER_KV` architectures store
    # their indexer keys this way; DeepSeek-V4 puts them in a compressed cache of
    # their own, already counted as a `spec.compressed` group above.
    idx_layers = set()
    if arch in _ARCH_INDEXER_KV:
        for name, _, _ in tensors:
            if ".indexer." in name:
                m = _BLK.match(name)
                if m:
                    idx_layers.add(int(m.group(1)))
    idx_head = (
        metadata.get(f"{arch}.attention.indexer.key_length") if arch else 0
    ) or 0
    if idx_layers and not idx_head:  # fall back to the indexer k_norm width
        for name, dims, _ in tensors:
            if name.endswith(".indexer.k_norm.weight") and dims:
                idx_head = dims[0]
                break
    index_cache_bytes = len(idx_layers) * idx_head * 4 * n_ctx  # f32, single head

    logits_bytes = n_vocab * 4
    overhead_bytes = CUDA_CTX_OVERHEAD + logits_bytes
    info["spec"] = spec
    info["n_ctx"] = n_ctx
    info["n_vocab"] = n_vocab
    info["logits_bytes"] = logits_bytes
    info["index_cache_bytes"] = index_cache_bytes
    info["n_idx_layers"] = len(idx_layers)
    info["idx_head"] = idx_head
    info["kv_by_quant"] = kv_by_quant
    info["arch"] = arch
    info["quant_bpw"] = quant_bpw
    return kv_by_quant, overhead_bytes, info


def _kv_tail_caveats(spec, n_ctx):
    """Notes for the `tN` column of the headline table, where the exact tail this
    script pairs each quant with reaches less of the cache than the label implies.

    Two things are worth saying out loud, both read off the spec:

    * A side cache (`spec.compressed`) is constructed with `tail_tokens = 0` --
      llama.cpp passes it no tail arguments at all -- so no exact tail ever
      protects it, however large `--kv-tail-tokens` is. On DeepSeek-V4 that is
      >99% of the cache.
    * `llama-context.cpp` clamps the sliding-window tail to the window itself
      (`kv_tail_tokens_swa = min(N, n_swa)`). A model with full-attention layers
      still gets the unclamped tail on those, but one whose *every* layer is
      sliding-window (again DeepSeek-V4) gets the same allocation for every tail
      at or above its window.
    """
    tails = sorted({t for _, t in KV_QUANTS if t})
    if not tails:
        return []
    lines = []
    side = [grp.name for grp in spec.compressed if grp.ratio]
    if side:
        lines.append(
            f"  note: the exact tail never reaches {', '.join(side)} -- llama.cpp "
            "builds those with tail 0,\n"
            "        so the tN column below protects only the token cache."
        )
    window = min(n_ctx, spec.sliding_window_size) if spec.sliding_window_layers else 0
    if window and not spec.full_attn_layers and min(tails) >= window:
        tail_list = "/".join(f"t{t}" for t in tails)
        lines.append(
            f"  note: every layer is sliding-window, and the tail is clamped to the "
            f"window ({window} tok),\n"
            f"        so {tail_list} are all the same allocation here."
        )
    return lines


def _kv_cache_breakdown(info, kv_by_quant):
    """Human-readable, line-by-line derivation of the KV-cache figure from
    `info` (set by `_model_kv_from_metadata` / `_model_kv_from_tensors`) and the
    `kv_by_quant` dict. Returned as a list of indented lines for stderr.
    Shows the elems (quant-independent) derivation once, then a per-quant table
    of bytes for the types in KV_QUANTS, split per layer group when the model
    has more than one."""
    spec = info["spec"]
    n_ctx = info["n_ctx"]
    # Straight from the spec, so it carries the loop expansion and cannot drift
    # from the byte figures below.
    total_width = spec.elems_per_token
    full_ctx_elems = total_width * n_ctx  # every layer at full n_ctx (no SWA)
    lines = ["    KV cache size derivation:"]
    gib = 1 << 30
    gb = 10**9
    if info.get("source") == "metadata":
        n_layer = info["n_attn_layers"]
        head_kv = info["head_kv_counts"]
        klen = info["key_length"]
        vlen = info["value_length"]
        uniform = len(set(head_kv)) == 1
        if info.get("n_loops", 1) > 1:
            lines.append(
                f"      looped transformer: {info['n_blocks']} blocks x "
                f"{info['n_loops']} loops = {info['n_blocks'] * info['n_loops']} "
                "cache layers"
            )
        if uniform and not info.get("n_swa_layers"):
            per_layer = head_kv[0] * (klen + vlen)
            lines.append(
                f"      {n_layer} layers x {head_kv[0]} kv-heads x "
                f"({klen} key + {vlen} value) dims/layer x {n_ctx} tok"
            )
            lines.append(
                f"      = {n_layer} x {per_layer} elems/layer = "
                f"{total_width} elems/token x {n_ctx} tok "
                f"= {full_ctx_elems} elems"
            )
        else:
            head_kv_desc = f"{head_kv[0]}" if uniform else f"{head_kv} (per-layer)"
            lines.append(
                f"      {n_layer} layers; head_count_kv={head_kv_desc}, "
                f"key_length={klen}, value_length={vlen}, {n_ctx} tok"
            )
            lines.append(
                f"      = sum_layers(head_kv * ({klen}+{vlen})) * n_ctx = "
                f"{total_width} elems/token x {n_ctx} tok = "
                f"{full_ctx_elems} elems (no-SWA baseline)"
            )
            if info.get("n_swa_layers"):
                window = min(n_ctx, spec.sliding_window_size)
                swa_width = (
                    spec.sliding_window_layers_all
                    * spec.sliding_window_kv_heads
                    * (spec.key_dim + spec.value_dim)
                )
                saved = int(swa_width * (n_ctx - window))
                lines.append(
                    f"      SWA: {info['n_swa_layers']} layers cache "
                    f"{info['swa_window']} tok not {n_ctx} tok -- saves "
                    f"{saved} elems -> actual {full_ctx_elems - saved} elems"
                )
        for grp in spec.compressed:
            # Side caches, which for DeepSeek-V4 hold the history the 128-token
            # window above does not. The row derivation comes out of ModelKV so
            # it cannot drift from the bytes below.
            lines.append(
                f"      + {grp.name}: {grp.layers * spec.n_loops} layers x"
                f"{grp.kv_heads:g} kv-heads x ({grp.key_dim} key + "
                f"{grp.value_dim} value) dims, "
                + spec.compressed_note(grp, n_ctx, KV_N_PARALLEL)
            )
    else:  # tensor-shape path (hparams missing)
        kv_width = info["kv_width"]
        n_layer = info["n_attn_layers"]
        if "MLA" in info["attn_kind"]:
            width_desc = (
                "sum over layers of compressed-latent width "
                "(kv_lora_rank + rope, from attn_kv_a_mqa)"
            )
        else:
            width_desc = "sum over layers of (attn_k.width + attn_v.width)"
        lines.append(
            f"      {width_desc} = {kv_width} elems/token "
            f"(across {n_layer} layers) x {n_ctx} tok"
        )
        lines.append(f"      = {kv_width} x {n_ctx} = {full_ctx_elems} elems")

    lines.append(
        f"      KV cache size by quantization and exact tail, n_parallel="
        f"{KV_N_PARALLEL} (bpw from kv_cache_common.BPW, bytes from ModelKV):"
    )
    arch = info.get("arch")
    for qname, tail in KV_QUANTS:
        # `_effective_quant` differs from `qname` only where the architecture
        # cannot use KVarN and the CLI's plain fallback type is stored instead.
        effective = _effective_quant(qname, arch)
        bpw = info["quant_bpw"][qname]
        label = _kv_label(qname, tail)
        nbytes = kv_by_quant[label]
        subst = f", {qname} unsupported -> {effective}" if effective != qname else ""
        lines.append(
            f"        {label:{_KV_LABEL_W}s}  ({bpw:7.4f} bpw, {bpw / 8:6.4f} B/elem"
            f"{subst}): {nbytes:>14,d} B = {nbytes / gib:7.2f} GiB  "
            f"{nbytes / gb:7.2f} GB"
        )
        groups = spec.cache_breakdown(n_ctx, bpw, bpw, tail, KV_N_PARALLEL)
        if len(groups) < 2 and not tail:  # nothing the headline doesn't say
            continue
        for grp in groups:
            if info.get("source") == "tensors":
                # The tensor path recovers no per-layer factorisation (see
                # _model_kv_from_tensors), so its notional 1 layer x 1 head
                # would only mislead -- report the real totals instead.
                geom = f"{info['n_attn_layers']} layers, {total_width} elems/token"
            else:
                geom = (
                    f"{grp.layers} layers x{grp.kv_heads:g} kv-heads x"
                    f"{grp.key_dim}/{grp.value_dim} dim"
                )
            lines.append(
                f"          {grp.name:15s} {geom}; {grp.note} "
                f"-> {grp.nbytes / gib:7.2f} GiB"
            )
    return lines


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description="Dump GGUF tensor info to CSV via header-only downloads."
    )
    ap.add_argument(
        "url",
        help="HF repo/directory URL (e.g. .../GLM-5.2-GGUF/tree/main/UD-IQ1_S) "
        "or a single .gguf blob URL (e.g. .../repo/blob/main/model.gguf)",
    )
    ap.add_argument("-o", "--output", default="gguf_tensors.csv")
    ap.add_argument(
        "--token", default=None, help="HF token (else $HF_TOKEN / $HUGGINGFACE_TOKEN)"
    )
    ap.add_argument(
        "--start-bytes",
        type=int,
        default=8 << 20,
        help="initial prefix size (default 8 MiB)",
    )
    ap.add_argument(
        "--max-bytes",
        type=int,
        default=512 << 20,
        help="per-file download cap (default 512 MiB)",
    )
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    import os

    token = (
        args.token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    )
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    repo_id, revision, subdir = parse_hf_url(args.url)
    if not args.quiet:
        sys.stderr.write(f"repo={repo_id} rev={revision} subdir={subdir or '(root)'}\n")

    api = HfApi(token=token)
    all_files = api.list_repo_files(repo_id, revision=revision)
    if any(c in subdir for c in "*?["):
        # Glob pattern in the filename part of a blob/resolve URL.
        # Expand against the repo file list using Python fnmatch.
        ggufs = sorted(fnmatch.filter(all_files, subdir))
        if not ggufs:
            sys.exit(f"No files matched glob pattern: '{subdir}'")
    elif subdir.endswith(".gguf"):
        # URL points directly at a single .gguf file (a blob/resolve URL).
        # Needed when several variant single-file GGUFs share one folder and we
        # want just one, not the whole directory.
        if subdir not in all_files:
            sys.exit(f"File not found in repo: '{subdir}'")
        ggufs = [subdir]
    else:
        pfx = (subdir.rstrip("/") + "/") if subdir else ""
        ggufs = sorted(
            f for f in all_files if f.endswith(".gguf") and f.startswith(pfx)
        )
        if not ggufs:
            sys.exit(f"No .gguf files under '{pfx or repo_id}'")
    if not args.quiet:
        sys.stderr.write(f"found {len(ggufs)} GGUF file(s)\n")

    rows = []
    all_tensors = []
    metadata = {}
    for f in ggufs:
        if not args.quiet:
            sys.stderr.write(f"  {f}\n")
        url = hf_hub_url(repo_id, f, revision=revision)
        md, tensors = read_gguf_tensors(
            url, headers, start=args.start_bytes, cap=args.max_bytes, quiet=args.quiet
        )
        # Split 0 carries the full hparams; later splits repeat only a subset.
        # First file wins so we keep the complete set.
        for k, v in md.items():
            metadata.setdefault(k, v)
        for name, dims, ttype in tensors:
            rows.append(build_row(name, dims, ttype))
            all_tensors.append((name, dims, ttype))

    # Stable ordering: by layer number (blanks last), then tensor name.
    def sort_key(r):
        return (int(r[0]) if r[0] != "" else 1 << 30, r[1])

    rows.sort(key=sort_key)

    with open(args.output, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "layer",
                "tensor_name",
                "geometry",
                "n_points",
                "quant",
                "bytes_per_point",
                "total_bytes",
            ]
        )
        w.writerows(rows)

    # Bonus: dense-vs-expert byte summary to stderr (does not touch the CSV).
    dense = exps = 0
    for r in rows:
        if isinstance(r[6], int):  # r[1] is tensor_name (blk.N. stripped)
            if "_exps." in r[1]:
                exps += r[6]
            else:
                dense += r[6]
    gib = 1 << 30  # binary gibibyte (2^30); HF web UI reports decimal GB (10^9)
    gb = 10**9

    # Size of the routed experts actually activated per token: top-k of the
    # routed experts fire (expert_used_count of expert_count), so only that
    # fraction of each *_exps tensor is touched. This is NOT "active parameters"
    # (the usual marketing figure), which also counts dense/shared/attn weights.
    arch = metadata.get("general.architecture")
    used = metadata.get(f"{arch}.expert_used_count") if arch else None
    n_experts = metadata.get(f"{arch}.expert_count") if arch else None
    activated_exps = 0
    if used is not None:
        for name, dims, ttype in all_tensors:
            if "_exps." not in name or not dims:
                continue
            nb = tensor_nbytes(dims, ttype)
            n_expert = dims[-1]  # routed experts are the last (slowest) axis
            if not nb or not n_expert:
                continue
            u = used
            if isinstance(used, list):  # per-layer top-k
                m = _BLK.match(name)
                if not m or int(m.group(1)) >= len(used):
                    continue
                u = used[int(m.group(1))]
            activated_exps += nb // n_expert * u

    summary = (
        f"\nwrote {len(rows)} rows -> {args.output}\n"
        f"  routed-expert tensors (*_exps.*): {exps / gib:8.2f} GiB  {exps / gb:8.2f} GB\n"
    )
    if activated_exps:
        k_desc = (
            f"top-{used} of {n_experts}"
            if not isinstance(used, list) and n_experts
            else "per-layer top-k"
        )
        summary += (
            f"  of which activated per token    : {activated_exps / gib:8.2f} GiB  "
            f"{activated_exps / gb:8.2f} GB  ({k_desc} routed experts/token; "
            "excludes dense/shared/attn weights)\n"
        )
    summary += (
        f"  everything else (dense)         : {dense / gib:8.2f} GiB  {dense / gb:8.2f} GB\n"
        f"  total (model weights)           : {(dense + exps) / gib:8.2f} GiB  "
        f"{(dense + exps) / gb:8.2f} GB\n"
    )
    sys.stderr.write(summary)

    # Educated guess of non-weight VRAM (KV cache + fixed scratch/overhead).
    kv_by_quant, overhead_bytes, info = estimate_context_vram(all_tensors, metadata)
    if all(v == 0 for v in kv_by_quant.values()):
        sys.stderr.write(
            "\ncontext/scratch VRAM estimate: SKIPPED -- could not derive KV "
            "width from metadata or tensor shapes (no head_count_kv / "
            "attn_k/attn_v / attn_kv_a_mqa)\n"
        )
    else:
        n_ctx = info["n_ctx"]
        layer_desc = f"{info['n_attn_layers']} attn layers"
        if info.get("n_loops", 1) > 1:
            layer_desc += f" ({info['n_blocks']} blocks x {info['n_loops']} loops)"
        if info.get("n_swa_layers"):
            layer_desc += (
                f": {info['n_full_layers']} full @ {n_ctx // 1024}k tok, "
                f"{info['n_swa_layers']} SWA @ {info['swa_window']} tok"
            )
        elif info.get("kv_width"):
            layer_desc += f", {info['kv_width']} elems/token"
        sys.stderr.write(
            f"\ncontext/scratch VRAM estimate (rough, KV cache by quant type):\n"
            f"  attn layout: {info['attn_kind']}, {layer_desc}\n"
            f"  KV cache @ {n_ctx // 1024}k tokens:\n"
        )
        # Per-quant headline, padded so the GiB/GB columns line up. `tN` is the
        # beellama --kv-tail-tokens the quant is paired with (see KV_QUANTS). A
        # `kvarnN -> qN_0` label means this architecture cannot use KVarN and the
        # figure is the fallback type it stores instead; the substitution has to
        # be visible HERE, not only in the derivation block below, or the headline
        # silently attributes KVarN sizes to a model that never gets them.
        arch = info.get("arch")
        labels = {(q, t): _kv_display_label(q, t, arch) for q, t in KV_QUANTS}
        width = max(len(lbl) for lbl in labels.values())
        if any(_effective_quant(q, arch) != q for q, _ in KV_QUANTS):
            sys.stderr.write(
                f"  note: '{arch}' builds its own KV cache and is never handed the "
                "KVarN params (llama-model.cpp\n"
                "        branches before the KVarN test), so -ctk kvarnN silently "
                "stores the plain qN_0\n"
                "        fallback it is paired with -- the kvarn rows below are "
                "sized at that fallback.\n"
            )
        for line in _kv_tail_caveats(info["spec"], n_ctx):
            sys.stderr.write(line + "\n")
        for qname, tail in KV_QUANTS:
            nbytes = kv_by_quant[_kv_label(qname, tail)]
            sys.stderr.write(
                f"    {labels[qname, tail]:{width}s}  {nbytes / gib:7.2f} GiB  "
                f"{nbytes / gb:7.2f} GB\n"
            )
        sys.stderr.write(
            f"  fixed overhead (CUDA ctx {CUDA_CTX_OVERHEAD / gib:.2f} GiB + "
            f"logits {info['logits_bytes'] / gib:.2f} GiB, n_vocab="
            f"{info['n_vocab']}): {overhead_bytes / gib:8.2f} GiB  "
            f"{overhead_bytes / gb:8.2f} GB\n"
            "  (overhead is independent of context length and KV quant; the KV "
            "cache scales linearly with both)\n"
        )
        sys.stderr.write("\n".join(_kv_cache_breakdown(info, kv_by_quant)) + "\n")

    # Lightning-indexer key cache (MSA/DSA) -- additive to the KV cache above.
    if info.get("index_cache_bytes"):
        ic = info["index_cache_bytes"]
        sys.stderr.write(
            f"  + lightning-indexer key cache (MSA/DSA sparse attn, "
            f"{info['n_idx_layers']} indexer layers x {info['idx_head']} dims, "
            f"f32): {ic / gib:8.2f} GiB  {ic / gb:8.2f} GB\n"
            "  (additive to the KV cache above -- sparse attention cuts compute, "
            "not cache size; only present when the GGUF carries indexer tensors)\n"
        )

    # Expert-offload (--cpu-moe / --n-cpu-moe) prefill staging scratch. During
    # batched prefill the routed-expert weights (kept in host RAM) are copied to
    # VRAM and the matmul runs on the GPU. llama.cpp allocates the *full* expert
    # tensor as the copy destination (only the activated experts' rows are
    # actually copied) and reuses that region across a layer's gate/up/down
    # matmuls and across layers -- so the peak is the single largest routed-
    # expert tensor. Offload only kicks in at batch >= 32 tokens, so this is a
    # prefill-only term (decode keeps experts on the CPU and adds nothing here).
    expert_scratch = max(
        (tensor_nbytes(d, t) or 0 for n, d, t in all_tensors if "_exps." in n),
        default=0,
    )
    if expert_scratch:
        sys.stderr.write(
            f"\nexpert-offload prefill scratch (--cpu-moe / --n-cpu-moe), "
            f"largest routed-expert tensor staged to VRAM: "
            f"{expert_scratch / gib:8.2f} GiB  {expert_scratch / gb:8.2f} GB\n"
            "  (prefill only, batch >= 32 tokens; the full expert tensor is "
            "allocated but only activated experts are copied; the region is "
            "reused across a layer's matmuls and across layers, so this is the "
            "single largest *_exps tensor, not their sum)\n"
        )


if __name__ == "__main__":
    main()
