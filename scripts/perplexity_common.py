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

import re
import shlex
from collections.abc import Iterator
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
    "n-gpu-layers": ("ngl",),
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
}
ALIASES: dict[str, str] = {
    short: long for long, shorts in _ALIAS_GROUPS.items() for short in (long, *shorts)
}

#: Flags that identify the model to load, in precedence order.
MODEL_KEYS = ("hf-repo", "model")
#: Flags that describe the KV cache.
KV_KEYS = ("cache-type-k", "cache-type-v", "kv-tail-tokens")
#: KLD plumbing: present on some runs and not others by construction (the
#: logits-dump run omits ``--kl-divergence``), so never part of a run's
#: identity nor of its label.
KLD_KEYS = ("kl-divergence", "kl-divergence-base")
#: Options that change nothing about what a run measures, and so take no part in
#: its identity or its label either: ``--offline`` only forbids a download.  The
#: sweeper used to inject it on every run but the logits dump -- without this, a
#: sweep resumed against such a log would re-run its baseline, and every label in
#: the report would carry a meaningless ``offline=on``/``off``.
NEUTRAL_KEYS = frozenset({"offline"})

# A token is a flag if it starts with a dash followed by a letter.  Negative
# numbers (``--seed -1``, ``-ngl -1``) are therefore values, not flags, which is
# what makes value-vs-flag disambiguation work without knowing each flag's arity.
_FLAG_RE = re.compile(r"^--?[A-Za-z]")


def canon(flag: str) -> str:
    """Canonical dash-free name of a CLI flag: ``-ctk`` -> ``cache-type-k``."""
    bare = flag.lstrip("-")
    return ALIASES.get(bare, bare)


def quant_matches(path: str, tag: str) -> bool:
    """Whether *path* is the file (or directory) of ``-hf <repo>:<tag>``.

    The tag appears either in the file name of a single-file quant or as the
    directory of a split one, so the whole path is searched -- but on **token
    boundaries**, underscore included.  A plain substring test would make
    ``Q6_K`` match ``Q6_K_L`` as well (and ``Q4_K`` match ``Q4_K_M``), which
    silently attributes a sibling quant's shard to the run and, once the report
    sums the provenance, inflates the model by the size of a second model.
    """
    return re.search(rf"(?<!\w){re.escape(tag)}(?!\w)", path, re.IGNORECASE) is not None


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

    The KLD plumbing is excluded, so the logits-dump run and its
    ``--kl-divergence`` rerun share a signature -- which is what makes the
    rerun's "already done?" check independent of the dump run -- and so are the
    options that measure nothing (:data:`NEUTRAL_KEYS`).
    """
    args = parse_cmd_args(cmd)
    return frozenset(
        (k, v) for k, v in args.items() if k not in KLD_KEYS and k not in NEUTRAL_KEYS
    )


def has_kld(cmd: str) -> bool:
    """Whether *cmd* measures KL divergence (as opposed to writing the dump)."""
    return parse_cmd_args(cmd).get("kl-divergence") is True


# ---------------------------------------------------------------------------
#  Log structure
# ---------------------------------------------------------------------------
# A completed run ends with a 30+ dash separator; an aborted one ends with an
# `--- ABORTED ... ---` marker and is followed, inside the same section, by the
# next run's provenance.  Hence the two-level split.
_SECTION_RE = re.compile(r"^-{30,}", re.MULTILINE)
_ABORTED_RE = re.compile(r"^-{3} ABORTED .+ -{3}$", re.MULTILINE)
_CMD_RE = re.compile(r"^\s*(llama-perplexity\s+.*)$")
_LABEL_RE = re.compile(r"^#\s*LABEL:\s*(.*)$")
# `# model: <path> size=<n> mtime=... sha256/<n>B=...`, as written by
# perplexity.py's file_provenance().  Unresolved / unreadable models have no
# `size=` and are skipped.
_MODEL_PROV_RE = re.compile(r"^#\s*model:\s*(?P<path>.+?)\s+size=(?P<size>\d+)\b")
# Last path component, whatever separator the machine that wrote the log used.
_BASENAME_RE = re.compile(r"[^\\/]*$")


@dataclass(frozen=True)
class ModelFile:
    """One model shard as recorded in a ``# model:`` provenance line."""

    path: str
    size: int


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


def iter_runs(text: str) -> Iterator[LogRun]:
    """Every llama-perplexity run recorded in *text*, in log order.

    A run's weights are taken from the **last** ``# model:`` block of its
    section, and only that one.  A run that downloaded its model carries two
    blocks -- the pre-run one describes the cache as it was *before*, so it may
    name nothing (or, in logs written before this was fixed, the wrong quant),
    while the one re-logged afterwards describes what the run actually loaded.
    Merging them would sum a model that was never loaded into the one that was.

    Within that block the shards are keyed by **file name**: the same shard
    reachable through two Hugging Face cache snapshots is one shard, and
    counting both would double the model's size.  Split GGUFs are unaffected,
    their shards having distinct names.
    """
    for section in _SECTION_RE.split(text):
        chunks = _ABORTED_RE.split(section)
        for i, chunk in enumerate(chunks):
            lines = chunk.strip().split("\n")
            cmd = ""
            label = ""
            weights: dict[str, ModelFile] = {}
            block: dict[str, ModelFile] | None = None
            for line in lines:
                if m := _MODEL_PROV_RE.match(line):
                    if block is None:
                        block = weights = {}
                    path = m.group("path")
                    name = _BASENAME_RE.search(path).group()
                    block[name] = ModelFile(path, int(m.group("size")))
                    continue
                block = None  # any other line ends the provenance block
                if m := _CMD_RE.match(line):
                    candidate = m.group(1).strip()
                    # Older logs record the binary version as a bare
                    # `llama-perplexity --version` invocation rather than the
                    # `# llama-perplexity:` provenance comment; it is not a run.
                    if not cmd and set(parse_cmd_args(candidate)) != {"version"}:
                        cmd = candidate
                elif m := _LABEL_RE.match(line):
                    label = m.group(1).strip()
            if not cmd:
                continue
            yield LogRun(
                cmd=cmd,
                lines=lines,
                # An ABORTED marker follows every chunk but the last.
                aborted=i < len(chunks) - 1,
                label=label,
                weight_files=weights,
            )
