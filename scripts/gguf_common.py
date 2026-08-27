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
"""

import math
import struct
import sys

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
    """Parse a GGUF header from bytes. Returns (metadata_dict, tensor_list)
    where tensor_list is a list of (name, dims, ttype).
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
        c.u64()  # data offset -- unused for our needs
        out.append((name, dims, ttype))
    return metadata, out


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
        while True:
            fh.seek(0)
            buf = fh.read(n)
            try:
                return parse_header(buf)
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
# call, per arch -> tuple of names (`src/models/<arch>.cpp`). Both entries are
# the same tensor: an n-gram / per-layer hash-embedding table, gathered a handful
# of rows at a time, so llama.cpp registers its byte range in
# `lazy_tensor_ranges` and reads those rows out of the mmap when a token needs
# them (llama-model-loader.cpp). It is never copied into the resident weights and
# therefore never into VRAM -- which matters here because on Qwen3.8-Flash-Next
# it is a *quarter* of the file (26.8 GiB of 103.7 GiB at UD-Q4_K_XL).
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
}

# `auto_lazy_min_size` in llama-model-loader.cpp: under the default
# `--tensor-read-lazy auto` a marked tensor is read lazily only above this size,
# because a small one is cheap enough to keep resident. `--tensor-read-lazy on`
# drops the threshold; `off` keeps everything resident.
LAZY_AUTO_MIN_BYTES = 4 << 30

# Lazy reading needs the mmap the rows are read from, and `use_mmap` is set for
# exactly these `--load-mode` values (llama-model-loader.cpp). Under `mlock`
# (this project's own models.ini) or `dio` there is no mapping, so a marked
# tensor is loaded in full and is resident like any other weight.
LAZY_LOAD_MODES = ("auto", "mmap", "mmap+mlock")


def lazy_tensors(metadata, tensors, threshold=LAZY_AUTO_MIN_BYTES):
    """The tensors of one GGUF file that llama.cpp reads lazily from disk.

    Yields (name, nbytes) for every tensor its architecture marks
    `TENSOR_READ_LAZY` and that clears *threshold* (pass 0 for
    `--tensor-read-lazy on`, which has none). Empty for every architecture not in
    `ARCH_LAZY_TENSORS`, and for a shard that does not happen to hold the tensor
    -- the caller sums over a model's shards.
    """
    arch = metadata.get("general.architecture")
    names = ARCH_LAZY_TENSORS.get(arch, ())
    if not names:
        return
    for name, dims, ttype in tensors:
        if name not in names:
            continue
        nbytes = tensor_nbytes(dims, ttype)
        if nbytes and nbytes > threshold:
            yield name, nbytes


def lazy_tensor_bytes(metadata, tensors, threshold=LAZY_AUTO_MIN_BYTES):
    """Total bytes of one GGUF file's lazily-read tensors; 0 when it has none."""
    return sum(nbytes for _, nbytes in lazy_tensors(metadata, tensors, threshold))
