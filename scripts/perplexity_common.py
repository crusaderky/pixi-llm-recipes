"""Shared llama-perplexity command-line and log handling.

Imported by ``perplexity.py`` (the sweeper) and ``perplexity-report.py`` (the
report).  Stdlib only, on purpose: the sweeper runs in the ``llamacpp-*``
environments and the report in ``pytools``, and the two share no third-party
dependency.

The sweeper writes the log and the report reads it back, so both need the same
answer to "which run is this?".  A run is identified by its command line, and a
command line is compared *after* canonicalisation: llama.cpp accepts several
spellings of every flag (``-ctk`` / ``--cache-type-k``, ``-hf`` / ``-hfr`` /
``--hf-repo``), the sweeper only ever emits the long one, but ``common:`` in the
config -- and every log written before the cross-product redesign -- is full of
short ones.  Canonicalising here is what lets a sweep resume against such a log
instead of re-running it from scratch.
"""

from __future__ import annotations

import pathlib
import re
import shlex
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
#  Flag canonicalisation
# ---------------------------------------------------------------------------
# Canonical (long, dash-free) name -> the short spellings llama.cpp also
# accepts for it.  Only the flags this project actually passes are listed; an
# unlisted flag canonicalises to itself, stripped of its leading dashes, which
# is all the identity/label machinery needs.  Note that the dash count is not
# derivable from the name (`-cmoe` and `-hffv` are single-dash, `--ppl` and
# `--ui` are double), so guessing is not an option -- hence the table.
_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "hf-repo": ("hf", "hfr"),
    "hf-file": ("hff",),
    "model": ("m",),
    "cache-type-k": ("ctk",),
    "cache-type-v": ("ctv",),
    "ctx-size": ("c",),
    "file": ("f",),
    "flash-attn": ("fa",),
    "n-gpu-layers": ("ngl", "gpu-layers"),
    "batch-size": ("b",),
    "ubatch-size": ("ub",),
    "threads": ("t",),
    "parallel": ("np",),
    "cpu-moe": ("cmoe",),
    "n-cpu-moe": ("ncmoe",),
    "override-tensor": ("ot",),
    "split-mode": ("sm",),
    "main-gpu": ("mg",),
    "tensor-split": ("ts",),
    "device": ("dev",),
    "seed": ("s",),
    "prompt": ("p",),
    "threads-batch": ("tb",),
    "cpu-mask": ("C",),
    "cpu-range": ("Cr",),
    "cpu-mask-batch": ("Cb",),
    "cpu-range-batch": ("Crb",),
    "kv-offload": ("kvo",),
    "no-kv-offload": ("nkvo",),
    "no-repack": ("nr",),
    "direct-io": ("dio",),
    "no-direct-io": ("ndio",),
    "load-mode": ("lm",),
    "prefetch-weights": ("pw",),
    # `-fit` / `--fit` strip to the same bare name, so only the abbreviated
    # long forms need listing.
    "fit-target": ("fitt",),
    "fit-ctx": ("fitc",),
    "hf-token": ("hft",),
    "verbose": ("v", "log-verbose"),
    "verbosity": ("lv", "log-verbosity"),
}
ALIASES: dict[str, str] = {
    short: long for long, shorts in _ALIAS_GROUPS.items() for short in (long, *shorts)
}

#: Flags that identify the model to load, in precedence order.  ``--hf-file``
#: is one of them: it names the file inside the repo, which is the only way to
#: reach a quant whose tag llama.cpp resolves to a sibling.  It comes last
#: because it is a modifier of ``--hf-repo`` rather than an alternative to it,
#: but it belongs here so that everything treating "the model" as one dimension
#: -- a run's identity, the report's labels -- covers it without a local patch.
MODEL_KEYS = ("hf-repo", "model", "hf-file")
#: Flags that describe the KV cache.
KV_KEYS = ("cache-type-k", "cache-type-v", "kv-tail-tokens")
#: KLD plumbing: present on some runs and not others by construction (the
#: logits-dump run omits ``--kl-divergence``), so never part of a run's
#: identity nor of its label.
KLD_KEYS = ("kl-divergence", "kl-divergence-base")
#: Options that touch neither the logits nor the clock, and so take no part in a
#: run's identity, its label, or the report's "Common Parameters" block:
#: ``--offline`` only forbids a download, ``--hf-token`` only authenticates one
#: (and has no business being echoed into a report), and the rest is logging.
#: The sweeper used to inject ``--offline`` on every run but the logits dump --
#: without this, a sweep resumed against such a log would re-run its baseline,
#: and every label in the report would carry a meaningless ``offline=on``/``off``.
NEUTRAL_KEYS = frozenset(
    {
        "offline",
        "hf-token",
        "log-disable",
        "log-file",
        "log-colors",
        "log-prefix",
        "no-log-prefix",
        "log-timestamps",
        "no-log-timestamps",
        "verbose",
        "verbosity",
        "perf",
        "no-perf",
    }
)

#: Options that steer *where* and *how* the work runs -- device placement,
#: offload, threading, model loading, and beellama's ``--fit`` autotuner.  They
#: change how fast a run is, never the distribution it measures.  (Moving a
#: layer between CPU and GPU does shuffle the last bits of a dot product, but
#: several orders of magnitude below the KLD a quantized cache costs, which is
#: what a sweep is there to resolve.)
#:
#: They therefore take no part in a run's identity: a sweep resumed after the
#: offload policy changed -- ``-ngl 99`` dropped in favour of ``--fit-target``,
#: a move to another GPU, a thread count tuned again -- skips the runs it already
#: has instead of repeating days of work.  Delete a run's block from the log to
#: force it to run again.  They take no part in a run's *label* either, for the
#: same reason.  Unlike :data:`NEUTRAL_KEYS` they do survive into the report's
#: "Common Parameters" block: a shared ``-ngl 99`` is what makes its speed chart
#: interpretable.
#:
#: Both polarities of a ``--x`` / ``--no-x`` pair are listed, since
#: :func:`canon` deliberately keeps them apart (they are opposites everywhere
#: else).
PLACEMENT_KEYS = frozenset(
    {
        # Threading and CPU affinity
        "threads",
        "threads-batch",
        "cpu-mask",
        "cpu-mask-batch",
        "cpu-range",
        "cpu-range-batch",
        "cpu-strict",
        "cpu-strict-batch",
        "poll",
        "poll-batch",
        "prio",
        "prio-batch",
        "numa",
        # Device placement and offload
        "n-gpu-layers",
        "device",
        "split-mode",
        "tensor-split",
        "main-gpu",
        "override-tensor",
        "cpu-moe",
        "n-cpu-moe",
        "kv-offload",
        "no-kv-offload",
        "op-offload",
        "no-op-offload",
        "no-host",
        "rpc",
        "prefetch-weights",
        "repack",
        "no-repack",
        # Model loading
        "load-mode",
        "mlock",
        "mmap",
        "no-mmap",
        "direct-io",
        "no-direct-io",
        "check-tensors",
        "warmup",
        "no-warmup",
        # beellama's fit autotuner: it only adjusts arguments left *unset*, and
        # everything a KLD sweep compares against the dump (--ctx-size above
        # all) is pinned in `common:`, so all it can move is the offload split.
        "fit",
        "fit-target",
        "fit-ctx",
    }
)

#: Every key excluded from a run's identity and from its label.
IGNORED_KEYS = frozenset(KLD_KEYS) | NEUTRAL_KEYS | PLACEMENT_KEYS

# A token is a flag if it starts with a dash followed by a letter.  Negative
# numbers (``--seed -1``, ``-ngl -1``) are therefore values, not flags, which is
# what makes value-vs-flag disambiguation work without knowing each flag's arity.
_FLAG_RE = re.compile(r"^--?[A-Za-z]")


def canon(flag: str) -> str:
    """Canonical dash-free name of a CLI flag: ``-ctk`` -> ``cache-type-k``."""
    bare = flag.lstrip("-")
    return ALIASES.get(bare, bare)


# --------------------------------------------------------------------------
#  Which file does `-hf <repo>:<tag>` load?
# --------------------------------------------------------------------------
# A port of `find_best_model` / `get_split_files` / `gguf_filename_is_model` in
# llama.cpp's `common/download.cpp`.  Guessing the rule instead of mirroring it
# is what this replaced: a tag is *not* matched on token boundaries, and it does
# *not* select every file it appears in.  Both halves of that matter for a repo
# publishing mixed quants (`Qwen3.8-27B-AD-Q5_K_M-Q4_K_M.gguf` next to
# `-Q4_K_M.gguf` and `-Q5_K_M.gguf`), where a boundary test claims two models
# for one run and the report then sums them into a model twice its real size.

#: What llama.cpp never treats as a model file, whatever the tag says:
#: sidecars and metadata shipped in the same repo (`gguf_filename_is_model`).
_NOT_A_MODEL = ("mmproj", "imatrix", "mtp-", "eagle3-", "dflash-", "dspark-")

#: Split-GGUF suffix on the stem, e.g. `model-00002-of-00003` (`re_split`).
_SPLIT_RE = re.compile(r"^(.+)-(\d{5})-of-(\d{5})$", re.IGNORECASE)

#: The tags llama.cpp tries, in order, for an `-hf <repo>` carrying none.
_DEFAULT_TAGS = ("Q4_K_M", "Q8_0")

#: `.../snapshots/<commit>` prefix of a Hugging Face cache path, if it has one.
_SNAPSHOT_RE = re.compile(r"^(.*[\\/]snapshots[\\/][^\\/]+)[\\/]")

#: Last path component, whatever separator the machine that wrote the log used.
_BASENAME_RE = re.compile(r"[^\\/]*$")


def _is_model_gguf(path: str) -> bool:
    """Whether llama.cpp would consider *path* a candidate model file."""
    name = _BASENAME_RE.search(path).group()
    return path.endswith(".gguf") and not any(s in name for s in _NOT_A_MODEL)


def _split_info(path: str) -> tuple[str, int, int]:
    """``(prefix, index, count)`` of a GGUF; ``(stem, 1, 1)`` when unsplit."""
    stem = path.removesuffix(".gguf")
    if m := _SPLIT_RE.match(stem):
        return m.group(1), int(m.group(2)), int(m.group(3))
    return stem, 1, 1


def _find_best_model(models: list[str], tag: str) -> str | None:
    """The first file of *models* that ``:tag`` resolves to, or None.

    llama.cpp searches for the tag followed by ``.`` or ``-``, case-insensitively
    and with **no left boundary**, and stops at the first hit -- so listing order
    decides between siblings, and `-` (0x2D) sorting before `.` (0x2E) is what
    makes ``:Q5_K_M`` land on ``…-Q5_K_M-Q4_K_M.gguf`` rather than on the
    ``…-Q5_K_M.gguf`` standing right next to it in the same repo.
    """
    for candidate in (tag,) if tag else _DEFAULT_TAGS:
        pattern = re.compile(re.escape(candidate) + "[.-]", re.IGNORECASE)
        for path in models:
            if pattern.search(path) and _split_info(path)[1] == 1:
                return path
    # An unmatched tag is an error in llama.cpp, not a fallback: it would load
    # some other quant than the one the sweep asked for.
    if tag:
        return None
    return next((p for p in models if _split_info(p)[1] == 1), None)


def select_model_files(paths: Iterable[str], tag: str) -> list[str]:
    """The files ``-hf <repo>:<tag>`` loads, out of the GGUFs in *paths*.

    Exactly one model is loaded per run -- one file, or the shards of one split
    GGUF -- so this returns at most that, never every path the tag appears in.
    Sidecars (mmproj, imatrix, draft models) are not candidates: llama.cpp
    downloads them alongside the weights, but llama-perplexity does not load
    them, and charging their bytes to the run would move it along the report's
    VRAM axis for weights that were never resident.

    *paths* is grouped by Hugging Face cache snapshot first, and each snapshot
    resolved on its own, because a repo cached at two commits is the one case
    where two answers are the honest one: the weights moved under the sweep,
    which is what the provenance exists to show.

    An empty *tag* takes llama.cpp's own default (``Q4_K_M``, then ``Q8_0``,
    then the first model file), which is also what makes a plain ``--model``
    path resolve to itself plus its sibling shards.
    """
    groups: dict[str, list[str]] = {}
    for path in paths:
        m = _SNAPSHOT_RE.match(path)
        groups.setdefault(m.group(1) if m else "", []).append(path)

    out: list[str] = []
    for group in groups.values():
        models = [p for p in group if _is_model_gguf(p)]
        if (best := _find_best_model(models, tag)) is not None:
            out.extend(split_shards(models, best))
    return out


def split_shards(paths: Iterable[str], model: str) -> list[str]:
    """*model* plus the rest of its split, out of *paths* (``get_split_files``).

    A split GGUF is one model in several files, so a run that loads any shard
    loads them all -- which is what makes a ``--hf-file`` naming shard 1 the same
    measurement as the tag that resolves to it.
    """
    prefix, _, count = _split_info(model)
    if count <= 1:
        return [model]
    return [
        p for p in paths if (info := _split_info(p))[0] == prefix and info[2] == count
    ]


def iter_cmd_options(cmd: str) -> Iterator[tuple[str, str | bool, list[str]]]:
    """Every option of a command line as ``(canonical key, value, raw tokens)``.

    A flag that takes no value yields ``True``.  The raw tokens are kept so a
    caller can echo the command back in its original spelling (see the report's
    "Common Parameters" block) instead of the canonical one.  The executable
    name and any stray positional argument are skipped.
    """
    tokens = shlex.split(cmd)
    i = 0
    while i < len(tokens):
        if not _FLAG_RE.match(tokens[i]):
            i += 1  # executable name or stray positional
            continue
        key = canon(tokens[i])
        if i + 1 < len(tokens) and not _FLAG_RE.match(tokens[i + 1]):
            yield key, tokens[i + 1], tokens[i : i + 2]
            i += 2
        else:
            yield key, True, tokens[i : i + 1]
            i += 1


def parse_cmd_args(cmd: str) -> dict[str, str | bool]:
    """Canonical ``{key: value}`` view of a llama-perplexity command line.

    A repeated flag keeps its last value, matching llama.cpp's own last-wins
    parsing -- which is what lets a combo's ``--hf-repo`` override an ``-hf``
    left in the config's ``common:`` prefix.
    """
    return {key: value for key, value, _ in iter_cmd_options(cmd)}


def cmd_signature(cmd: str) -> frozenset[tuple[str, str | bool]]:
    """Identity of a run: its canonical ``(key, value)`` pairs, order-insensitive.

    Everything in :data:`IGNORED_KEYS` is left out: the KLD plumbing, so the
    logits-dump run and its ``--kl-divergence`` rerun share a signature -- which
    is what makes the rerun's "already done?" check independent of the dump run
    -- the options that measure nothing (:data:`NEUTRAL_KEYS`), and the ones
    that only decide where the work runs (:data:`PLACEMENT_KEYS`).
    """
    args = parse_cmd_args(cmd)
    return frozenset((k, v) for k, v in args.items() if k not in IGNORED_KEYS)


def has_kld(cmd: str) -> bool:
    """Whether *cmd* measures KL divergence (as opposed to writing the dump)."""
    return parse_cmd_args(cmd).get("kl-divergence") is True


# ---------------------------------------------------------------------------
#  Log structure
# ---------------------------------------------------------------------------
# A completed run ends with a 30+ dash separator; an aborted one ends with an
# `--- ABORTED ... ---` marker and is followed, inside the same section, by the
# next run's provenance.  Hence the two-level split.
SEPARATOR = "-" * 30
"""Line closing off a run's block in the log; the section delimiter."""

_SECTION_RE = re.compile(r"^-{30,}", re.MULTILINE)
_ABORTED_RE = re.compile(r"^-{3} ABORTED .+ -{3}$", re.MULTILINE)
_CMD_RE = re.compile(r"^\s*(llama-perplexity\s+.*)$")
_LABEL_RE = re.compile(r"^#\s*LABEL:\s*(.*)$")
# The `#` comments a run's block opens with, and which therefore mark where the
# next block starts when two of them share a section (see `_run_spans`).
_BLOCK_HEAD_RE = re.compile(r"^#\s*(?:llama-perplexity|model|LABEL)\b")
# `# model: <path> size=<n> [lazy=<n>] mtime=... sha256/<n>B=...`, as written by
# perplexity.py's file_provenance().  Unresolved / unreadable models have no
# `size=` and are skipped.  `lazy=` is written only for a shard that holds one of
# the tensors llama.cpp reads from disk on demand, so it is absent from every log
# of an ordinary model -- and from every log written before it existed, which is
# why it defaults to 0 rather than to "unknown".
_MODEL_PROV_RE = re.compile(
    r"^#\s*model:\s*(?P<path>.+?)\s+size=(?P<size>\d+)\b"
    r"(?:\s+lazy=(?P<lazy>\d+)\b)?"
)


@dataclass(frozen=True)
class ModelFile:
    """One model shard as recorded in a ``# model:`` provenance line."""

    path: str
    size: int
    lazy: int = 0
    """Bytes of ``size`` that llama.cpp reads from disk on demand rather than
    keeping resident -- the n-gram / per-layer embedding table of an arch that
    marks it ``TENSOR_READ_LAZY`` (``gguf_common.ARCH_LAZY_TENSORS``). 0 when the
    shard holds no such tensor, and in logs written before it was recorded."""

    @property
    def resident(self) -> int:
        """Bytes of this shard that end up in the resident weights.

        Clamped at 0 so a corrupt provenance line cannot make a *negative*
        contribution to a sum. It is not a repair -- a ``lazy=`` above its own
        ``size=`` means the line is wrong and the sum is meaningless either way --
        so a caller that sums these must check :attr:`lazy_overflow` and say so;
        ``perplexity-report``'s ``_assign_vram`` does.
        """
        return max(0, self.size - self.lazy)

    @property
    def lazy_overflow(self) -> bool:
        """Whether ``lazy=`` exceeds ``size=``, i.e. the line cannot be trusted."""
        return self.lazy > self.size


@dataclass
class LogRun:
    """One llama-perplexity invocation as recorded in the log."""

    cmd: str
    """The full command line, verbatim."""
    lines: list[str]
    """Every line of the run's block: comments, the command, its output."""
    aborted: bool = False
    """True if the block ends in an ``--- ABORTED ... ---`` marker."""
    label: str = ""
    """Override from the ``# LABEL: ...`` comment; empty when absent."""
    weight_files: dict[str, ModelFile] = field(default_factory=dict)
    """The shards the run loaded, keyed by file name (see :func:`iter_runs`)."""

    @property
    def args(self) -> dict[str, str | bool]:
        return parse_cmd_args(self.cmd)

    @property
    def signature(self) -> frozenset[tuple[str, str | bool]]:
        return cmd_signature(self.cmd)

    @property
    def has_kld(self) -> bool:
        return has_kld(self.cmd)


def _is_run_cmd(line: str) -> str:
    """The command *line* records, or ``""`` if it is not a run's command.

    Older logs record the binary version as a bare ``llama-perplexity
    --version`` invocation rather than the ``# llama-perplexity:`` provenance
    comment; it is not a run.
    """
    if not (m := _CMD_RE.match(line)):
        return ""
    cmd = m.group(1).strip()
    return "" if set(parse_cmd_args(cmd)) == {"version"} else cmd


def _run_spans(lines: list[str]) -> list[tuple[int, int]]:
    """Index ranges of the runs recorded in one section's *lines*.

    Normally a section holds exactly one run, since `run_llama_perplexity`
    closes every block with a `SEPARATOR`.  It writes that line only after the
    child exits, though, so a sweep killed mid-run leaves its block unclosed and
    the next invocation appends into the same section -- and if the child's last
    write had no trailing newline, the next block's first comment is even glued
    onto the end of it.  A second command line is therefore a second run, and
    dropping it (as taking the first and ignoring the rest used to) loses a
    *completed* measurement and files it under the killed run's flags instead:
    the sweep then re-runs the good combo and skips the dead one forever.

    A run's block opens with its `#` provenance comments, so the boundary is the
    start of the comment block heading the second command -- not the command
    itself.  Where that command has none (killed before the comments were
    written, or an older log that logged no provenance) the command line is the
    boundary.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    seen_cmd = False
    # Start of the run of `#` comments ending at the current line, if any.
    comments: int | None = None
    for i, line in enumerate(lines):
        if _BLOCK_HEAD_RE.match(line):
            if comments is None:
                comments = i
            continue
        if _is_run_cmd(line):
            if seen_cmd:
                boundary = i if comments is None else comments
                spans.append((start, boundary))
                start = boundary
            seen_cmd = True
        comments = None
    spans.append((start, len(lines)))
    return spans


def _block_weights(lines: list[str]) -> dict[str, ModelFile]:
    """The shards named by the **last** ``# model:`` block of *lines*.

    Only that one.  A run that downloaded its model carries two blocks -- the
    pre-run one describes the cache as it was *before*, so it may name nothing
    (or, in logs written before this was fixed, the wrong quant), while the one
    re-logged afterwards describes what the run actually loaded.  Merging them
    would sum a model that was never loaded into the one that was.

    Within that block the shards are keyed by **file name**: the same shard
    reachable through two Hugging Face cache snapshots is one shard, and
    counting both would double the model's size.  Split GGUFs are unaffected,
    their shards having distinct names.
    """
    weights: dict[str, ModelFile] = {}
    block: dict[str, ModelFile] | None = None
    for line in lines:
        if m := _MODEL_PROV_RE.match(line):
            if block is None:
                block = weights = {}
            path = m.group("path")
            name = _BASENAME_RE.search(path).group()
            block[name] = ModelFile(
                path, int(m.group("size")), int(m.group("lazy") or 0)
            )
        else:
            block = None  # any other line ends the provenance block
    return weights


def iter_runs(text: str) -> Iterator[LogRun]:
    """Every llama-perplexity run recorded in *text*, in log order."""
    for section in _SECTION_RE.split(text):
        chunks = _ABORTED_RE.split(section)
        for i, chunk in enumerate(chunks):
            lines = chunk.strip().split("\n")
            spans = _run_spans(lines)
            for j, (start, stop) in enumerate(spans):
                block = lines[start:stop]
                cmd = next(filter(None, map(_is_run_cmd, block)), "")
                if not cmd:
                    continue
                labels = [
                    m.group(1).strip() for line in block if (m := _LABEL_RE.match(line))
                ]
                yield LogRun(
                    cmd=cmd,
                    lines=block,
                    # An ABORTED marker follows every chunk but the last, and
                    # describes the run it directly follows -- the chunk's last.
                    # Any run before that one was killed, not ETA-aborted.
                    aborted=i < len(chunks) - 1 and j == len(spans) - 1,
                    label=labels[-1] if labels else "",
                    weight_files=_block_weights(block),
                )


# Enough of the log's tail to find its last non-blank line.
_SEAL_TAIL_BYTES = 8192


def seal_log(logfile: pathlib.Path) -> None:
    """Close off a block in *logfile* left dangling by a sweep killed mid-run.

    `run_llama_perplexity` writes a block's closing `SEPARATOR` only once the
    child has exited, so a sweep killed -- or a machine that lost power --
    mid-run leaves the block open, and the next invocation appends into the same
    section.  `iter_runs` splits such a section back apart, but only the writer
    can keep the log's one-section-per-run invariant intact for every other
    reader (`grep`, a human, an older checkout of this script).

    Called before appending, so it also repairs a log that a previous version
    left broken.  A no-op on the healthy path, where the last line is already
    the separator.
    """
    try:
        size = logfile.stat().st_size
    except OSError:
        return  # no log yet
    if not size:
        return
    with open(logfile, "rb") as f:
        f.seek(max(0, size - _SEAL_TAIL_BYTES))
        tail = f.read().decode("utf-8", "replace")
    last = next((ln for ln in reversed(tail.splitlines()) if ln.strip()), "")
    closed = _SECTION_RE.match(last) or _ABORTED_RE.match(last)
    if tail.endswith("\n") and closed:
        return
    with open(logfile, "a") as f:
        if not tail.endswith("\n"):
            # The child's last write had no trailing newline, so the caller's
            # first comment would be glued onto the end of it.
            f.write("\n")
        if not closed:
            f.write(SEPARATOR + "\n")
