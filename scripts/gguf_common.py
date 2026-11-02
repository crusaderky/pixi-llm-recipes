"""Shared GGUF header primitives.

Imported by ``gguf-meta-extract.py`` (which fetches headers over HTTP Range) and
by ``perplexity.py`` (which reads them off the local Hugging Face cache while
recording a run's model provenance). Both need the same two things -- the ggml
type table and a header-only parser -- and the lazy-read rules below have to
agree with the report that reads the provenance back.

**Stdlib only.** ``perplexity.py`` runs in the ``llamacpp-*`` environments and
``perplexity-report.py`` / ``gguf-meta-extract.py`` in ``pytools``; the two share
no third-party dependency, exactly as for ``perplexity_common.py``. The optional
``gguf`` import below is guarded and only ever supplies a prettier name for an
unknown type id.

Why we parse the header ourselves instead of ``gguf.GGUFReader``: the reader
eagerly materialises a numpy view of every tensor's *data* at construction time,
so it raises on a header-only / truncated file and has no header-only mode. The
GGUF header is fully self-describing (name + shape + dtype for every tensor live
before the data section), so a tiny direct parser reads everything we need from
the first few MB. Validated to produce byte-identical name/shape/dtype output to
GGUFReader on complete files.

The **sizes** come from the header's offset column, not from ``GGML_SIZES``
(``GgufHeader.sizes``). A ggml type id is only an integer: a quantization fork
can re-number its enum and the id then means a different format than this table
says, which a byte total derived from the table would silently report as that
other format's size. The offsets are what the writer actually stored, so they are
authoritative, and the type table survives as the cross-check that names the
collision (``GgufHeader.renumbered_types``).
"""

import math
import os
import re
import struct
import sys
from dataclasses import dataclass

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
    42: ("Q2_0", 64, 18),  # mainline group-64, 2.25 bpw
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
    # --- PrismML ternary (PrismML-Eng/llama.cpp, branch `prism`) ---
    # Ternary {−1,0,+1} weights, one fp16 scale per 128 weights.
    # PQ2_0: 2-bit trit slots (2.125 bpw); PTQ1_0: dense base-3 trit packing
    # (1.75 bpw). ggml-common.h block_pq2_0 / block_ptq1_0, both group 128.
    142: ("PQ2_0", 128, 34),
    143: ("PTQ1_0", 128, 28),
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
            f"WARNING: unknown ggml type id {ttype} ({nm}); no block size is known, "
            "so only a tensor whose offsets could not be read is left without a size\n"
        )
    name = "?"
    if _GGUFType:
        try:
            name = _GGUFType(ttype).name
        except ValueError:
            pass
    return (f"UNKNOWN_{ttype}_{name}", None, None, 0)


# --------------------------------------------------------------------------- #
# Sizes: the offsets are authoritative, the type table is the cross-check
# --------------------------------------------------------------------------- #
# GGUF aligns every tensor's offset up to ``{general.alignment}`` (32 by default)
# and counts offsets from the start of the data section -- the first aligned byte
# after the tensor-info table. So the gap between two consecutive offsets is the
# earlier tensor's payload plus at most ``alignment - 1`` bytes of padding, and
# the gap past the last tensor is its payload plus the header-to-data padding.
ALIGN_DEFAULT = 32


@dataclass(frozen=True)
class TensorSize:
    """One tensor, its size settled against both sources."""

    name: str
    dims: list
    ttype: int
    offset: int
    nbytes: int | None
    """On-disk payload bytes -- the number every byte total here is built from."""
    table_bytes: int | None
    """What ``tensor_nbytes`` derives from ``GGML_SIZES``; None for an unknown id."""
    source: str
    """``table``: the two agree within the alignment padding, so ``nbytes`` is the
    exact payload a loader allocates. ``offsets``: they disagree, or the id is
    unknown, so ``nbytes`` is the gap between the offsets (and carries up to
    ``alignment - 1`` bytes of padding -- at most tens of KB across a whole file,
    against GiB-scale totals). ``table-unverified``: no usable offset, e.g. the
    last tensor of a file whose total size the server never reported."""


@dataclass(frozen=True)
class RenumberedType:
    """One ggml type id whose bytes-per-point in this file contradicts the table."""

    ttype: int
    table_name: str
    tensors: int
    points: int
    table_bytes: int
    offset_bytes: int

    @property
    def table_bpw(self) -> float:
        return self.table_bytes * 8 / self.points

    @property
    def offset_bpw(self) -> float:
        return self.offset_bytes * 8 / self.points


# --------------------------------------------------------------------------- #
# Minimal GGUF header parser (header-only safe). Raises Truncated if the buffer
# ends before the tensor-info table is fully read, signalling "fetch more".
# --------------------------------------------------------------------------- #
class Truncated(Exception):
    pass


class _Cur:
    def __init__(self, b):
        self.b = b
        self.i = 0

    def take(self, n):
        if self.i + n > len(self.b):
            raise Truncated()
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
    """Parse a GGUF header from bytes. Returns a :class:`GgufHeader` (defined at
    the bottom of this module) whose ``tensors`` is a list of
    ``(name, dims, ttype, offset)`` and whose ``file_size`` is None -- the size of
    the whole file is the caller's to supply, and only the *last* tensor needs it.
    Raises Truncated if more bytes are needed."""
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
        offset = c.u64()
        out.append((name, dims, ttype, offset))
    # A writer that emits the table in offset order is what makes consecutive
    # offsets differ by a tensor's size. Anything else and the gaps are junk, so
    # `GgufHeader.sizes` stays on the type table and says it did not verify.
    ordered = all(out[i][3] < out[i + 1][3] for i in range(len(out) - 1))
    return GgufHeader(
        metadata=metadata, tensors=out, header_len=c.i, offsets_ordered=ordered
    )


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


# --------------------------------------------------------------------------- #
# Local header reads
# --------------------------------------------------------------------------- #
# A vocab-sized tokenizer block puts the tensor table several MB in, and the
# n-gram PLE archs push it past 8 MB, so start where a normal model's header
# already fits and grow only when the parse comes up short.
HEADER_START = 8 << 20
HEADER_CAP = 512 << 20


def read_header(path, start=HEADER_START, growth=4, cap=HEADER_CAP):
    """Parse the GGUF header of a local file. Returns (metadata, tensors).

    Reads increasing prefixes rather than the whole file: a shard is tens of GiB
    and only its first few MB are the header. Raises ValueError if the header
    does not fit within *cap*, or if the file is not a GGUF.
    """
    n = start
    with open(path, "rb") as fh:
        file_size = os.fstat(fh.fileno()).st_size
        while True:
            fh.seek(0)
            buf = fh.read(n)
            try:
                hdr = parse_header(buf)
                hdr.file_size = file_size
                return hdr
            except Truncated:
                if len(buf) < n:  # got the whole file, still short
                    raise ValueError("file ended before header finished parsing")
                if n >= cap:
                    raise ValueError(f"header exceeds {cap} byte cap")
                n = min(n * growth, cap)


# --------------------------------------------------------------------------- #
# Tensors llama.cpp reads from disk on demand instead of keeping resident
# --------------------------------------------------------------------------- #
# Tensor names an architecture marks `TENSOR_READ_LAZY` in its `create_tensor`
# call, per arch -> tuple of names (`src/models/<arch>.cpp`). All three entries
# are the same tensor name: an n-gram / per-layer hash-embedding table, gathered
# a handful of rows at a time, so llama.cpp registers its byte range in
# `lazy_tensor_ranges` and reads those rows out of the mmap when a token needs
# them (llama-model-loader.cpp). It is never copied into the resident weights and
# therefore never into VRAM -- which matters here because on Qwen3.8-Flash-Next
# it is a *quarter* of the file (26.8 GiB of 103.7 GiB at UD-Q4_K_XL), and on
# DeepSeek-V4.1-Flash two fifths (125.9 GiB of 311.8 GiB at UD-Q2_K_XL).
#
# The flag is a property of the arch, not of the GGUF, so it cannot be derived
# from a header -- hence this table. Keep it in step with llama.cpp:
#     git grep -l TENSOR_READ_LAZY src/models/
ARCH_LAZY_TENSORS = {
    # `{n_embd_per_layer * n_layer, n_vocab}` -- large only on the bigger Gemma 4
    # sizes, and below the AUTO threshold on the small ones.
    "gemma4": ("per_layer_token_embd.weight",),
    # `{ple_head_dim, ple_rows}` -- `ple.ngram_size`-1 groups of
    # `ple.heads_per_ngram` hash heads, ~20M n-gram rows each (320,001,536 rows
    # of 160 on Qwen3.8-Flash-Next), of which 16 are gathered per token.
    "qwen4exp": ("per_layer_token_embd.weight",),
    # `{ple_head_dim, n_rows}` under the *same* name -- DeepSeek-V4.1-Flash's
    # Engram memory is the qwen4exp n-gram hash with one set of hash constants
    # per Engram layer, and `deepseek41.cpp` keeps them "all in one lazily read
    # tensor, like the qwen4exp n-gram table" rather than one per layer:
    # `n_rows` is the largest `head_offset + head_vocab` over `ple.layers` x
    # (`ple.ngram_size` - 1) x `ple.heads_per_ngram` hash heads. On
    # DeepSeek-V4.1-Flash-1017 that is 2 Engram layers x 24 heads x ~16M buckets
    # = 768,022,850 rows of 256 (196.6B params, ~183 GiB at the checkpoint's fp8
    # / ~195 GiB at Q8_0), gathered a few rows per token by 2- to 4-gram hashes
    # of the surrounding tokens. There is no `engram_embd.weight` to look for:
    # the arch's own tensors are the small `blk.N.engram_kv` / `engram_gate`
    # projections, which are ordinary resident weights.
    # Looked up row-at-a-time like the PLE table above; the reference
    # implementation keeps them in pinned host memory even on 288 GiB GPUs, and
    # llama-model-loader.h's TENSOR_READ_LAZY comment names "PLE / engrams
    # embd" as the use case. Never resident weights.
    "deepseek41": ("per_layer_token_embd.weight",),
}

# `auto_min_size` in llama-model-loader.cpp's `lazy_read::add`: under the default
# lazy mode a marked tensor is read lazily only above this size, because a small
# one is cheap enough to keep resident. The mode flag is `-lzm` / `--lazy-mode`
# (`on` drops the threshold, `off` keeps everything resident) -- not the older
# `--tensor-read-lazy` spelling, which this fork's binary does not accept.
LAZY_AUTO_MIN_BYTES = 4 << 30
LAZY_MODE_FLAG = "--lazy-mode"

# Strips the `blk.N.` prefix of a per-layer tensor name, for matching lazy-tensor
# entries that are written arch-relative (the create_tensor call has no layer in
# its name).
_RE_BLK_LAYER = re.compile(r"^blk\.\d+\.(.+)$")

# Whether `--load-mode` decides the answer: it does not. `init_mappings` maps a
# file when `use_mmap || lazy.any()`, so a marked tensor is read from the mapping
# even under `mlock` -- which deliberately skips locking lazy ranges ("locking a
# lazy tensor would fault all of it in") -- and under `dio`. What does turn lazy
# reading off is `--lazy-mode off`, the size threshold above, and a device
# without `mmap_support`: `load_tensors` resolves `auto` to `off` there (iGPUs,
# llama-model.cpp / upstream #28160), which makes the table resident.


def lazy_tensor_names(metadata):
    """The tensor names this model's architecture marks `TENSOR_READ_LAZY`.

    Empty for every architecture not in `ARCH_LAZY_TENSORS`. Pass the **merged**
    metadata of a split model, not one shard's: `general.architecture` lives only
    in split 0 while the lazy tensor may sit in any shard. Whether a given tensor
    is actually read lazily is `GgufHeader.lazy_bytes`'s answer -- the size
    threshold and `--lazy-mode` are properties of the tensor and the run, not of
    the arch.
    """
    return ARCH_LAZY_TENSORS.get(metadata.get("general.architecture"), ())


@dataclass
class GgufHeader:
    """A parsed GGUF header: metadata, tensor table, and the two numbers that make
    the offsets yield byte sizes (`header_len`, `file_size`).

    `tensors` is a list of `(name, dims, ttype, offset)`; `offset` counts from the
    start of the data section. `file_size` is the whole file, or None when nobody
    reported it -- then every tensor but the last still has an exact size.
    """

    metadata: dict
    tensors: list
    header_len: int
    file_size: int | None = None
    offsets_ordered: bool = True

    @property
    def alignment(self) -> int:
        a = self.metadata.get("general.alignment")
        return a if isinstance(a, int) and a > 0 else ALIGN_DEFAULT

    def sizes(self):
        """`TensorSize` per tensor, in table order.

        The offset gap is the authoritative size: a ggml type id is just an
        integer, and a fork that re-numbered its enum makes `GGML_SIZES` describe
        a format the file does not hold. When the table and the gap agree within
        the alignment padding the table's value wins -- it excludes the padding, so
        it is exactly what a loader allocates -- and only a disagreement takes the
        gap.

        The last tensor's gap needs `file_size`; without it its size is the table's
        and `source` says `table-unverified`, which keeps `renumbered_types` from
        claiming a collision it never saw.
        """
        align = self.alignment
        out = []
        for i, (name, dims, ttype, offset) in enumerate(self.tensors):
            table = tensor_nbytes(dims, ttype)
            gap = None
            if self.offsets_ordered:
                if i + 1 < len(self.tensors):
                    gap = self.tensors[i + 1][3] - offset
                elif self.file_size is not None:
                    gap = self.file_size - self.header_len - offset
                if gap is not None and gap < 0:
                    gap = None  # truncated file, or a table not in file order
            if gap is None:
                nbytes, source = table, "table-unverified"
            elif table is None:
                nbytes, source = gap, "offsets"
            elif 0 <= gap - table < align:
                nbytes, source = table, "table"
            else:
                nbytes, source = gap, "offsets"
            out.append(TensorSize(name, dims, ttype, offset, nbytes, table, source))
        return out

    def renumbered_types(self):
        """Type ids whose bytes-per-point in this file contradicts `GGML_SIZES`.

        One `RenumberedType` per id, summed over the file's tensors. Only a
        disagreement counts: an unknown id is a missing entry, not a re-numbering,
        and a tensor whose size could not be verified is no evidence either way.
        """
        found = {}
        for ts in self.sizes():
            if ts.source != "offsets" or ts.table_bytes is None or ts.nbytes is None:
                continue
            points = math.prod(ts.dims)
            if not points:
                continue
            row = found.setdefault(ts.ttype, [0, 0, 0, 0])
            row[0] += 1
            row[1] += points
            row[2] += ts.table_bytes
            row[3] += ts.nbytes
        return [
            RenumberedType(ttype, quant_info(ttype)[0], n, points, table, offsets)
            for ttype, (n, points, table, offsets) in found.items()
        ]

    def lazy_bytes(self, names, threshold=None):
        """This file's lazily-read tensors: base name -> bytes, empty when it holds
        none.

        *names* comes from `lazy_tensor_names` (see that function for why the
        caller's merged metadata, not this header's, decides it). *threshold* is
        `LAZY_AUTO_MIN_BYTES` unless given -- pass 0 for `--lazy-mode on`, which
        drops the threshold. Names match on the `blk.N.`-stripped form, so a
        per-layer tensor (`blk.N.per_layer_token_embd.weight`) hits the same entry
        as a global one.
        """
        if threshold is None:
            threshold = LAZY_AUTO_MIN_BYTES
        out = {}
        if not names:
            return out
        for ts in self.sizes():
            m = _RE_BLK_LAYER.match(ts.name)
            base = m.group(1) if m else ts.name
            if base not in names or ts.nbytes is None or ts.nbytes <= threshold:
                continue
            out[base] = out.get(base, 0) + ts.nbytes
        return out
