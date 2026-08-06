#!/usr/bin/env python3
"""
Parse perplexity.log, extract each run's quantization settings and per-chunk KL
divergence, generate HTML and/or Markdown report with table + log-scale plot.

A run's display label is built from whatever the sweep actually varied -- the
model (`--hf-repo`), the KV cache (`-ctk`/`-ctv`/`--kv-tail-tokens`), or any
other option -- and a `# LABEL:` comment in the log overrides it (see
`_build_labels`).  The Pareto/x-axis cost is total VRAM: the model weights, read
off the `# model:` provenance the sweeper records, plus the KV cache at the
projected context.

HTML report: interactive Chart.js plot.
Markdown report: static SVG plot (via matplotlib) + cross-ref link to HTML.

Usage:

1. Modify perplexity.yaml
2. pixi r perplexity
3. pixi r perplexity-report
"""

import argparse
import contextlib
import html as html_mod
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

from kv_cache_common import ModelKV, resolve_bpw, resolve_model
from perplexity_common import (
    KLD_KEYS,
    KV_KEYS,
    MODEL_KEYS,
    NEUTRAL_KEYS,
    ModelFile,
    iter_cmd_options,
    iter_runs,
    quant_matches,
)

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
    except (urllib.error.URLError, OSError) as e:
        print(f"Warning: could not fetch Chart.js ({e}), using CDN", file=sys.stderr)
        return f'<script src="{CHART_JS_CDN}"></script>'


# ---------------------------------------------------------------------------
#  "Context (MiB) @ 256k" column.
#
#  The KV-cache VRAM of a run does not follow from bpw alone: it comes from
#  kv_cache_common.ModelKV, which captures the model's layer geometry and models
#  beellama v0.4.1's persistent KV-cache allocation (see that module for the
#  details). The exact-tail overlay it models scales with the tail and
#  n_parallel, so the figure is deployment-dependent: the report evaluates at a
#  chosen context (--ctx-size, default: the run's own context from the log) and
#  n_parallel (--n-parallel, default 4 = llama-server's auto).
# ---------------------------------------------------------------------------
DEFAULT_CTX_SIZE = 262144  # 256k tokens (256 * 1024); override with --ctx-size
BYTES_PER_MIB = 1 << 20


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
    for grp in spec.cache_breakdown(n_ctx, bpw_k, bpw_v, tail, n_parallel):
        lines.append(
            f"{grp.name}: {grp.layers} layers x{grp.kv_heads} kv-heads x"
            f"{grp.key_dim}/{grp.value_dim} dim; {grp.note} "
            f"-> {grp.nbytes / BYTES_PER_MIB:,.0f} MiB"
        )
    lines.append(f"total {mib:,.0f} MiB")
    return mib, "\n".join(lines)


# ---------------------------------------------------------------------------
#  Log parser
# ---------------------------------------------------------------------------
# Runs, their command lines, their `# LABEL:` overrides and their `# model:`
# weight sizes all come from perplexity_common.iter_runs; the options of one
# command line come from its parse_cmd_args, which canonicalises every spelling
# llama.cpp accepts (`-ctk` and `--cache-type-k`, `-hf` and `--hf-repo`) so that
# logs written before the cross-product redesign still parse.
#
# Baseline logits dump logs "perplexity: … seconds per pass"; KLD runs log
# "kl_divergence: … seconds per pass". Match either so the baseline speed is
# derived from the per-pass time, not the (remaining-time) ETA fallback.
SECONDS_PER_PASS_RE = re.compile(
    r"(?:kl_divergence|perplexity): (\d+\.?\d*) seconds per pass"
)
TOTAL_MINUTES_RE = re.compile(r"(\d+\.?\d*)\s+minutes$")
# Summary statistics from the "====== KL divergence statistics ======" block
SUMMARY_HDR = re.compile(r"^=+\s+KL divergence statistics\s+=+")
SUMMARY_LINE = re.compile(r"^\s*(Mean|Median|([\d.]+)%)\s+KLD:\s+([\d.-]+)")
# "Cor(ln(PPL(Q)), ln(PPL(base))):  99.87%"
PPL_COR_RE = re.compile(
    r"^Cor\(ln\(PPL\(Q\)\), ln\(PPL\(base\)\)\):\s*([\d.]+)\s*%", re.MULTILINE
)
# "Same top p: 97.148 ± 0.043 %"
TOP_P_RE = re.compile(r"^Same top p:\s*([\d.]+)\s*±\s*([\d.]+)", re.MULTILINE)
# "Same sampled p: 48.512 ± 0.061 %" -- the collision probability
# sum_i p_base(i)*p(i), i.e. temperature-1 agreement, as opposed to "Same top p"
# which only compares the argmax (greedy decoding). Only emitted by patched
# llama-perplexity builds, so every consumer below treats it as optional: on a log
# without it the column and the plot are silently omitted.
COLL_P_RE = re.compile(r"^Same sampled p:\s*([\d.]+)\s*±\s*([\d.]+)", re.MULTILINE)


def _common_params(runs: list[dict]) -> str:
    """The command-line options every run shares, in the first run's spelling.

    An option whose *value* differs anywhere is dropped along with its flag, so
    the block shows only what the rows genuinely have in common -- everything
    else is in their labels.  The KLD plumbing is excluded: the logits-dump run
    lacks ``--kl-divergence`` by construction.
    """
    if not runs:
        return ""
    shared: set[tuple[str, str | bool]] | None = None
    for r in runs:
        pairs = {
            (k, v)
            for k, v in r["args"].items()
            if k not in KLD_KEYS and k not in NEUTRAL_KEYS
        }
        shared = pairs if shared is None else shared & pairs
    assert shared is not None
    tokens: list[str] = []
    for key, value, raw in iter_cmd_options(runs[0]["cmd"]):
        if key not in KLD_KEYS and key not in NEUTRAL_KEYS and (key, value) in shared:
            tokens += raw
    return " ".join(tokens)


def _kv_label(ctk: str, ctv: str, tail: int | str, show_tail: bool) -> str:
    """Compact display label for a (ctk, ctv, kv-tail-tokens) combo.

    * A symmetric pair ``qk == qv`` always collapses to a single ``qk``: naming
      the same quant twice says nothing the one mention does not.
    * The `` tN`` suffix appears only where it carries information -- when
      --kv-tail-tokens is passed explicitly somewhere in the log
      (``show_tail``) *and* this run's tail is not 0. A tail of 0 is the
      absence of a tail, so ``t0`` is noise on every label that carries it.
      ``tail`` is the effective value: the explicit one, or the default when
      omitted (128 for KVarN caches, 0 otherwise); it may also be a
      non-numeric beellama tail spec, which is shown verbatim.

    Examples: ``q8_0``, ``q8_0/q4_0``, ``kvarn4 t128``, ``q4_0 t1024``.
    """
    quant = ctk if ctk == ctv else f"{ctk}/{ctv}"
    return f"{quant} t{tail}" if show_tail and str(tail) != "0" else quant


def _model_name(ref: str) -> str:
    """Display form of a model reference: ``-m`` paths shrink to a file name,
    ``--hf-repo`` specs are already short enough to keep whole."""
    if "/" in ref and ":" not in ref:  # a filesystem path, not <repo>:<quant>
        return Path(ref).name.removesuffix(".gguf")
    return ref


# Marker colours, one per author. Slots 1-3 of the reference categorical theme
# plus its violet, in fixed order (never cycled while spare slots remain, never
# reassigned by rank -- they key off the sorted author name, so filtering the
# report cannot repaint the survivors). Validated for a scatter, i.e. over *all*
# pairs rather than adjacent ones, on this report's white card:
#
#   validate_palette.js "#2a78d6,#eb6834,#1baf7a,#4a3aa7" \
#       --mode light --surface "#ffffff" --pairs all
#   worst CVD ΔE 9.2 (deutan), worst normal-vision ΔE 16.3 -- all checks pass.
#
# The one WARN is aqua at 2.82:1 against white, which obliges relief: the report
# carries both permitted forms, a full table of every run and direct labels on
# the plotted points. A fifth author reuses the four hues, so identity moves to
# the marker shape as well -- adding a fifth hue is what fails the floors.
AUTHOR_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7")
#: Secondary channel once the hues run out: (Chart.js pointStyle, matplotlib
#: marker, legend glyph).
AUTHOR_SHAPES = (
    ("circle", "o", ""),
    ("triangle", "^", " ▲"),
    ("rect", "s", " ■"),
    ("rectRot", "D", " ◆"),
)


class AuthorStyle(NamedTuple):
    color: str
    point_style: str  # Chart.js
    marker: str  # matplotlib
    glyph: str  # appended to the legend label when shapes are in play


def _author(ref: str) -> str:
    """Who published a model: the part of the reference before the first ``/``."""
    author, slash, _ = ref.partition("/")
    return author if slash else ""


def _present_authors(runs: list[dict]) -> dict[str, AuthorStyle]:
    """Author -> marker style for the authors actually plotted.

    The style itself was fixed over the *whole* log by :func:`_author_styles`
    and stashed on each run, so filtering the report down to a few authors
    (``--author``) narrows the legend without repainting anyone: a colour means
    the same publisher in every view of the same log.
    """
    return dict(
        sorted(
            (_author(r["model_ref"]), r["author_style"])
            for r in runs
            if r.get("author_style")
        )
    )


#: KV-cache types mainline llama.cpp accepts for -ctk/-ctv. Anything else in a
#: log came from the beellama fork (its kvarnN family, q6_0/q3_0/q2_0, turboN),
#: which is what the sidebar's "deselect non-stock quants" button drops: those
#: runs are unreachable on a stock build.
STOCK_KV_QUANTS = frozenset(
    {"f32", "f16", "bf16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0", "iq4_nl"}
)


def _is_stock(r: dict) -> bool:
    return r["ctk"] in STOCK_KV_QUANTS and r["ctv"] in STOCK_KV_QUANTS


def _model_quant(ref: str) -> str:
    """The quant tag of a model reference, else its name."""
    return ref.partition(":")[2] or _model_name(ref)


def _sidebar_groups(runs: list[dict]) -> list[dict]:
    """The sidebar's three grouping sections, each a list of {name, ids}.

    Authors go alphabetically; the two quant sections go by descending weight,
    which is bits-per-weight for a fixed model -- a KV pair is ranked by
    ``bpw(ctk) + bpw(ctv)`` and a model quant by the size of the shards it
    loaded, since nothing in a GGUF name reliably orders Q4_K_S below Q4_K_M.
    A run appears once in each section, so any run can be reached three ways.
    """

    def section(title, key, rank, note=None):
        groups: dict[str, list[int]] = {}
        weight: dict[str, float] = {}
        for r in runs:
            name = key(r)
            groups.setdefault(name, []).append(r["uid"])
            weight[name] = max(weight.get(name, 0.0), rank(r))
        order = sorted(groups, key=lambda n: (-weight[n], n))
        return {
            "title": title,
            "items": [
                {"name": n, "ids": groups[n], "note": note(weight[n]) if note else ""}
                for n in order
            ],
        }

    # A KV group is ranked and labelled by the cache it actually costs, not by
    # its bits per weight: an exact tail adds bytes without changing the quant,
    # so `q4_0 t1024` has to sort above plain `q4_0` instead of tying with it
    # and landing wherever the name happens to fall. Where no run has a sized
    # cache -- an unrecognised model -- bpw is all there is to rank on, and the
    # figure is left off rather than printed as MiB it is not.
    sized = all(r.get("ctx_mib") is not None for r in runs)
    return [
        section(
            "By author", lambda r: _author(r["model_ref"]) or "(local)", lambda r: 0
        ),
        section(
            "By model quant",
            lambda r: _model_quant(r["model_ref"]),
            lambda r: r["weight_bytes"],
        ),
        section(
            "By KV cache quant",
            lambda r: r["kv_label"],
            (lambda r: r["ctx_mib"]) if sized else (lambda r: r["size"]),
            note=(lambda v: f"{v:,.0f} MiB") if sized else None,
        ),
    ]


def _selected(r: dict, ctk: set[str], ctv: set[str], authors: set[str]) -> bool:
    """Whether a run passes the --cache-type-k / --cache-type-v / --author
    whitelists: OR within a flag, AND between them, and a flag that was not
    passed selects everything. The sets are pre-lowercased; matching ignores
    case so ``--author liquidai`` and ``--cache-type-k Q8_0`` do what they read
    like instead of silently selecting nothing.
    """
    return (
        (not ctk or r["ctk"].lower() in ctk)
        and (not ctv or r["ctv"].lower() in ctv)
        and (not authors or _author(r["model_ref"]).lower() in authors)
    )


def _author_styles(runs: list[dict]) -> dict[str, AuthorStyle]:
    """Marker style per author, or ``{}`` when the sweep has only one.

    Colouring by author is what makes a multi-publisher sweep readable at a
    glance -- whose quant is which -- so it only kicks in when there is more
    than one to tell apart; a single-author sweep keeps the plain per-stat
    colour and gains no legend entries it cannot use.
    """
    authors = sorted({_author(r["model_ref"]) for r in runs})
    if len(authors) < 2:
        return {}
    n = len(AUTHOR_COLORS)
    return {
        a: AuthorStyle(
            AUTHOR_COLORS[i % n], *AUTHOR_SHAPES[i // n % len(AUTHOR_SHAPES)]
        )
        for i, a in enumerate(authors)
    }


def _scatter_by_author(ax, best_pts, sub_pts, styles: dict[str, AuthorStyle]) -> None:
    """Draw the markers of an SVG plot in author colours, grouped by author.

    matplotlib takes one marker per call, so the points are grouped rather than
    styled per-point as Chart.js allows. Frontier points are solid, the rest
    translucent, which keeps "on the frontier" readable independently of hue.
    """
    for pts, alpha, size in ((best_pts, 1.0, 36), (sub_pts, 0.35, 22)):
        by_author: dict[str, list[tuple[float, float]]] = {}
        for x, y, r in pts:
            by_author.setdefault(_author(r["model_ref"]), []).append((x, y))
        for author, xy in by_author.items():
            st = styles[author]
            ax.scatter(
                [p[0] for p in xy],
                [p[1] for p in xy],
                color=st.color,
                marker=st.marker,
                s=size,
                alpha=alpha,
                linewidths=0,
                zorder=6,
            )


def _legend(ax, styles: dict[str, AuthorStyle], **kwargs) -> None:
    """The plot's own legend, plus one entry per author when colouring by it."""
    handles, labels = ax.get_legend_handles_labels()
    if styles:
        from matplotlib.lines import Line2D

        for author, st in styles.items():
            handles.append(
                Line2D(
                    [],
                    [],
                    color=st.color,
                    marker=st.marker,
                    linestyle="none",
                    markersize=6,
                )
            )
            labels.append(author + st.glyph)
    ax.legend(handles, labels, **kwargs)


def _author_quant(ref: str) -> str:
    """``author/model:quant`` -> ``author:quant``, or "" if *ref* is not one.

    The model name is the part a reader least needs: one sweep compares quants
    of one model, so what tells its runs apart is who published each and which
    quant it is.  It is dropped even when it differs between models -- the full
    spec is one hover away in the table.
    """
    repo, colon, quant = ref.partition(":")
    author, slash, _ = repo.partition("/")
    return f"{author}:{quant}" if colon and slash and quant else ""


def _model_labels(refs: set[str]) -> dict[str, str]:
    """Display name per distinct model reference, dropping what they share.

    * quants of one repo -> the quant tag alone (``UD-Q4_K_XL``)
    * several repos -> ``author:quant`` (``LiquidAI:Q8_0``)
    * anything else -> the whole reference (``--model`` paths shortened to a
      file name), which is also the fallback whenever the shorter forms would
      give two different models the same label -- two repos by one author at the
      same quant, say. A label that cannot tell two runs apart is worse than a
      long one.
    """
    full = {ref: _model_name(ref) for ref in refs}
    if not all(ref.partition(":")[2] for ref in refs):
        return full  # an untagged ref or a plain path: nothing to strip
    if len({ref.partition(":")[0] for ref in refs}) == 1:
        return {ref: ref.partition(":")[2] for ref in refs}
    short = {ref: _author_quant(ref) or full[ref] for ref in refs}
    return short if len(set(short.values())) == len(short) else full


def _option_value(value: str | bool | None) -> str:
    """Label form of one option value: a flag that is merely present reads
    ``on``, one that is absent from this run's command line reads ``off``."""
    if value is None:
        return "off"
    if value is True:
        return "on"
    return value


def _build_labels(runs: list[dict], show_tail: bool) -> None:
    """Set ``label`` on every run: what this sweep actually varied, and only that.

    The label is a ``|``-joined list of the varying dimensions -- model, then KV
    cache, then any other option -- because a run is only distinguishable from
    its siblings by what changed between them:

    * KV cache only (the classic sweep) -> ``q8_0/q5_1 t1024``
    * quants of one repo -> ``UD-Q4_K_XL``, the shared repo stripped
    * several repos -> ``LiquidAI:Q8_0`` (see :func:`_model_labels`)
    * both -> ``UD-Q4_K_XL|q8_0/q5_1 t1024``
    * anything else that varies -> ``…|n-cpu-moe=8``

    A ``# LABEL:`` comment in the log (from a combo's ``label:`` key) overrides
    the lot.  When nothing varies -- a single run -- the KV label is used, since
    an empty label would leave the plots unreadable.
    """
    models = {r["model_ref"] for r in runs}
    model_label = _model_labels(models)
    kv = {(r["ctk"], r["ctv"], r["tail_label"]) for r in runs}
    # Every option that is neither the model nor the KV cache nor KLD plumbing,
    # and whose value is not the same on every run. `None` (flag absent here,
    # present elsewhere) counts as a value of its own.
    fixed = {*MODEL_KEYS, *KV_KEYS, *KLD_KEYS, *NEUTRAL_KEYS}
    other = sorted({k for r in runs for k in r["args"]} - fixed)
    varying = [k for k in other if len({r["args"].get(k) for r in runs}) > 1]

    for r in runs:
        r["kv_label"] = _kv_label(r["ctk"], r["ctv"], r["tail_label"], show_tail)
        parts = []
        if len(models) > 1:
            parts.append(model_label[r["model_ref"]])
        if len(kv) > 1:
            parts.append(r["kv_label"])
        parts += [f"{k}={_option_value(r['args'].get(k))}" for k in varying]
        if not parts:
            parts = [r["kv_label"]]
        r["label"] = r["log_label"] or "|".join(parts)


def parse_log(
    path: str, n_parallel: int = 4, projected_ctx: int | None = None
) -> tuple[list[dict], str, int]:
    """Parse the log. ``projected_ctx`` is the context the KV cache (MiB)
    column is evaluated at; when None it defaults to the run's own context
    size: the logits (baseline-generating) run's ``--ctx-size``, else any
    run's, else 256k. Returns (runs, common_params, projected_ctx)."""
    runs = []
    log_ctx: int | None = None  # ctx of the logits (baseline-generating) run
    any_ctx: int | None = None  # ctx of any run (fallback)
    unsized_tails: set[str] = set()
    for lr in iter_runs(Path(path).read_text()):
        args = lr.args
        ctk = _cache_type(args.get("cache-type-k"))
        ctv = _cache_type(args.get("cache-type-v"))
        # Omitting --kv-tail-tokens defaults to 128 for KVarN caches (KVarN
        # keeps a 128-token f16 tail by default), 0 otherwise.
        is_kvarn = ctk.startswith("kvarn") or ctv.startswith("kvarn")
        spec = args.get("kv-tail-tokens")
        spec = spec if isinstance(spec, str) else ""
        # beellama takes a SPEC here, not just a token count: `auto`, a
        # positional list, a named group list. Only a plain count can be sized,
        # so anything else is shown verbatim in the label and sized as if the
        # flag were absent -- a wrong number would be worse than an honest one.
        tail = int(spec) if spec.isdigit() else (128 if is_kvarn else 0)
        tail_label = spec or str(tail)
        if spec and not spec.isdigit():
            unsized_tails.add(spec)

        ctx_size = int(args["ctx-size"]) if isinstance(args.get("ctx-size"), str) else 0
        if ctx_size and any_ctx is None:
            any_ctx = ctx_size

        lines = lr.lines
        model_ref = _model_ref(args)
        shards = _run_shards(lr.weight_files, model_ref)

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
        elif total_minutes is not None and n_chunks_tmp > 0:
            speed = (n_chunks_tmp * ctx_size) / (total_minutes * 60)

        run = {
            # The run's identity, for labels, hovers and the weight half of the
            # cost axis. ``sig`` ignores the KLD switches, so the logits run and
            # its rerun share one.
            "cmd": lr.cmd,
            "args": args,
            "sig": lr.signature,
            "model_ref": model_ref,
            "log_label": lr.label,
            "weight_files": shards,
            # Summed from the same filtered set the hover lists, never from the
            # raw provenance: one source of truth or the two drift apart.
            "weight_bytes": sum(f.size for f in shards.values()),
            "ctk": ctk,
            "ctv": ctv,
            "tail": tail,
            "tail_label": tail_label,
            "label": "",  # filled in by _build_labels once the log is whole
            "size": resolve_bpw(ctk) + resolve_bpw(ctv),
            "n_chunks": 0,
            "mean": None,
            "p999": None,
            "top1": None,
            "top1_tol": None,
            "coll": None,
            "coll_tol": None,
            "ppl_cor": None,
            "speed": speed,
        }

        if not lr.has_kld:
            # Logits-generating run — no KLD stats of its own. The baseline
            # KLD rerun (same combo + --kl-divergence) carries the measured
            # baseline numbers; both are flagged in the post-pass below.
            if ctx_size and log_ctx is None:
                log_ctx = ctx_size
            runs.append(run | {"logits": True})
            continue

        # Count chunks for display; parse summary for statistics
        # Find last chunk's index (not count — chunks 2-575 may be collapsed to [...])
        n_chunks = 0
        for ln in reversed(lines):
            m = re.match(r"^\s*(\d+)", ln)
            if m and "±" in ln:
                n_chunks = int(m.group(1))
                break
        stats = {"mean": 0.0, "p999": 0.0}
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
            elif key == "Mean":
                stats["mean"] = val

        chunk = "\n".join(lines)
        top_m = TOP_P_RE.search(chunk)
        coll_m = COLL_P_RE.search(chunk)
        cor_m = PPL_COR_RE.search(chunk)

        if not in_summary:
            # Aborted run — no KLD stats. Still list in table with blank metrics.
            if lr.aborted:
                runs.append(run | {"aborted": True})
                continue
            print(
                f"WARNING: --kl-divergence flag found but no summary stats for {ctk}/{ctv}",
                file=sys.stderr,
            )
            continue

        runs.append(
            run
            | {
                "n_chunks": n_chunks,
                "mean": stats["mean"],
                "p999": stats["p999"],
                "top1": float(top_m.group(1)) if top_m else None,
                "top1_tol": float(top_m.group(2)) if top_m else None,
                "coll": float(coll_m.group(1)) if coll_m else None,
                "coll_tol": float(coll_m.group(2)) if coll_m else None,
                "ppl_cor": float(cor_m.group(1)) if cor_m else None,
            }
        )

    # Baseline identification. The logits-generating run (``logits`` flag) has
    # no KLD stats; the baseline is the KLD rerun of the same combo, which
    # carries the measured numbers (KLD noise floor, top-1) and the
    # reference speed without the logits-dump I/O. Old logs have no rerun:
    # the logits run itself stays the baseline (speed reference only, blank
    # KLD stats).
    logits_run = next((r for r in runs if r.get("logits")), None)
    if logits_run is not None:
        rerun = next(
            (
                r
                for r in runs
                if not r.get("logits")
                and not r.get("aborted")
                and r["sig"] == logits_run["sig"]
            ),
            None,
        )
        if rerun is not None:
            rerun["baseline"] = True
        else:
            logits_run["baseline"] = True

    # Labels depend on the whole log: only what varies across it is shown, and
    # the `` tN`` suffix appears on every run iff --kv-tail-tokens is passed
    # explicitly somewhere. Its absence is a default (128 for KVarN, 0
    # otherwise), so keying off non-zero tails would wrongly show suffixes for a
    # KVarN-only log where the flag never appears.
    if unsized_tails:
        print(
            "WARNING: --kv-tail-tokens "
            + ", ".join(sorted(unsized_tails))
            + " is not a plain token count -- those runs are sized in the KV "
            "cache (MiB) column as if they had no exact tail",
            file=sys.stderr,
        )
    show_tail = any("kv-tail-tokens" in r["args"] for r in runs)
    _build_labels(runs, show_tail)
    # Fixed here, over every run in the log, so that a later --author /
    # --cache-type-* filter cannot change what a colour means.
    styles = _author_styles(runs)
    for r in runs:
        r["author_style"] = styles.get(_author(r["model_ref"]))
    common_params = _common_params(runs)

    # Projected context for the KV-cache (MiB) column: explicit --ctx-size,
    # else the run's own context (logits run first, any run as fallback).
    if projected_ctx is None:
        projected_ctx = log_ctx or any_ctx or DEFAULT_CTX_SIZE

    # KV-cache size at the projected context, per run: a sweep may span several
    # models, and only the hand-curated geometries in MODEL_KV can be sized.
    specs: dict[str, tuple[str, ModelKV] | None] = {}
    for r in runs:
        ref = r["model_ref"]
        if ref not in specs:
            specs[ref] = resolve_model(ref)
            if specs[ref] is None:
                print(
                    f"WARNING: no KV-cache model matched '{ref or '(none)'}' -- "
                    "KV cache (MiB) column left empty for its runs",
                    file=sys.stderr,
                )
        if (match := specs[ref]) is not None:
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
    _assign_vram(runs)

    return runs, common_params, projected_ctx


def _cache_type(value: str | bool | None) -> str:
    """A ``-ctk``/``-ctv`` option as a type name; absent means llama.cpp's f16."""
    return value if isinstance(value, str) else "f16"


def _run_shards(files: dict[str, ModelFile], model_ref: str) -> dict[str, ModelFile]:
    """The shards of *model_ref* among those the log recorded for one run.

    Logs written before the sweeper matched quant tags on token boundaries name
    a sibling quant next to the right one -- `:Q6_K` also resolved `Q6_K_L` --
    and summing both reports one model as two. Re-applying the rule here fixes
    those logs without re-running them.

    Everything is kept when the tag matches nothing, which is the honest answer
    for an untagged `-hf`, a `--model` path, or a repo whose file names do not
    carry the tag: there the recorded set is all the log knows.
    """
    tag = model_ref.partition(":")[2]
    if not tag:
        return files
    return {name: f for name, f in files.items() if quant_matches(f.path, tag)} or files


def _model_ref(args: dict[str, str | bool]) -> str:
    """The model a run loaded: its ``--hf-repo`` spec, else its ``--model`` path."""
    for key in MODEL_KEYS:
        if isinstance(ref := args.get(key), str):
            return ref
    return ""


def _assign_vram(runs: list[dict]) -> None:
    """Set ``weights_mib`` and ``vram_mib`` (weights + KV cache) on every run.

    Both are all-or-nothing across the log, because they feed the cost axis: a
    row silently missing its weights would sit at the wrong x and could displace
    a real point from the Pareto frontier.  Weights come from the ``# model:``
    provenance, so a log written before the sweeper recorded it -- or one whose
    model never resolved to a known geometry -- simply falls back to the coarser
    cost metric (see ``_cost_axis``).
    """
    for r in runs:
        r["weights_mib"] = r["weight_bytes"] / BYTES_PER_MIB or None
    known = [r for r in runs if r["weights_mib"] is not None]
    if known and len(known) != len(runs):
        print(
            f"WARNING: model provenance missing for {len(runs) - len(known)} of "
            f"{len(runs)} runs -- weights left out of the cost axis",
            file=sys.stderr,
        )
        for r in runs:
            r["weights_mib"] = None
        known = []
    for r in runs:
        r["vram_mib"] = (
            r["weights_mib"] + r["ctx_mib"]
            if len(known) == len(runs) and r["ctx_mib"] is not None
            else None
        )


def _cost_axis(runs: list[dict], ctx_label: str = "256k") -> tuple[str, str, str]:
    """Pick the frontier / plot-x cost metric: the most informative one every
    run can supply. Returns (cost_key, x_axis_label, frontier_marker).

    * ``vram_mib`` -- model weights (from the log's ``# model:`` provenance) plus
      the KV cache at ``ctx_label``. The only honest cost when the sweep varies
      the model quantization, since all quants of one model share a KV geometry
      and would otherwise pile up at the same x.
    * ``ctx_mib`` -- KV cache alone, when the weights are unrecorded. Tail
      variants that share a bpw still separate out.
    * ``weights_mib`` -- weights alone, when the model has no curated geometry.
    * ``size`` -- bpw(ctk) + bpw(ctv), the last resort.

    Frontier points are drawn solid black on every metric but the last.
    """
    for key, label in (
        ("vram_mib", f"VRAM (MiB) @ {ctx_label}"),
        ("ctx_mib", f"Context size (MiB) @ {ctx_label}"),
        ("weights_mib", "Weights (MiB)"),
    ):
        if runs and all(r.get(key) is not None for r in runs):
            return key, label, "⚫"  # black circle
    return "size", "Size (bits / weight)", "\U0001f7e2"  # green circle


def _x_range(runs: list[dict], cost_key: str) -> tuple[float, float]:
    """X-axis range with 5% padding on both sides.

    Not anchored at 0: once the model weights are in the cost, every point sits
    at some tens of GiB and a zero-based axis would squeeze the whole sweep into
    a sliver at the right edge.
    """
    costs = [r[cost_key] for r in runs]
    lo, hi = min(costs), max(costs)
    pad = (hi - lo or hi or 1.0) * 0.05
    return max(0.0, lo - pad), hi + pad


# Plotted statistics: key -> (display label, colour). Mean / 99.9% KLD read
# lower-is-better and top-1 / same-sampled / perplexity / speed
# higher-is-better, but nothing branches on that: the frontier is the mean-KLD
# one on every chart (see _stat_frontier).
STAT_STYLE = {
    "mean": ("Mean KLD", "#e74c3c"),
    "p999": ("99.9% KLD", "#9b59b6"),
    "ppl_cor": ("Cor(ln(PPL(Q)), ln(PPL(base)))", "#2980b9"),
    "top1": ("Top-1 (%)", "#e67e22"),
    # Sits next to top1 in the fixed hue order; #c2185b is the only candidate
    # that clears the CVD and normal-vision floors against carrot (ΔE 19.4
    # deutan / 22.7 normal) without colliding with any other slot.
    "coll": ("Same sampled (%)", "#c2185b"),
    # Speed is the one cost-like stat here, and it never shares a chart with the
    # accuracy ones, so it takes a deliberately neutral slate rather than
    # competing for a hue in the sequence above.
    "speed": ("Speed (tok/s)", "#546e7a"),
}
# Run-dict key holding the ± tolerance for stats that have one.
# Plot titles (h2 in HTML, figure title in SVG). Legend/dataset labels stay
# the short STAT_STYLE names.
STAT_TITLE = {
    "mean": "Mean KL Divergence (linear scale)",
    "p999": "99% KL Divergence (linear scale)",
    "ppl_cor": "Perplexity (%)",
    "top1": "Top-1 (%)",
    "coll": "Same sampled token, temp 1 (%)",
    "speed": "Speed (tok/s)",
}
STAT_TOL_KEY = {"top1": "top1_tol", "coll": "coll_tol"}
# Decimal places for table cells and chart tooltips (stats with a tolerance).
STAT_DECIMALS = {"top1": 3, "coll": 3, "ppl_cor": 2, "speed": 1}
# Suffix of the extra per-stat SVG files: BASENAME.<suffix>.svg
STAT_SVG_SUFFIX = {
    "mean": "mean-kld",
    "p999": "p999-kld",
    "ppl_cor": "ppl",
    "top1": "top1",
    "coll": "same-sampled",
    "speed": "speed",
}


# Guide lines tracing "hold one side of the cache exact, vary everything else".
_REFERENCE_LINES = (
    ("k=f16", "f16 K", lambda r: r["ctk"] == "f16"),
    ("v=f16", "f16 V", lambda r: r["ctv"] == "f16"),
)


def _reference_subsets(runs: list[dict]) -> list[tuple[str, str, list[dict]]]:
    """The ``f16`` guide lines worth drawing over *runs*, as (key, name, subset).

    Each connects the runs whose K (or V) cache is exact, in cost order, so a
    sweep that varies both the model and the cache can be read as "what does
    this model cost at an untouched KV cache?" against the solid frontier's
    "what does it cost at the best cache quant?".

    A subset covering every plotted run traces the main series itself -- which
    is what happens whenever the sweep leaves the cache at f16/f16 and varies
    something else, a model-quant sweep being the obvious case -- and a v=f16
    subset equal to the k=f16 one is that same line twice.  Either way the extra
    dataset only stacks a duplicate point on top of an existing one, and since
    Chart.js' ``nearest`` mode returns *every* dataset sharing the pixel, each
    duplicate becomes another identical row under the cursor.  Drop them -- but
    when the two coincide the survivor is renamed, since it holds both sides
    exact and calling it the K line alone would understate it.
    """
    plotted = [r for r in runs if not r.get("aborted")]
    out: list[tuple[str, str, list[dict]]] = []
    seen: dict[frozenset[int], int] = {}
    for key, name, pred in _REFERENCE_LINES:
        subset = [r for r in plotted if pred(r)]
        ids = frozenset(id(r) for r in subset)
        if not subset or len(subset) == len(plotted):
            continue
        if (i := seen.get(ids)) is not None:
            out[i] = (out[i][0], "f16 KV", out[i][2])
            continue
        seen[ids] = len(out)
        out.append((key, name, subset))
    return out


def _guide_frontier(
    subset: list[dict], cost_key: str, exclude: frozenset[int] | set[int] = frozenset()
) -> list[dict]:
    """The Pareto frontier *within* a guide subset, by the rule the report's own
    frontier uses (:func:`_stat_frontier`), in cost order.

    Joining every run of the subset instead makes the line zigzag: two models at
    nearly the same VRAM rarely have the same KLD, so it walks up and down
    between them rather than tracing the best of them -- which is precisely what
    the solid line beside it does, and what it is there to be compared against.
    The runs it drops are still plotted; they belong to the main series too.
    """
    eligible = [r for r in subset if not r.get("aborted") and id(r) not in exclude]
    ids = _stat_frontier(eligible, cost_key)
    return [r for r in subset if id(r) in ids]


#: matplotlib dash pattern per guide line (Chart.js takes its own, inline).
_MPL_REF_DASH = {"k=f16": (0, (6, 3)), "v=f16": (0, (2, 3))}
#: Legend text. The solid series is the Pareto frontier over every run, so it
#: rides whichever cache quant wins at each cost; a guide line holds one fixed.
#: The qualifier is only added when there is a guide line to tell it apart from.
FRONTIER_QUALIFIER = "best quant"


def _series_label(stat_label: str, qualifier: str = "") -> str:
    return f"{stat_label} @ {qualifier}" if qualifier else stat_label


#: The stat whose Pareto frontier is *the* frontier, on every plot.
FRONTIER_STAT = "mean"


def _stat_frontier(candidate_runs: list[dict], cost_key: str) -> set[int]:
    """The report's Pareto frontier: lowest mean KLD at each cost.

    Grouped by cost so that equal-cost runs compete; runs without a mean KLD
    are skipped.

    This one set is marked on *every* chart, which is why the other plots show
    points beyond their own optimum -- the fastest run at a given cost need not
    be the most accurate one. That is deliberate: the frontier answers "which
    run should I use?", a question about output quality, and each other plot
    then shows what that choice costs on its own axis.
    """
    from collections import defaultdict

    by_cost: dict[float, list[dict]] = defaultdict(list)
    for r in candidate_runs:
        if r[FRONTIER_STAT] is None:
            continue
        by_cost[r[cost_key]].append(r)
    ids = set()
    best = float("inf")
    for s in sorted(by_cost):
        lowest = min(r[FRONTIER_STAT] for r in by_cost[s])
        if lowest < best:
            ids.update(id(r) for r in by_cost[s] if r[FRONTIER_STAT] == lowest)
            best = lowest
    return ids


def _fmt_tol(v, tol, decimals):
    """Format a value with its ± tolerance (e.g. ``6.1129 ± 0.0383``), or
    blank if the value is None (aborted / unparsed run)."""
    if v is None:
        return ""
    if tol is None:
        return f"{v:.{decimals}f}"
    return f"{v:.{decimals}f} ± {tol:.{decimals}f}"


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


def _fmt_mib(v) -> str:
    """Format a MiB figure for a table cell, or blank if unknown."""
    return "" if v is None else f"{v:,.0f}"


def _weights_cell(r: dict) -> str:
    """Weights (MiB) cell, hovering the shards it was summed from."""
    note = "\n".join(
        f"{f.path} {f.size / BYTES_PER_MIB:,.0f} MiB"
        for f in r["weight_files"].values()
    )
    return f'<td class="hint" title="{_esc(note)}">{_fmt_mib(r["weights_mib"])}</td>'


def _vram_cell(r: dict) -> str:
    """VRAM (MiB) cell: weights + KV cache, the cost axis itself."""
    note = (
        f"weights {_fmt_mib(r['weights_mib'])} MiB + "
        f"KV cache {_fmt_mib(r['ctx_mib'])} MiB"
    )
    return f'<td class="hint" title="{_esc(note)}">{_fmt_mib(r["vram_mib"])}</td>'


def generate_html(
    runs: list[dict],
    common_params: str = "",
    chart_js_src: str = "",
    speed_cutoff_factor: float = 0.33,
    n_parallel: int = 4,
    ctx_label: str = "256k",
) -> str:
    # Cost metric for the frontier and x-axis: weights + KV cache if the log
    # records both, KV cache or weights alone if only one, else bpw.
    cost_key, x_axis_label, _ = _cost_axis(runs, ctx_label)

    # "Same sampled p" is optional (patched llama-perplexity only): drop the
    # column entirely rather than showing one full of blanks. Likewise the
    # weight columns, which need the `# model:` provenance.
    has_coll = any(r.get("coll") is not None for r in runs)
    has_weights = all(r.get("weights_mib") is not None for r in runs)
    author_style = _present_authors(runs)

    # The baseline (KLD rerun against its own logits) is plotted like any
    # other run and joins the frontier line when it is on the frontier. A
    # stats-less baseline (old log: the logits-generating run) and the logits
    # run itself have nothing to plot.
    sorted_runs = sorted(
        (
            r
            for r in runs
            if not r.get("logits")
            and (not r.get("baseline") or r.get("mean") is not None)
        ),
        key=lambda r: r[cost_key],
    )

    # ---- Pareto frontiers (separate per stat; group by cost so equal-cost runs compete) ----
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
        and not r.get("logits")
        and (speed_cutoff is None or r["speed"] is None or r["speed"] >= speed_cutoff)
    ]

    frontier = _stat_frontier(eligible_runs, cost_key)
    # Runs that fail the speed cutoff (used by the per-stat plots, whose
    # frontier semantics are per-stat rather than the combined KLD one).
    slow_ids = {
        id(r)
        for r in runs
        if speed_cutoff is not None
        and r["speed"] is not None
        and r["speed"] < speed_cutoff
    }

    # A run is suboptimal if it's NOT on either frontier, OR if it's too slow.
    # The baseline and logits runs are excluded: they are not frontier
    # candidates but must not be greyed out in the table either.
    suboptimal_ids = {
        id(r)
        for r in runs
        if not r.get("baseline") and not r.get("logits") and id(r) not in frontier
    }
    if speed_cutoff is not None:
        for r in runs:
            if r["speed"] is not None and r["speed"] < speed_cutoff:
                suboptimal_ids.add(id(r))

    # ---- selection: every plotted run gets an id the page can address ----
    # The logits run is left out: it has no point on any chart, so there is
    # nothing to select. Default selection is the frontier, i.e. what "select
    # none" then "select frontier" would give.
    selectable = [r for r in runs if not r.get("logits")]
    for n, r in enumerate(selectable):
        r["uid"] = n
    sidebar_sections = _sidebar_groups(selectable)
    run_meta = {
        r["uid"]: {"label": r["label"], "stock": _is_stock(r)} for r in selectable
    }
    frontier_uids = [r["uid"] for r in selectable if id(r) in frontier]

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
        elif r.get("logits"):
            label += " (logits)"
        elif r.get("aborted"):
            label += " (aborted)"
        # Grey follows the *selection*, which the page owns; the row only
        # carries the id it toggles.
        cls = f' data-id="{r["uid"]}" class="run-row"' if "uid" in r else ""
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}" if r["speed_pct"] is not None else "N/A"
        if r.get("ctx_mib") is not None:
            ctx_cell = (
                f'<td class="hint" title="{_esc(r["ctx_note"])}">'
                f"{r['ctx_mib']:,.0f}</td>"
            )
        else:
            ctx_cell = "<td></td>"
        # A label only carries what the sweep varied, so the hover carries the
        # rest: the run's command line, verbatim.
        tbl_rows += (
            f"<tr{cls}>"
            f'<td class="hint" title="{_esc(r["cmd"])}">{_esc(label)}</td>'
            f"<td>{_fmt_size(r['size'])}</td>"
            + (_weights_cell(r) if has_weights else "")
            + f"{ctx_cell}"
            + (_vram_cell(r) if has_weights else "")
            + f"<td>{_fmt(r['mean'])}</td>"
            f"<td>{_fmt(r['p999'])}</td>"
            f"<td>{_fmt_tol(r.get('top1'), r.get('top1_tol'), 3)}</td>"
            + (
                f"<td>{_fmt_tol(r.get('coll'), r.get('coll_tol'), 3)}</td>"
                if has_coll
                else ""
            )
            + f"<td>{_fmt_tol(r.get('ppl_cor'), None, 2)}</td>"
            f"<td>{speed_fmt}</td>"
            f"<td>{pct_fmt}</td>"
            f"</tr>\n"
        )

    # ---- chart datasets ----
    # Every point carries ``_frontier``: whether its label survives the
    # "hide labels of non-frontier points" toggle. The combined KLD log chart
    # hides labels via the shared mean/p999 suboptimal set (historical
    # behaviour); the per-stat linear charts hide everything not on that
    # stat's own frontier (or too slow).
    def _point(r, key, clamp_log, tol_key):
        y = r[key]
        if clamp_log and y <= 0:
            y = 1e-10
        return {
            "x": r[cost_key],
            "y": y,
            # The run this point stands for. Selection is per run, so the same
            # id appears in every chart and on the table row, and one click
            # anywhere repaints all of them.
            "_id": r["uid"],
            "_label": r["label"],
            "_speed": r["speed"],
            "_speed_pct": r["speed_pct"],
            "_tol": r.get(tol_key) if tol_key else None,
        }

    def _marks(rs, color):
        """Per-point marker styling.

        ``_full``/``_faint`` are the selected and deselected colours of each
        point; the page picks between them at draw time from the live
        selection, which is why they are arrays here and not a single colour.
        One hue per author when the sweep has several (and one shape per author
        past the fourth); otherwise the series keeps its stat colour.
        """
        colors = [
            author_style[_author(r["model_ref"])].color if author_style else color
            for r in rs
        ]
        d = {
            "_full": colors,
            "_faint": [c + "2e" for c in colors],
        }
        if author_style:
            d["pointStyle"] = [
                author_style[_author(r["model_ref"])].point_style for r in rs
            ]
        return d

    def _line_ds(label, pts, color, rs, extra=None):
        d = {
            "label": label,
            "data": pts,
            "borderColor": color,
            "backgroundColor": color,
            "showLine": True,
            "fill": False,
            "tension": 0,
            "pointRadius": 6,
            "pointHoverRadius": 8,
            **_marks(rs, color),
        }
        if extra:
            d.update(extra)
        return d

    def _sub_ds(label, pts, color, rs, extra=None):
        d = {
            "label": label,
            "data": pts,
            "borderColor": color,
            "backgroundColor": color,
            "showLine": False,
            "fill": False,
            "pointRadius": 5,
            "pointHoverRadius": 7,
            "_sub": True,
            **_marks(rs, color),
        }
        if extra:
            d.update(extra)
        return d

    def _faint(color):
        r_, g_, b_ = (
            int(color[1:3], 16),
            int(color[3:5], 16),
            int(color[5:7], 16),
        )
        return f"rgba({r_}, {g_}, {b_}, 0.35)"

    def _build_datasets(stat_keys, clamp_log, combined):
        """Datasets for one chart over ``stat_keys``.

        The split is structural -- which points the frontier *line* joins --
        and fixed at build time. How a point *looks* is not: its colour and its
        label follow the live selection, which the page resolves per point
        through ``_full``/``_faint`` and ``_id``.
        """
        hidden = suboptimal_ids if combined else slow_ids
        guides = _reference_subsets(sorted_runs)
        ds = []
        for key in stat_keys:
            label, color = STAT_STYLE[key]
            tol_key = STAT_TOL_KEY.get(key)
            best_pts, sub_pts, best_rs, sub_rs = [], [], [], []
            for r in sorted_runs:
                if r.get("aborted") or r[key] is None:
                    continue
                pt = _point(r, key, clamp_log, tol_key)
                if id(r) in frontier and id(r) not in hidden:
                    best_pts.append(pt)
                    best_rs.append(r)
                else:
                    sub_pts.append(pt)
                    sub_rs.append(r)
            solid = _series_label(label, FRONTIER_QUALIFIER if guides else "")
            ds.append(_line_ds(solid, best_pts, color, best_rs))
            ds.append(_sub_ds(solid, sub_pts, color, sub_rs))
        # Extra lines: k=f16 (dashed) and v=f16 (dotted)
        for subset_key, subset_name, subset in guides:
            dash_pat = {"k=f16": [6, 3], "v=f16": [2, 3]}[subset_key]
            guide_runs = _guide_frontier(subset, cost_key, slow_ids)
            for key in stat_keys:
                label, color = STAT_STYLE[key]
                tol_key = STAT_TOL_KEY.get(key)
                pts, line_rs = [], []
                for r in guide_runs:
                    if r[key] is None:
                        continue
                    pts.append(_point(r, key, clamp_log, tol_key))
                    line_rs.append(r)
                if not pts:
                    continue
                # Named in the legend, next to the solid series it shadows:
                # a dashed line running below the frontier is unreadable
                # otherwise.
                ds.append(
                    _line_ds(
                        _series_label(label, subset_name),
                        pts,
                        _faint(color),
                        line_rs,
                        {
                            "pointRadius": 4,
                            "pointHoverRadius": 6,
                            "borderDash": dash_pat,
                        },
                    )
                )
        # Colour key: dataless series carrying one legend entry per author.
        for author, st in author_style.items():
            ds.append(
                {
                    "label": author + st.glyph,
                    "data": [],
                    "borderColor": st.color,
                    "backgroundColor": st.color,
                    "showLine": False,
                    "pointStyle": st.point_style,
                }
            )
        return ds

    # Compute y-axis range so smallest non-zero point sits at 1/3 from bottom
    # (a zero/negative KLD clamps to y=1e-10 and shoots out of plot).
    # The baseline is ignored for the range: its near-zero measured KLD would
    # drag the bottom of the log scale far below every other point.
    max_y = max(
        r[key]
        for key in ["mean", "p999"]
        for r in sorted_runs
        if not r.get("baseline") and r[key] is not None and r[key] > 0
    )
    min_nonzero = min(
        r[key]
        for key in ["mean", "p999"]
        for r in sorted_runs
        if not r.get("baseline") and r[key] is not None and r[key] > 0
    )
    import math

    # Position min_nonzero at 1/10 from bottom of log scale (90% down):
    # (log(min_nonzero) - log(y_min)) / (log(max_y) - log(y_min)) = 0.1
    log_max = math.log10(max_y)
    log_min = math.log10(min_nonzero)
    log_y_min = (10 * log_min - log_max) / 9
    y_min = 10**log_y_min
    y_max = max_y * 1.15  # 15% headroom above max
    x_min, x_max = _x_range(sorted_runs, cost_key)

    baseline_label = next((r["label"] for r in runs if r.get("baseline")), None)
    baseline_label_json = json.dumps(baseline_label) if baseline_label else "null"

    # ---- assemble the five charts ----
    # All charts share the same X range so they stay vertically aligned.
    def _lin_range(key, with_tol=False):
        vals = []
        for r in sorted_runs:
            if r.get("aborted") or r[key] is None:
                continue
            tol = r.get(STAT_TOL_KEY.get(key, ""), 0) or 0 if with_tol else 0
            vals.append((r[key] - tol, r[key] + tol))
        lo = min(v[0] for v in vals)
        hi = max(v[1] for v in vals)
        span = hi - lo or abs(hi) * 0.1 or 1.0
        return lo - span * 0.1, hi + span * 0.1

    chart_defs = [
        {
            "id": "chartLog",
            "title": "KL Divergence (log scale)",
            "yType": "logarithmic",
            "yTitle": "KL Divergence",
            "yMin": y_min,
            "yMax": y_max,
            "decimals": 6,
            "errorBars": False,
            "baselineNote": True,
            "datasets": _build_datasets(["mean", "p999"], True, True),
        }
    ]
    for key, cid in [("mean", "chartMean"), ("p999", "chartP999")]:
        hi = max(
            r[key] for r in sorted_runs if not r.get("aborted") and r[key] is not None
        )
        chart_defs.append(
            {
                "id": cid,
                "title": STAT_TITLE[key],
                "yType": "linear",
                "yTitle": STAT_STYLE[key][0],
                "yMin": 0,
                "yMax": hi * 1.15,
                "decimals": 6,
                "errorBars": False,
                "baselineNote": False,
                "datasets": _build_datasets([key], False, False),
            }
        )
    # Short tooltip name + unit suffix for percent-scale stats (the series
    # label of ppl_cor is too long for a tooltip prefix).
    TOOLTIP_NAME_UNIT = {
        "top1": ("Top-1", "%"),
        "coll": ("Same sampled", "%"),
        "ppl_cor": ("Perplexity", "%"),
        "speed": ("Speed", " tok/s"),
    }
    for key, cid in [
        ("top1", "chartTop1"),
        ("coll", "chartColl"),
        ("ppl_cor", "chartPplCor"),
        ("speed", "chartSpeed"),
    ]:
        if not any(not r.get("aborted") and r[key] is not None for r in sorted_runs):
            continue
        lo, hi = _lin_range(key, with_tol=True)
        tt_name, tt_unit = TOOLTIP_NAME_UNIT.get(key, (None, ""))
        chart_defs.append(
            {
                "id": cid,
                "title": STAT_TITLE[key],
                "yType": "linear",
                "yTitle": STAT_STYLE[key][0],
                "yMin": lo,
                "yMax": hi,
                "decimals": STAT_DECIMALS[key],
                "errorBars": key in STAT_TOL_KEY,
                "baselineNote": False,
                "tooltipLabel": tt_name,
                "unit": tt_unit,
                # Every other chart appends the run's speed to its tooltip; on
                # the speed chart that is the y value all over again.
                "showSpeed": key != "speed",
                "datasets": _build_datasets([key], False, False),
            }
        )

    def zoom_btn(cid, axis, direction, tip):
        glyph = "+" if direction == "in" else "\u2212"
        return (
            f'<button class="zoom-btn" data-chart="{cid}" data-axis="{axis}"'
            f' data-dir="{direction}" title="{tip}">{glyph}</button>'
        )

    containers_html = ""
    for d in chart_defs:
        cid = d["id"]
        containers_html += (
            '<div class="chart-container">\n'
            f'  <h2 class="chart-title">{_esc(d["title"])}</h2>\n'
            '  <div class="chart-box">\n'
            # Beside the rotated Y title, and under the X one: each pair zooms
            # its own axis and leaves the other alone.
            '    <div class="zoom-y">'
            + zoom_btn(cid, "y", "in", "Zoom in on Y (this plot)")
            + zoom_btn(cid, "y", "out", "Zoom out on Y (this plot)")
            + "</div>\n"
            f'    <canvas id="{cid}" width="1000" height="600"></canvas>\n'
            '    <div class="zoom-x">'
            + zoom_btn(cid, "x", "out", "Zoom out on X (all plots)")
            + zoom_btn(cid, "x", "in", "Zoom in on X (all plots)")
            + "</div>\n"
            "  </div>\n"
            "</div>\n"
        )

    # Chunk count — same for all non-baseline runs
    n_chunks = next(
        (r["n_chunks"] for r in sorted_runs if r.get("n_chunks", 0) > 0), None
    )
    chunks_html = (
        (f'<div class="common-params"><h2>Chunks per run</h2>{n_chunks}</div>\n')
        if n_chunks
        else ""
    )

    sidebar_html = "".join(
        f'<h3>{_esc(sec["title"])}</h3>\n<ul class="tri-list">\n'
        + "".join(
            f'  <li class="tri-row" data-ids="{",".join(str(i) for i in item["ids"])}">'
            f'<span class="tri"></span>{_esc(item["name"])}'
            + (
                f'<span class="tri-note">({_esc(item["note"])})</span>'
                if item["note"]
                else ""
            )
            + "</li>\n"
            for item in sec["items"]
        )
        + "</ul>\n"
        for sec in sidebar_sections
    )

    html = (
        HTML_HEAD.replace("{chart_js_src}", chart_js_src)
        .replace("{sidebar}", sidebar_html)
        .replace("{ctx_label}", _esc(ctx_label))
        .replace("{coll_th}", "  <th>Same sampled (%)</th>\n" if has_coll else "")
        .replace("{weights_th}", "  <th>Weights (MiB)</th>\n" if has_weights else "")
        .replace(
            "{vram_th}",
            f"  <th>VRAM (MiB) @{_esc(ctx_label)}</th>\n" if has_weights else "",
        )
    )
    html += common_html
    html += chunks_html
    html += tbl_rows
    html += HTML_TABLE_END
    html += containers_html
    html += (
        HTML_SCRIPT.replace("{chart_defs_json}", json.dumps(chart_defs))
        .replace("{run_meta_json}", json.dumps(run_meta))
        .replace("{frontier_json}", json.dumps(frontier_uids))
        .replace("{x_min_json}", json.dumps(x_min))
        .replace("{x_max_json}", json.dumps(x_max))
        .replace("{x_axis_label_json}", json.dumps(x_axis_label))
        .replace("{baseline_label_json}", baseline_label_json)
    )
    if speed_cutoff is not None:
        pct = speed_cutoff_factor * 100
        html += f'<p class="note">Runs with speed &lt; {pct:.0f}% of baseline excluded from frontier determination.</p>\n'
    html += (
        '<p class="note">Hover a run label for its full command line: a label '
        "shows only what this sweep varied.</p>\n"
    )
    if any(r.get("ctx_mib") is not None for r in runs):
        html += (
            f'<p class="note">KV cache (MiB) @{_esc(ctx_label)} is the estimated '
            f"beellama v0.4.1 llama-server KV-cache VRAM at a {_esc(ctx_label)} "
            f"context with n_parallel={n_parallel} (source-modelled, not yet "
            "log-validated); hover a cell "
            "for the per-layer-group breakdown.</p>\n"
        )
    if has_weights:
        html += (
            '<p class="note">Weights (MiB) is the on-disk size of the shards the '
            "run loaded, from the log's own provenance; VRAM (MiB) adds the KV "
            "cache to it and is the x axis of every plot.</p>\n"
        )
    html += HTML_TAIL
    return html


HTML_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KLD Effect of Quantization</title>
{chart_js_src}
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 0; background: #f8f9fa; color: #222; }
  .layout { display: flex; align-items: flex-start; gap: 20px; padding: 20px; }
  /* Sticky, and scrolls inside itself once the filters outgrow the viewport,
     so the sidebar and the charts scroll independently. */
  .side { position: sticky; top: 20px; flex: 0 0 250px; max-height: calc(100vh - 40px); overflow-y: auto; background: #fff; padding: 14px 16px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); font-size: 0.85rem; }
  .main { flex: 1 1 auto; min-width: 0; max-width: 1200px; }
  h1 { font-size: 1.5rem; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 0.9rem; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 30px; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 6px; overflow: hidden; }
  th, td { padding: 8px 12px; text-align: right; border-bottom: 1px solid #eee; }
  th { background: #f0f0f0; font-weight: 600; white-space: nowrap; }
  td:first-child, th:first-child { text-align: left; }
  tr:hover { background: #f5f5f5; }
  tr.run-row { cursor: pointer; }
  tr.deselected { color: #b0b0b0; }
  tr.deselected td { color: inherit; }
  .chart-container { background: #fff; padding: 20px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 30px; }
  .common-params { background: #fff; padding: 12px 16px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; }
  .common-params h2 { font-size: 1rem; margin: 0 0 6px 0; }
  .common-params code { font-size: 0.82rem; word-break: break-all; color: #333; }
  td.hint { cursor: help; text-decoration: underline dotted; text-underline-offset: 2px; }
  .label-toggle { display: block; margin-bottom: 10px; cursor: pointer; user-select: none; }
  .label-toggle input { margin-right: 6px; }
  .chart-title { font-size: 1.05rem; margin: 0 0 10px 0; }
  .chart-box { position: relative; }
  /* Left of the rotated Y title, in the card's own padding. */
  .zoom-y { position: absolute; left: -17px; top: 50%; transform: translateY(-50%); display: flex; flex-direction: column; gap: 3px; }
  .zoom-x { display: flex; justify-content: center; gap: 3px; margin-top: 2px; }
  .zoom-btn { width: 19px; height: 19px; padding: 0; font: inherit; font-size: 13px; line-height: 1; color: #555; background: #f4f4f4; border: 1px solid #ddd; border-radius: 3px; cursor: pointer; }
  .zoom-btn:hover { background: #e6e6e6; }
  .note { color: #888; font-size: 0.8rem; margin-top: 10px; }
  .side h3 { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: #666; margin: 16px 0 6px; }
  .side button { display: block; width: 100%; margin-bottom: 4px; padding: 5px 8px; font: inherit; font-size: 0.82rem; text-align: left; background: #f4f4f4; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; }
  .side button:hover { background: #eaeaea; }
  .tri-list { list-style: none; margin: 0; padding: 0; }
  .tri-row { display: flex; align-items: center; gap: 6px; padding: 2px 0; cursor: pointer; user-select: none; word-break: break-all; }
  .tri-row:hover { background: #f4f4f4; }
  .tri-note { margin-left: auto; padding-left: 6px; color: #888; white-space: nowrap; }
  .tri { flex: 0 0 auto; width: 13px; height: 13px; border: 1px solid #999; border-radius: 3px; background: #fff; position: relative; }
  .tri::after { content: "\\2713"; position: absolute; left: 0; top: -4px; width: 13px; text-align: center; font-size: 13px; font-weight: 700; display: none; }
  .tri[data-state="all"]::after { display: block; color: #111; }
  .tri[data-state="some"]::after { display: block; color: #b0b0b0; }
</style>
</head>
<body>
<div class="layout">
<aside class="side">
<button data-act="reset">Reset plots framing</button>
<label class="label-toggle"><input type="checkbox" id="hideAllLabels"> Hide all labels</label>
<button data-act="all">Select all</button>
<button data-act="none">Select none</button>
<button data-act="frontier">Select frontier</button>
<button data-act="stock">Deselect non-stock quants</button>
{sidebar}</aside>
<main class="main">
<h1>KLD Effect of Quantization</h1>
<p class="subtitle">Generated by perplexity-report.py</p>
<table>
<thead>
<tr>
  <th>Run</th>
  <th>KV (bpw)</th>
{weights_th}  <th>KV cache (MiB) @{ctx_label}</th>
{vram_th}  <th>Mean KLD</th>
  <th>99.9% KLD</th>
  <th>Top-1 (%)</th>
{coll_th}  <th>Perplexity (%)</th>
  <th>Speed (tok/s)</th>
  <th>Speed (%)</th>
</tr>
</thead>
<tbody>
"""

HTML_TABLE_END = """\
</tbody>
</table>
"""

HTML_SCRIPT = """\
<p class="note">First chart's Y-axis is log scale; all others linear. All charts share the same X axis.
The X axis is shared by every plot: scrolling or dragging one reframes X on all of them, while Y stays local. The +/- pairs beside each axis label zoom that axis alone. Double-click to reset.
Click a point, a table row or a sidebar group to select or deselect those runs: deselected runs stay on the plot, faint and unlabelled, and grey in the table.</p>
<script>
var CHART_DEFS = {chart_defs_json};
var RUN_META = {run_meta_json};
var FRONTIER = {frontier_json};
var X_MIN = {x_min_json};
var X_MAX = {x_max_json};
var X_AXIS_LABEL = {x_axis_label_json};
var BASELINE_LABEL = {baseline_label_json};
var HIDE_ALL_LABELS = false;
var charts = [];
var CHART_BY_ID = {};

// The one piece of state everything else reads: which runs are selected.
// A deselected run keeps its point (faint, unlabelled) and its table row
// (grey); it is dimmed, never hidden, so the shape of the sweep is preserved.
// Default = the frontier, i.e. "select none" followed by "select frontier".
var SELECTED = new Set(FRONTIER);
function isSelected(pt) { return pt && SELECTED.has(pt._id); }

function drawExtras(chart, def) {
  var ctx = chart.ctx;
  var xScale = chart.scales.x;
  var yScale = chart.scales.y;
  ctx.save();
  // Tolerance tick brackets (error bars)
  if (def.errorBars) {
    ctx.strokeStyle = '#555';
    ctx.lineWidth = 1;
    for (var d = 0; d < chart.data.datasets.length; d++) {
      var pts = chart.data.datasets[d].data;
      for (var p = 0; p < pts.length; p++) {
        var pt = pts[p];
        if (pt._tol === null || pt._tol === undefined) continue;
        // A deselected point is dimmed to near-invisible; a full-strength
        // bracket left hanging over it would be the loudest thing on the plot.
        if (!isSelected(pt)) continue;
        var xPix = xScale.getPixelForValue(pt.x);
        var yHi = yScale.getPixelForValue(pt.y + pt._tol);
        var yLo = yScale.getPixelForValue(pt.y - pt._tol);
        ctx.beginPath();
        ctx.moveTo(xPix, yHi); ctx.lineTo(xPix, yLo);
        ctx.moveTo(xPix - 4, yHi); ctx.lineTo(xPix + 4, yHi);
        ctx.moveTo(xPix - 4, yLo); ctx.lineTo(xPix + 4, yLo);
        ctx.stroke();
      }
    }
  }
  // Point labels: only for selected runs, and only while "hide all labels" is
  // off. Hiding labels does not dim anything -- the two are separate controls.
  if (!HIDE_ALL_LABELS) {
    ctx.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#333';
    var drawn = {};
    for (var d = 0; d < chart.data.datasets.length; d++) {
      var pts = chart.data.datasets[d].data;
      for (var p = 0; p < pts.length; p++) {
        var pt = pts[p];
        if (!isSelected(pt) || drawn[pt._id]) continue;
        var xPos = xScale.getPixelForValue(pt.x);
        if (xPos < 0 || xPos > chart.width) continue;
        drawn[pt._id] = 1;
        var y = yScale.getPixelForValue(pt.y) - 10;
        ctx.fillText(pt._label, xPos, y);
      }
    }
  }
  ctx.restore();
  // Baseline annotation — bottom-right corner
  if (def.baselineNote && BASELINE_LABEL) {
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

// ---------------------------------------------------------------------------
//  Framing. The X axis is shared: it means the same VRAM on every chart, so
//  every X change -- wheel, drag, button, reset -- goes through setXRange and
//  lands on all of them at once. Y is each plot's own, in its own units.
// ---------------------------------------------------------------------------
function setXRange(min, max) {
  charts.forEach(function(c) {
    c.options.scales.x.min = min;
    c.options.scales.x.max = max;
    c.update('none');
  });
}

function zoomX(factor, center) {
  var x = charts[0].options.scales.x;
  if (center === undefined) center = (x.min + x.max) / 2;
  var range = (x.max - x.min) * factor;
  setXRange(center - range / 2, center + range / 2);
}

function zoomY(chart, def, factor, center) {
  var y = chart.options.scales.y;
  if (def.yType === 'logarithmic') {
    var lo = Math.log10(y.min), hi = Math.log10(y.max);
    var c = center === undefined ? (lo + hi) / 2 : Math.log10(center);
    var range = (hi - lo) * factor;
    y.min = Math.pow(10, c - range / 2);
    y.max = Math.pow(10, c + range / 2);
  } else {
    var c2 = center === undefined ? (y.min + y.max) / 2 : center;
    var range2 = (y.max - y.min) * factor;
    y.min = c2 - range2 / 2;
    y.max = c2 + range2 / 2;
  }
  chart.update('none');
}

function attachZoomPan(canvas, chart, def) {
  // Reset restores the range the plot was generated with, not one recomputed
  // from whatever is on screen. Kept on the chart so the sidebar can reframe
  // every plot at once.
  chart.resetView = function() {
    chart.options.scales.x.min = X_MIN;
    chart.options.scales.x.max = X_MAX;
    chart.options.scales.y.min = def.yMin;
    chart.options.scales.y.max = def.yMax;
    chart.update('none');
  };

  // The wheel zooms both axes of the plot under the cursor -- and, because X is
  // shared, the X axis of every other plot with it.
  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var xScale = chart.scales.x;
    var yScale = chart.scales.y;
    if (!xScale || !yScale) return;
    var factor = e.deltaY > 0 ? 1.15 : 0.85;
    zoomY(chart, def, factor, yScale.getValueForPixel(e.clientY - rect.top));
    zoomX(factor, xScale.getValueForPixel(e.clientX - rect.left));
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
    // Y pan, this plot only
    var yPixRange = yScale.bottom - yScale.top;
    if (yPixRange > 0) {
      if (def.yType === 'logarithmic') {
        var yLogMin = Math.log10(panYMin);
        var yLogMax = Math.log10(panYMax);
        var yLogRange = yLogMax - yLogMin;
        chart.options.scales.y.min = Math.pow(10, yLogMin + (dy / yPixRange) * yLogRange);
        chart.options.scales.y.max = Math.pow(10, yLogMax + (dy / yPixRange) * yLogRange);
      } else {
        var yDataRange = panYMax - panYMin;
        chart.options.scales.y.min = panYMin + (dy / yPixRange) * yDataRange;
        chart.options.scales.y.max = panYMax + (dy / yPixRange) * yDataRange;
      }
    }
    // X pan, every plot (the update below covers this one's Y too)
    var xPixRange = xScale.right - xScale.left;
    if (xPixRange > 0) {
      var xDataRange = panXMax - panXMin;
      var shift = (dx / xPixRange) * xDataRange;
      setXRange(panXMin - shift, panXMax - shift);
    } else {
      chart.update('none');
    }
  });

  document.addEventListener('mouseup', function() {
    if (isPanning) {
      isPanning = false;
      canvas.style.cursor = '';
    }
  });

  // Double-click resets this plot's Y and the shared X.
  canvas.addEventListener('dblclick', function() {
    chart.resetView();
    setXRange(X_MIN, X_MAX);
  });
}

// Marker colour is resolved per point at draw time, so one repaint reflects
// the current selection everywhere without rebuilding any dataset.
function markColor(c) {
  var ds = c.dataset, i = c.dataIndex;
  if (!ds._full) return undefined;
  return (isSelected(ds.data[i]) ? ds._full : ds._faint)[i];
}

CHART_DEFS.forEach(function(def) {
  var canvas = document.getElementById(def.id);
  def.datasets.forEach(function(ds) {
    if (!ds._full) return;
    ds.pointBackgroundColor = markColor;
    ds.pointBorderColor = markColor;
  });
  var chart = new Chart(canvas.getContext('2d'), {
    type: 'scatter',
    data: { datasets: def.datasets },
    options: {
      responsive: true,
      // Chart.js hands onClick the elements for the active interaction mode;
      // ask explicitly as well, so a build whose defaults differ still only
      // toggles when the pointer is genuinely over a marker.
      onClick: function(e, els) {
        var hit = (els && els.length) ? els : chart.getElementsAtEventForMode(
          e, 'nearest', { intersect: true }, true);
        if (hit.length) {
          var pt = chart.data.datasets[hit[0].datasetIndex].data[hit[0].index];
          if (pt && pt._id !== undefined) toggle([pt._id]);
        }
      },
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            filter: function(item, data) {
              // The non-frontier scatter shares its name with the solid line.
              return !data.datasets[item.datasetIndex]._sub;
            }
          }
        },
        tooltip: {
          // A guide line can legitimately re-plot a point the main series also
          // carries (an f16/f16 run sits on both), and 'nearest' hands us every
          // dataset sharing the pixel. Show each distinct point once.
          filter: function(item, index, items) {
            for (var i = 0; i < index; i++) {
              if (items[i].raw._label === item.raw._label &&
                  items[i].parsed.x === item.parsed.x &&
                  items[i].parsed.y === item.parsed.y) return false;
            }
            return true;
          },
          callbacks: {
            label: function(c) {
              var lbl = c.raw._label || '';
              var name = def.tooltipLabel || c.dataset.label;
              var unit = def.unit || '';
              var s = lbl + '  ' + name + ': ' + c.parsed.y.toFixed(def.decimals);
              if (c.raw._tol !== null && c.raw._tol !== undefined) {
                s += ' \u00b1 ' + c.raw._tol.toFixed(def.decimals);
              }
              s += unit;
              var speed = c.raw._speed;
              var pct = c.raw._speed_pct;
              if (def.showSpeed !== false && speed !== null && speed !== undefined) {
                s += '  ' + speed.toFixed(1) + ' tok/s';
                if (pct !== null && pct !== undefined) {
                  s += ' (' + pct.toFixed(1) + '%)';
                }
              }
              return s;
            }
          }
        }
      },
      scales: {
        x: {
          title: { display: true, text: X_AXIS_LABEL },
          type: 'linear',
          min: X_MIN,
          max: X_MAX
        },
        y: {
          title: { display: true, text: def.yTitle },
          type: def.yType,
          min: def.yMin,
          max: def.yMax
        }
      },
      elements: {
        point: {
          radius: 6,
          hoverRadius: 8
        }
      }
    },
    plugins: [{ afterDraw: function(chart) { drawExtras(chart, def); } }]
  });
  attachZoomPan(canvas, chart, def);
  chart._def = def;
  CHART_BY_ID[def.id] = chart;
  charts.push(chart);
});

// The +/- pairs beside each axis label: one axis at a time, X across all plots.
// A click is a 10% step -- fine enough to creep up on a crowded region, and an
// exact inverse of its opposite, so a click each way returns to where you were.
var ZOOM_STEP = 0.9;
document.querySelectorAll('.zoom-btn').forEach(function(b) {
  b.addEventListener('click', function() {
    var factor = b.dataset.dir === 'in' ? ZOOM_STEP : 1 / ZOOM_STEP;
    if (b.dataset.axis === 'x') {
      zoomX(factor);
    } else {
      var c = CHART_BY_ID[b.dataset.chart];
      if (c) zoomY(c, c._def, factor);
    }
  });
});

// ---------------------------------------------------------------------------
//  Selection: the sidebar, the table and the plots are three views of SELECTED
// ---------------------------------------------------------------------------
var triRows = Array.prototype.slice.call(document.querySelectorAll('.tri-row'));
triRows.forEach(function(row) {
  row.__ids = row.dataset.ids.split(',').map(Number);
});

function redraw() {
  // Sidebar: a group is ticked black when all of it is in, grey when some is.
  triRows.forEach(function(row) {
    var n = row.__ids.filter(function(i) { return SELECTED.has(i); }).length;
    row.querySelector('.tri').dataset.state =
      n === 0 ? 'none' : (n === row.__ids.length ? 'all' : 'some');
  });
  document.querySelectorAll('tr.run-row').forEach(function(tr) {
    tr.classList.toggle('deselected', !SELECTED.has(Number(tr.dataset.id)));
  });
  charts.forEach(function(c) { c.update('none'); });
}

function setSelection(ids, on) {
  ids.forEach(function(i) { on ? SELECTED.add(i) : SELECTED.delete(i); });
  redraw();
}

// One rule for a group and for a lone point: select only when nothing in the
// set is selected, otherwise clear it. On a group that is the required
// grey -> unticked -> black -> unticked cycle; on a single run it is a plain
// selected <-> deselected flip.
function toggle(ids) {
  var none = ids.every(function(i) { return !SELECTED.has(i); });
  setSelection(ids, none);
}

triRows.forEach(function(row) {
  row.addEventListener('click', function() { toggle(row.__ids); });
});
document.querySelectorAll('tr.run-row').forEach(function(tr) {
  tr.addEventListener('click', function() { toggle([Number(tr.dataset.id)]); });
});

var ALL_IDS = Object.keys(RUN_META).map(Number);
document.querySelectorAll('.side button').forEach(function(b) {
  b.addEventListener('click', function() {
    var act = b.dataset.act;
    if (act === 'reset') charts.forEach(function(c) { c.resetView(); });
    else if (act === 'all') setSelection(ALL_IDS, true);
    else if (act === 'none') setSelection(ALL_IDS, false);
    else if (act === 'frontier') setSelection(FRONTIER, true);
    else if (act === 'stock') setSelection(
      ALL_IDS.filter(function(i) { return !RUN_META[i].stock; }), false);
  });
});

document.getElementById('hideAllLabels').addEventListener('change', function() {
  HIDE_ALL_LABELS = this.checked;
  charts.forEach(function(c) { c.draw(); });
});

redraw();
</script>
"""

HTML_TAIL = """\
</main>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
#  SVG plot (matplotlib)
# ---------------------------------------------------------------------------
def _frontier_groups(
    runs: list[dict],
    speed_cutoff_factor: float = 0.33,
    cost_key: str = "size",
) -> tuple[set[int], set[int]]:
    """Return (frontier_ids, suboptimal_ids); see :func:`_stat_frontier`.

    The baseline (KLD rerun) is a frontier candidate like any other run; the
    stats-less logits run is dropped, as are aborted and too-slow runs. The
    speed cutoff is still derived from the baseline. ``cost_key`` is the run
    field ranked along the x-axis (``size`` bpw, or ``ctx_mib`` when the model
    is recognised).
    """
    # Only consider runs that are not too slow
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    speed_cutoff = None
    if baseline_run and baseline_run["speed"] is not None:
        speed_cutoff = baseline_run["speed"] * speed_cutoff_factor

    eligible_runs = [r for r in runs if not r.get("aborted") and not r.get("logits")]
    if speed_cutoff is not None:
        eligible_runs = [
            r for r in eligible_runs if r["speed"] is None or r["speed"] >= speed_cutoff
        ]

    frontier = _stat_frontier(eligible_runs, cost_key)
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

    # The baseline is a frontier candidate like any other run; the stats-less
    # logits run is never plotted.
    frontier, _ = _frontier_groups(sorted_runs, cost_key=cost_key)

    sorted_runs = [r for r in sorted_runs if not r.get("logits")]
    suboptimal_ids = {id(r) for r in sorted_runs if id(r) not in frontier}

    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax.set_xlabel(x_axis_label, fontsize=11)
    ax.set_ylabel("KL Divergence", fontsize=11)
    ax.set_yscale("log")
    ax.set_xlim(*_x_range(sorted_runs, cost_key))

    # Y range: same logic as chart.js (baseline ignored: its near-zero
    # measured KLD would drag the bottom of the log scale far down)
    max_y = max(
        r[key]
        for key in ["mean", "p999"]
        for r in sorted_runs
        if not r.get("baseline") and r[key] and r[key] > 0
    )
    min_nonzero = min(
        r[key]
        for key in ["mean", "p999"]
        for r in sorted_runs
        if not r.get("baseline") and r[key] and r[key] > 0
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
        ("Mean KLD", "mean", "#e74c3c", frontier),
        ("99.9% KLD", "p999", "#9b59b6", frontier),
    ]
    guides = _reference_subsets(sorted_runs)
    author_style = _present_authors(sorted_runs)

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
                # Markers move to the author scatter below when colouring by
                # author; the line keeps its stat colour either way.
                marker=None if author_style else "o",
                markersize=6,
                label=_series_label(label, FRONTIER_QUALIFIER if guides else ""),
                zorder=5,
            )

        if author_style:
            _scatter_by_author(ax, best_pts, sub_pts, author_style)
        elif sub_pts:
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
    for subset_key, subset_name, subset in guides:
        dash_style = _MPL_REF_DASH[subset_key]
        subset_runs_list = _guide_frontier(subset, cost_key)
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
                label=_series_label(stat_label, subset_name),
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

    _legend(ax, author_style, fontsize=8, loc="upper left", framealpha=0.9)
    fig.tight_layout()

    from io import StringIO

    buf = StringIO()
    fig.savefig(buf, format="svg", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def generate_stat_svg(
    runs: list[dict],
    key: str,
    width=1000,
    height=600,
    dpi=100,
    speed_cutoff_factor: float = 0.33,
    ctx_label: str = "256k",
) -> str:
    """Generate a linear-Y SVG plot of a single stat (mean / p999 / top1 /
    ppl_cor / speed) vs size using matplotlib. Stats with a tolerance (top1,
    coll) get error-bar tick brackets. The frontier is per-stat (Top-1 and
    Perplexity: higher is better)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    label, color = STAT_STYLE[key]
    tol_key = STAT_TOL_KEY.get(key)
    cost_key, x_axis_label, _ = _cost_axis(runs, ctx_label)

    sorted_runs = sorted(
        (
            r
            for r in runs
            if not r.get("aborted") and not r.get("logits") and r[key] is not None
        ),
        key=lambda r: r[cost_key],
    )

    frontier, _ = _frontier_groups(runs, speed_cutoff_factor, cost_key=cost_key)
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    speed_cutoff = (
        baseline_run["speed"] * speed_cutoff_factor
        if baseline_run and baseline_run["speed"] is not None
        else None
    )
    slow_ids = {
        id(r)
        for r in runs
        if speed_cutoff is not None
        and r["speed"] is not None
        and r["speed"] < speed_cutoff
    }

    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax.set_xlabel(x_axis_label, fontsize=11)
    ax.set_ylabel(label, fontsize=11)
    ax.set_title(STAT_TITLE[key], fontsize=12)
    ax.set_xlim(*_x_range(sorted_runs, cost_key))

    # Linear Y range; include tolerance whiskers when present. Only the KLD
    # stats floor at 0; percent-scale stats (ppl_cor) auto-range on the data
    # like the tolerance stats do.
    lo = min(r[key] - (r.get(tol_key) or 0) for r in sorted_runs)
    hi = max(r[key] + (r.get(tol_key) or 0) for r in sorted_runs)
    span = hi - lo or abs(hi) * 0.1 or 1.0
    if key in ("mean", "p999"):
        ax.set_ylim(0, hi * 1.15)
    else:
        ax.set_ylim(lo - span * 0.1, hi + span * 0.1)

    ax.tick_params(labelsize=9)
    ax.grid(True, which="both", ls=":", alpha=0.3)

    best_pts = [
        (r[cost_key], r[key], r)
        for r in sorted_runs
        if id(r) in frontier and id(r) not in slow_ids
    ]
    sub_pts = [
        (r[cost_key], r[key], r)
        for r in sorted_runs
        if id(r) not in frontier or id(r) in slow_ids
    ]

    guides = _reference_subsets(sorted_runs)
    author_style = _present_authors(sorted_runs)
    if best_pts:
        best_pts.sort(key=lambda p: p[0])
        ax.plot(
            [p[0] for p in best_pts],
            [p[1] for p in best_pts],
            color=color,
            linewidth=1.5,
            marker=None if author_style else "o",
            markersize=6,
            label=_series_label(label, FRONTIER_QUALIFIER if guides else ""),
            zorder=5,
        )
    if author_style:
        _scatter_by_author(ax, best_pts, sub_pts, author_style)
    elif sub_pts:
        ax.scatter(
            [p[0] for p in sub_pts],
            [p[1] for p in sub_pts],
            color=color,
            alpha=0.25,
            s=20,
            zorder=3,
        )

    # Tolerance tick brackets
    if tol_key:
        xs = [r[cost_key] for r in sorted_runs if r.get(tol_key) is not None]
        ys = [r[key] for r in sorted_runs if r.get(tol_key) is not None]
        yerr = [r[tol_key] for r in sorted_runs if r.get(tol_key) is not None]
        ax.errorbar(
            xs,
            ys,
            yerr=yerr,
            fmt="none",
            ecolor="#555555",
            elinewidth=1,
            capsize=3,
            zorder=4,
        )

    # Labels: frontier points bold, others faint
    for pt_list, alpha in [(best_pts, 1.0), (sub_pts, 0.4)]:
        for x, y, r in pt_list:
            ax.annotate(
                r["label"],
                (x, y),
                textcoords="offset points",
                xytext=(0, -12),
                fontsize=6.5 if alpha == 1.0 else 6,
                ha="center",
                va="top",
                alpha=alpha,
                zorder=10,
            )

    # Extra lines: k=f16 (dashed), v=f16 (dotted)
    for subset_key, subset_name, subset in guides:
        dash_style = _MPL_REF_DASH[subset_key]
        pts = sorted(
            (
                (r[cost_key], r[key])
                for r in _guide_frontier(subset, cost_key, slow_ids)
            ),
            key=lambda p: p[0],
        )
        if not pts:
            continue
        r_, g_, b_ = (
            int(color[1:3], 16),
            int(color[3:5], 16),
            int(color[5:7], 16),
        )
        ax.plot(
            [p[0] for p in pts],
            [p[1] for p in pts],
            color=(r_ / 255, g_ / 255, b_ / 255, 0.35),
            linewidth=1,
            linestyle=dash_style,
            marker="o",
            markersize=4,
            label=_series_label(label, subset_name),
            zorder=4,
        )

    _legend(ax, author_style, fontsize=8, loc="best", framealpha=0.9)
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
    extra_plots: list[tuple[str, str]] | None = None,
    repo: str | None = None,
    branch: str = "main",
    speed_cutoff_factor: float = 0.33,
    n_parallel: int = 4,
    ctx_label: str = "256k",
) -> str:
    """Generate a Markdown report with table and SVG plots (image ref + xref).

    ``extra_plots`` is a list of (title, path) for the per-stat linear SVGs."""
    cost_key, _, frontier_marker = _cost_axis(runs, ctx_label)
    sorted_runs = sorted(runs, key=lambda r: r["size"], reverse=True)

    frontier, _ = _frontier_groups(sorted_runs, speed_cutoff_factor, cost_key=cost_key)
    suboptimal_ids = {id(r) for r in runs if id(r) not in frontier}

    lines = []
    lines.append("# KLD Effect of Quantization")
    lines.append("")
    lines.append("Generated by perplexity-report.py")
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

    # "Same sampled p" is optional (patched llama-perplexity only): drop the
    # column entirely rather than showing one full of blanks.
    has_coll = any(r.get("coll") is not None for r in runs)
    has_weights = all(r.get("weights_mib") is not None for r in runs)
    headers = [
        "Run",
        "KV (bpw)",
        *(["Weights (MiB)"] if has_weights else []),
        f"KV cache (MiB) @{ctx_label}",
        *([f"VRAM (MiB) @{ctx_label}"] if has_weights else []),
        "Mean KLD",
        "99.9% KLD",
        "Top-1 (%)",
        *(["Same sampled (%)"] if has_coll else []),
        "Perplexity (%)",
        "Speed (tok/s)",
        "Speed (%)",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))

    for r in sorted(runs, key=lambda r: r[cost_key], reverse=True):
        # A label joins its dimensions with `|`, which is also this table's cell
        # separator: escape it or the row splits into extra columns.
        label = r["label"].replace("|", "\\|")
        if r.get("baseline"):
            label += " (baseline)"
        elif r.get("logits"):
            label += " (logits)"
        elif r.get("aborted"):
            label += " (aborted)"
        frontier_mark = (
            f" {frontier_marker}"
            if id(r) not in suboptimal_ids and not r.get("logits")
            else ""
        )
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}" if r["speed_pct"] is not None else "N/A"
        cost_cells = [
            *([_fmt_mib(r["weights_mib"])] if has_weights else []),
            _fmt_mib(r.get("ctx_mib")),
            *([_fmt_mib(r["vram_mib"])] if has_weights else []),
        ]
        lines.append(
            f"| {label}{frontier_mark} | {_fmt_size(r['size'])} |"
            + "".join(f" {c} |" for c in cost_cells)
            + f" {_fmt(r['mean'])} | {_fmt(r['p999'])} |"
            f" {_fmt_tol(r.get('top1'), r.get('top1_tol'), 3)} |"
            + (
                f" {_fmt_tol(r.get('coll'), r.get('coll_tol'), 3)} |"
                if has_coll
                else ""
            )
            + f" {_fmt_tol(r.get('ppl_cor'), None, 2)} |"
            f" {speed_fmt} | {pct_fmt} |"
        )

    lines.append("")

    # SVG images + xref links
    all_plots = [("KLD Plot", plot_path)] + (extra_plots or [])
    if repo and plot_path:
        owner, repo_name = repo.split("/", 1)
        # plot paths are forward-slash paths relative to the git repo root
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}"
        # htmlpreview proxies raw github; passes through with correct mime
        html_preview = f"https://htmlpreview.github.io/?{raw_base}/{html_path}"
        lines.append(f"[Interactive report]({html_preview})")
        lines.append("")
        for title, p in all_plots:
            if p:
                lines.append(f"![{title}]({raw_base}/{p})")
                lines.append("")
    elif plot_path:
        xref_parts = []
        if html_path:
            xref_parts.append(f"[Interactive report]({html_path})")
        if xref_parts:
            lines.append(" | ".join(xref_parts))
            lines.append("")
        for title, p in all_plots:
            if p:
                lines.append(f"![{title}]({p})")
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
            f"> KV cache (MiB) @{ctx_label} is the estimated beellama v0.4.1 "
            f"llama-server KV-cache VRAM at a {ctx_label} context with "
            f"n_parallel={n_parallel} (source-modelled, not yet log-validated)."
        )
        lines.append("")

    if has_weights:
        lines.append(
            "> Weights (MiB) is the on-disk size of the shards the run loaded, "
            "from the log's own provenance; VRAM (MiB) adds the KV cache to it "
            "and is the x axis of every plot."
        )
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Generate KLD report from perplexity.py output"
    )
    ap.add_argument(
        "log",
        nargs="?",
        default="perplexity.log",
        help="Input log (default: perplexity.log)",
    )
    ap.add_argument(
        "-o",
        "--output",
        default="perplexity-report",
        help="Output basename (no extension). Generates BASENAME.html, BASENAME.md, BASENAME.log-kld.svg and per-stat BASENAME.<stat>.svg (default: perplexity-report)",
    )
    ap.add_argument(
        "--whitelist",
        nargs="+",
        metavar="LABEL",
        help="Only include these runs, matched against the display label "
        "(e.g. q4_0, q8_0/q4_0, q4_0 t1024, UD-Q4_K_XL|q8_0). "
        "The baseline is always included even if not listed.",
    )
    ap.add_argument(
        "--cache-type-k",
        nargs="+",
        metavar="TYPE",
        help="Only include runs whose -ctk is one of these (e.g. f16 q8_0). "
        "ANDed with --cache-type-v and --author; the baseline is always kept.",
    )
    ap.add_argument(
        "--cache-type-v",
        nargs="+",
        metavar="TYPE",
        help="Only include runs whose -ctv is one of these.",
    )
    ap.add_argument(
        "--author",
        nargs="+",
        metavar="NAME",
        help="Only include runs whose model comes from one of these publishers "
        "(the part of --hf-repo before the '/').",
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
        "when sizing the KV cache (MiB) column; it scales the KVarN f16 "
        "exact-tail overlay. Default 4 (llama-server auto for these models).",
    )
    ap.add_argument(
        "--ctx-size",
        type=parse_ctx_size,
        default=None,
        metavar="N[k|M]",
        help="Projected context size for the KV cache (MiB) column; accepts "
        "k/M suffixes (default: the run's own --ctx-size from the log).",
    )
    args = ap.parse_args()

    # Auto-detect GitHub repo from git remote
    repo = args.repo
    branch = args.branch or "main"
    repo_root: Path | None = None
    if not repo:
        with contextlib.suppress(OSError):
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
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
                        check=False,
                    )
                    if br.returncode == 0 and br.stdout.strip() != "HEAD":
                        branch = br.stdout.strip()

    # Find git repo root to compute repo-relative paths for embedded URLs
    with contextlib.suppress(OSError):
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if toplevel.returncode == 0:
            repo_root = Path(toplevel.stdout.strip())

    if repo:
        print(f"GitHub repo detected: {repo} (branch: {branch})")

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"ERROR: {log_path} not found", file=sys.stderr)
        sys.exit(1)

    runs, common_params, projected_ctx = parse_log(
        args.log, args.n_parallel, args.ctx_size
    )
    ctx_label = _fmt_ctx_label(projected_ctx)

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
    # The baseline and the logits run survive every filter: the first is the
    # KLD zero and the 100% speed reference the rest are read against, the
    # second is what pinned the weights. Labels are not recomputed either --
    # they describe the whole log, which keeps a filtered report comparable
    # with the full one and keeps --whitelist matching against the same names.
    ctk = {v.lower() for v in args.cache_type_k or ()}
    ctv = {v.lower() for v in args.cache_type_v or ()}
    authors = {v.lower() for v in args.author or ()}
    filtered = bool(args.whitelist or ctk or ctv or authors)
    if filtered:
        whitelisted = set(args.whitelist or ())
        runs = [
            r
            for r in runs
            if r.get("baseline")
            or r.get("logits")
            or (
                (not whitelisted or r["label"] in whitelisted)
                and _selected(r, ctk, ctv, authors)
            )
        ]
    # The baseline and the logits run are kept unconditionally above, so an
    # empty *selection* still leaves them behind; a report of nothing but the
    # reference has no plot to draw (its y-range is taken over the others).
    if not any(not r.get("logits") and not r.get("baseline") for r in runs):
        print(
            "No KLD runs to report"
            + (" (nothing matched the filters)" if filtered else ""),
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Parsed {len(runs)} KLD runs:"
        + (
            f" (filtered from {len(runs_unfiltered)})"
            if filtered and len(runs) != len(runs_unfiltered)
            else ""
        )
    )
    for r in runs:
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}%" if r["speed_pct"] is not None else "N/A"
        mean_fmt = f"{r['mean']:.6f}" if r["mean"] is not None else "       -"
        p999_fmt = f"{r['p999']:.6f}" if r["p999"] is not None else "       -"
        top1_fmt = f"{r['top1']:.3f}" if r.get("top1") is not None else "     -"
        aborted_tag = " (aborted)" if r.get("aborted") else ""
        ctx_fmt = f"{r['ctx_mib']:>8,.0f} MiB" if r.get("ctx_mib") is not None else ""
        vram_fmt = (
            f"  vram={r['vram_mib']:>9,.0f} MiB"
            if r.get("vram_mib") is not None
            else ""
        )
        cor_fmt = f"{r['ppl_cor']:.2f}" if r.get("ppl_cor") is not None else "     -"
        print(
            f"  {r['label']:25s}{aborted_tag}  size={r['size']:6.2f} bpw  "
            f"ctx@{ctx_label}={ctx_fmt:>12s}{vram_fmt}  "
            f"mean={mean_fmt}  p999={p999_fmt}  "
            f"top1={top1_fmt}  ppl_cor={cor_fmt}  "
            f"speed={speed_fmt:>7s}  {pct_fmt:>7s}  ({r['n_chunks']} chunks)"
        )

    # Append extensions to -o value as-is (don't strip any existing extension)
    prefix = str(args.output)
    html_path = Path(prefix + ".html")
    md_path = Path(prefix + ".md")
    plot_path = Path(prefix + ".log-kld.svg")

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

    # Extra per-stat linear SVGs: mean KLD, 99.9% KLD, Top-1, Perplexity, speed
    extra_plots: list[tuple[str, Path]] = []
    stat_keys = ["mean", "p999", "top1", "ppl_cor", "speed"]
    # Optional stat (patched llama-perplexity only). Left out of the list
    # entirely when absent, so no "skipped" line is printed for old logs.
    if any(r.get("coll") is not None for r in runs):
        stat_keys.insert(stat_keys.index("top1") + 1, "coll")
    for key in stat_keys:
        if not any(
            not r.get("baseline")
            and not r.get("logits")
            and not r.get("aborted")
            and r.get(key) is not None
            for r in runs
        ):
            print(f"SVG  -> (skipped {key}: no data)")
            continue
        p = Path(f"{prefix}.{STAT_SVG_SUFFIX[key]}.svg")
        p.write_text(
            generate_stat_svg(
                runs, key, speed_cutoff_factor=speed_cutoff_factor, ctx_label=ctx_label
            )
        )
        extra_plots.append((STAT_STYLE[key][0], p))
        print(f"SVG  -> {p}")

    md = generate_markdown(
        runs,
        common_params,
        html_path=html_repo_rel,
        plot_path=plot_repo_rel,
        extra_plots=[(t, _repo_rel(p)) for t, p in extra_plots],
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
