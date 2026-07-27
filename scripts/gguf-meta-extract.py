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

try:
    from huggingface_hub import HfApi, hf_hub_url
except ImportError:
    sys.exit("Need huggingface_hub:  pip install huggingface_hub")

# Optional: only used to give a human-readable name to an UNKNOWN ggml type id.
try:
    from gguf import GGMLQuantizationType as _GGUFType
except Exception:
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
            except Exception:
                pass
        sys.stderr.write(
            f"WARNING: unknown ggml type id {ttype} ({nm}); "
            f"sizes left blank for these tensors\n"
        )
    name = "?"
    if _GGUFType:
        try:
            name = _GGUFType(ttype).name
        except Exception:
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

    qname, block, tsize, row_meta = quant_info(ttype)
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


def _swa_flags(pattern, n_layer):
    """Per-layer SWA bool list from `attention.sliding_window_pattern`.

    * Array form (e.g. mimo2): one bool per layer, True => sliding-window.
    * Scalar form `n` (llama.cpp set_swa_pattern, dense_first=False): layer il
      is SWA iff `il % n < n-1` -- one full-attention layer at the end of each
      group of n.

    Returns a list[bool] of length n_layer, or None if there is no pattern.
    """
    if pattern is None:
        return None
    if isinstance(pattern, list):
        return [bool(x) for x in pattern[:n_layer]] if len(pattern) >= n_layer else None
    n = int(pattern)
    if n <= 0:
        return [True] * n_layer
    return [(il % n) < (n - 1) for il in range(n_layer)]


# Bytes per element for each KV-cache quantization we report on. Source:
#   f16    -- 16-bit IEEE float, no block structure  -> 2.0 B/elem
#   q8_0   -- llama.cpp q8_0 block: 32 int8 + fp16 scale = 34 B / 32 elem
#   kvarnN -- BeeLlama.cpp KVarN n-bit round-to-nearest cache quant
#             (Huawei KVarN paper, n = bits/value, block-scale overhead
#             small enough to ignore at order-of-magnitude level)
_KV_QUANT_TABLE = (
    ("f16", 2.0, "16-bit float (no block)"),
    ("q8_0", 34 / 32, "8-bit, q8_0 block (32 + fp16 scale)"),
    ("kvarn4", 0.5, "4-bit KVarN"),
    ("kvarn3", 0.375, "3-bit KVarN"),
)


def _kv_from_metadata(md, n_ctx):
    """KV-cache width derived from GGUF hparams. Handles fused QKV (no separate
    attn_k/attn_v tensors), per-layer GQA (`head_count_kv` as an array),
    distinct key/value head dims, and sliding-window attention.

    Returns (total_elems, info) or None if the metadata is insufficient. Not
    used for MLA models (the caller checks first). total_elems already accounts
    for per-layer SWA capping; full_ctx_elems (in info) is the no-SWA counter-
    part (every layer at full n_ctx) used for SWA-savings reporting and for
    callers that want to recompute kv_bytes at a different bytes-per-elem.
    """
    arch = md.get("general.architecture")
    if not arch:
        return None

    def g(key):
        return md.get(f"{arch}.{key}")

    n_layer = g("block_count")
    if not n_layer:
        return None
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

    head_kv = _norm_per_layer(head_kv, n_layer)
    if head_kv is None:
        return None

    swa = g("attention.sliding_window")
    swa_flags = (
        _swa_flags(g("attention.sliding_window_pattern"), n_layer) if swa else None
    )

    total_elems = 0
    full_ctx_elems = 0
    n_swa = n_full = 0
    eff_ctx_per_layer = []
    for il in range(n_layer):
        width = head_kv[il] * (key_length + value_length)  # K + V elems/token
        if swa_flags and swa_flags[il]:
            eff_ctx = min(n_ctx, swa)
            n_swa += 1
        else:
            eff_ctx = n_ctx
            n_full += 1
        eff_ctx_per_layer.append(eff_ctx)
        total_elems += width * eff_ctx
        full_ctx_elems += width * n_ctx

    attn_kind = "GQA" if (n_head and any(h < n_head for h in head_kv)) else "MHA"
    if swa_flags:
        attn_kind += "+SWA"
    total_width = sum(
        head_kv[il] * (key_length + value_length) for il in range(n_layer)
    )
    info = {
        "attn_kind": attn_kind,
        "n_attn_layers": n_layer,
        "n_full_layers": n_full,
        "n_swa_layers": n_swa,
        "swa_window": swa if swa_flags else None,
        "source": "metadata",
        "n_head": n_head,
        "head_kv_counts": head_kv,
        "key_length": key_length,
        "value_length": value_length,
        "total_width": total_width,  # elems/token summed over layers (K+V)
        "full_ctx_elems": full_ctx_elems,  # same shape, no SWA capping
        "eff_ctx_per_layer": eff_ctx_per_layer,
    }
    return total_elems, info


def _kv_from_tensors(tensors, n_ctx):
    """Fallback: derive KV width from per-layer tensor shapes when metadata is
    missing. Handles separate attn_k/attn_v tensors and MLA (attn_kv_a_mqa);
    no SWA awareness. Returns (total_elems, info); total_elems is 0 if no KV
    tensors are found.

    * Standard attention -- K and V are separate tensors; sum their output
      widths over all layers (no extra x2):
          width += dims[1] of blk.N.attn_k.weight   (n_embd_k_gqa)
          width += dims[1] of blk.N.attn_v.weight   (n_embd_v_gqa)
    * MLA (no attn_k/attn_v) -- a single compressed latent is cached per layer:
          width += dims[1] of blk.N.attn_kv_a_mqa.weight  (kv_lora_rank + rope)
    """
    k_width = sum(d[1] for n, d, _ in tensors if len(d) >= 2 and _RE_ATTN_K.match(n))
    v_width = sum(d[1] for n, d, _ in tensors if len(d) >= 2 and _RE_ATTN_V.match(n))
    n_attn_layers = sum(1 for n, d, _ in tensors if len(d) >= 2 and _RE_ATTN_K.match(n))

    if k_width or v_width:
        attn_kind = "GQA/MHA"
        kv_width = k_width + v_width
    else:
        attn_kind = "MLA"
        kv_width = sum(d[1] for n, d, _ in tensors if len(d) >= 2 and _RE_MLA.match(n))
        n_attn_layers = sum(
            1 for n, d, _ in tensors if len(d) >= 2 and _RE_MLA.match(n)
        )

    total_elems = n_ctx * kv_width
    info = {
        "attn_kind": attn_kind,
        "n_attn_layers": n_attn_layers,
        "kv_width": kv_width,
        "source": "tensors",
        "total_width": kv_width,
        "full_ctx_elems": total_elems,  # no SWA awareness in this path
    }
    return total_elems, info


def estimate_context_vram(tensors, metadata, n_ctx=262144):
    """Educated guess of the non-weight VRAM llama.cpp needs.

    Returns (kv_by_quant, overhead_bytes, info_dict).

    kv_by_quant is a dict {qname: bytes} with one entry per row of
    _KV_QUANT_TABLE. The KV-cache width is derived from GGUF hparams when
    available (`_kv_from_metadata`), which reflects fused QKV, per-layer GQA,
    distinct K/V head dims, and sliding-window attention. For MLA models, or
    when the hparams are missing, it falls back to per-layer tensor shapes
    (`_kv_from_tensors`). The total elements are independent of the quant
    type; bytes = total_elems * bytes_per_elem for the chosen quant.

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

    # MLA caches a compressed latent, not n_head_kv*(k+v) -- the hparams-based
    # estimate would be wrong for it, so route MLA models to the tensor path.
    is_mla = any(_RE_MLA.match(n) for n, _, _ in tensors)
    result = None
    if not is_mla:
        result = _kv_from_metadata(metadata, n_ctx)
    if result is None:
        result = _kv_from_tensors(tensors, n_ctx)
    total_elems, info = result
    kv_by_quant = {qname: int(total_elems * bpe) for qname, bpe, _ in _KV_QUANT_TABLE}

    # Lightning-indexer key cache (DeepSeek-V3.2 DSA / MiniMax-M3 MSA,
    # llama.cpp PR #24908). Block-sparse attention stores one extra key per
    # token per indexer layer -- a single MQA head of `indexer.key_length`
    # dims, kept in f32 *alongside* the full K/V cache. Sparsity cuts attention
    # compute (a constant top-k blocks per query), not cache size, so this is
    # purely additive. Present only when the GGUF carries indexer tensors;
    # dense conversions omit them and this term stays zero.
    arch = metadata.get("general.architecture")
    idx_layers = set()
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
    info["n_ctx"] = n_ctx
    info["n_vocab"] = n_vocab
    info["logits_bytes"] = logits_bytes
    info["index_cache_bytes"] = index_cache_bytes
    info["n_idx_layers"] = len(idx_layers)
    info["idx_head"] = idx_head
    info["kv_by_quant"] = kv_by_quant
    return kv_by_quant, overhead_bytes, info


def _kv_cache_breakdown(info, kv_by_quant):
    """Human-readable, line-by-line derivation of the KV-cache figure from
    `info` (set by `_kv_from_metadata` / `_kv_from_tensors`) and the
    `kv_by_quant` dict. Returned as a list of indented lines for stderr.
    Shows the elems (quant-independent) derivation once, then a per-quant
    table of bytes for the types in _KV_QUANT_TABLE."""
    n_ctx = info["n_ctx"]
    full_ctx_elems = info.get("full_ctx_elems")
    lines = ["    KV cache size derivation:"]
    gib = 1 << 30
    gb = 10**9
    if info.get("source") == "metadata":
        n_layer = info["n_attn_layers"]
        head_kv = info["head_kv_counts"]
        klen = info["key_length"]
        vlen = info["value_length"]
        total_width = info["total_width"]
        uniform = len(set(head_kv)) == 1
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
                actual_elems = sum(
                    head_kv[il] * (klen + vlen) * info["eff_ctx_per_layer"][il]
                    for il in range(n_layer)
                )
                lines.append(
                    f"      SWA: {info['n_swa_layers']} layers cache "
                    f"{info['swa_window']} tok not {n_ctx} tok -- saves "
                    f"{full_ctx_elems - actual_elems} elems -> actual "
                    f"{actual_elems} elems"
                )
        lines.append("      KV cache size by quantization (B = elems * B/elem):")
        for qname, bpe, desc in _KV_QUANT_TABLE:
            nbytes = kv_by_quant[qname]
            lines.append(
                f"        {qname:8s}  ({bpe:5.4f} B/elem, {desc}): "
                f"{nbytes:>14,d} B = {nbytes / gib:7.2f} GiB  "
                f"{nbytes / gb:7.2f} GB"
            )
    else:  # tensor-shape path (hparams missing)
        kv_width = info["kv_width"]
        n_layer = info["n_attn_layers"]
        kind = info["attn_kind"]
        if "MLA" in kind:
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
        lines.append("      KV cache size by quantization (B = elems * B/elem):")
        for qname, bpe, desc in _KV_QUANT_TABLE:
            nbytes = kv_by_quant[qname]
            lines.append(
                f"        {qname:8s}  ({bpe:5.4f} B/elem, {desc}): "
                f"{nbytes:>14,d} B = {nbytes / gib:7.2f} GiB  "
                f"{nbytes / gb:7.2f} GB"
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
        # Per-quant headline, padded so the GiB/GB columns line up.
        for qname, bpe, _ in _KV_QUANT_TABLE:
            nbytes = kv_by_quant[qname]
            sys.stderr.write(
                f"    {qname:8s}  {nbytes / gib:7.2f} GiB  {nbytes / gb:7.2f} GB\n"
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
