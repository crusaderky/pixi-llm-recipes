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
import contextlib
import html as html_mod
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from kv_cache_common import ModelKV, resolve_bpw, resolve_model

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
# "RMS Δp    :  2.105 ± 0.040 %"
RMS_DP_RE = re.compile(r"^RMS\s+Δp\s*:\s*([\d.]+)\s*±\s*([\d.]+)", re.MULTILINE)
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
    path: str, n_parallel: int = 4, projected_ctx: int | None = None
) -> tuple[list[dict], str, int]:
    """Parse the log. ``projected_ctx`` is the context the Context (MiB)
    column is evaluated at; when None it defaults to the run's own context
    size: the logits (baseline-generating) run's ``--ctx-size``, else any
    run's, else 256k. Returns (runs, common_params, projected_ctx)."""
    text = Path(path).read_text()
    sections = re.split(r"^-{30,}", text, flags=re.MULTILINE)
    common_params = _common_params(text)

    runs = []
    log_ctx: int | None = None  # ctx of the logits (baseline-generating) run
    any_ctx: int | None = None  # ctx of any run (fallback)
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
            if ctx_size and any_ctx is None:
                any_ctx = ctx_size

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

            if not has_kld:
                # Logits-generating run — no KLD stats of its own. The baseline
                # KLD rerun (same combo + --kl-divergence) carries the measured
                # baseline numbers; both are flagged in the post-pass below.
                if ctx_size and log_ctx is None:
                    log_ctx = ctx_size
                size = resolve_bpw(ctk) + resolve_bpw(ctv)
                runs.append(
                    {
                        "ctk": ctk,
                        "ctv": ctv,
                        "tail": tail,
                        "label": f"{ctk}/{ctv}",
                        "size": size,
                        "n_chunks": 0,
                        "mean": None,
                        "p999": None,
                        "rms_dp": None,
                        "rms_dp_tol": None,
                        "top1": None,
                        "top1_tol": None,
                        "coll": None,
                        "coll_tol": None,
                        "ppl_cor": None,
                        "speed": speed,
                        "logits": True,
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

            rms_m = RMS_DP_RE.search(chunk)
            top_m = TOP_P_RE.search(chunk)
            coll_m = COLL_P_RE.search(chunk)
            cor_m = PPL_COR_RE.search(chunk)

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
                            "p999": None,
                            "rms_dp": None,
                            "rms_dp_tol": None,
                            "top1": None,
                            "top1_tol": None,
                            "coll": None,
                            "coll_tol": None,
                            "ppl_cor": None,
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
                    "p999": stats["p999"],
                    "rms_dp": float(rms_m.group(1)) if rms_m else None,
                    "rms_dp_tol": float(rms_m.group(2)) if rms_m else None,
                    "top1": float(top_m.group(1)) if top_m else None,
                    "top1_tol": float(top_m.group(2)) if top_m else None,
                    "coll": float(coll_m.group(1)) if coll_m else None,
                    "coll_tol": float(coll_m.group(2)) if coll_m else None,
                    "ppl_cor": float(cor_m.group(1)) if cor_m else None,
                    "speed": speed,
                }
            )

    # Baseline identification. The logits-generating run (``logits`` flag) has
    # no KLD stats; the baseline is the KLD rerun of the same combo, which
    # carries the measured numbers (KLD noise floor, RMS Δp, top-1) and the
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
                and (r["ctk"], r["ctv"], r["tail"])
                == (logits_run["ctk"], logits_run["ctv"], logits_run["tail"])
            ),
            None,
        )
        if rerun is not None:
            rerun["baseline"] = True
        else:
            logits_run["baseline"] = True

    # Labels depend on the whole log: the `` tN`` suffix appears on every run
    # iff --kv-tail-tokens is passed explicitly somewhere. Its absence is a
    # default (128 for KVarN, 0 otherwise), so keying off non-zero tails would
    # wrongly show suffixes for a KVarN-only log where the flag never appears.
    show_tail = bool(KV_TAIL_RE.search(text))
    for r in runs:
        r["label"] = _make_label(r["ctk"], r["ctv"], r["tail"], show_tail)

    # Projected context for the Context (MiB) column: explicit --ctx-size,
    # else the run's own context (logits run first, any run as fallback).
    if projected_ctx is None:
        projected_ctx = log_ctx or any_ctx or DEFAULT_CTX_SIZE

    # Total KV-cache size at the projected context (--ctx-size), if recognised.
    ref_m = MODEL_REF_RE.search(text)
    model_ref = ref_m.group(1) if ref_m else ""
    match = resolve_model(model_ref)
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

    return runs, common_params, projected_ctx


def _cost_axis(runs: list[dict], ctx_label: str = "256k") -> tuple[str, str, str]:
    """Pick the frontier / plot-x cost metric. When the model is recognised
    (``ctx_mib`` present on the runs) the Pareto frontier and the plot x-axis
    use the total KV-cache size at ``ctx_label`` -- so tail variants that share a
    bpw separate out -- and frontier points are drawn solid black. Otherwise both
    fall back to bpw. Returns (cost_key, x_axis_label, frontier_marker)."""
    if any(r.get("ctx_mib") is not None for r in runs):
        return "ctx_mib", f"Context size (MiB) @ {ctx_label}", "⚫"  # black circle
    return "size", "Size (bits / weight)", "\U0001f7e2"  # green circle


# Plotted statistics: key -> (display label, colour, higher_is_better).
# mean/p999/rms_dp are "lower is better" (frontier = running minimum); top1,
# coll and ppl_cor are "higher is better" (frontier = running maximum).
STAT_STYLE = {
    "mean": ("Mean KLD", "#e74c3c", False),
    "p999": ("99.9% KLD", "#9b59b6", False),
    "rms_dp": ("RMS Δp", "#16a085", False),
    "ppl_cor": ("Cor(ln(PPL(Q)), ln(PPL(base)))", "#2980b9", True),
    "top1": ("Top-1 (%)", "#e67e22", True),
    # Sits next to top1 in the fixed hue order; #c2185b is the only candidate
    # that clears the CVD and normal-vision floors against carrot (ΔE 19.4
    # deutan / 22.7 normal) without colliding with any other slot.
    "coll": ("Same sampled (%)", "#c2185b", True),
}
# Run-dict key holding the ± tolerance for stats that have one.
# Plot titles (h2 in HTML, figure title in SVG). Legend/dataset labels stay
# the short STAT_STYLE names.
STAT_TITLE = {
    "mean": "Mean KL Divergence (linear scale)",
    "p999": "99% KL Divergence (linear scale)",
    "rms_dp": "RMS Δp",
    "ppl_cor": "Perplexity (%)",
    "top1": "Top-1 (%)",
    "coll": "Same sampled token, temp 1 (%)",
}
STAT_TOL_KEY = {"rms_dp": "rms_dp_tol", "top1": "top1_tol", "coll": "coll_tol"}
# Decimal places for table cells and chart tooltips (stats with a tolerance).
STAT_DECIMALS = {"rms_dp": 3, "top1": 3, "coll": 3, "ppl_cor": 2}
# Suffix of the extra per-stat SVG files: BASENAME.<suffix>.svg
STAT_SVG_SUFFIX = {
    "mean": "mean-kld",
    "p999": "p999-kld",
    "rms_dp": "rms-dp",
    "ppl_cor": "ppl",
    "top1": "top1",
    "coll": "same-sampled",
}


def _stat_frontier(
    candidate_runs: list[dict], key: str, cost_key: str, higher_better: bool = False
) -> set[int]:
    """Pareto frontier of ``key`` over ``candidate_runs`` (grouped by cost so
    equal-cost runs compete). ``higher_better`` flips the extremum (used for
    Top-1). Runs with ``key is None`` are skipped."""
    from collections import defaultdict

    by_cost: dict[float, list[dict]] = defaultdict(list)
    for r in candidate_runs:
        if r[key] is None:
            continue
        by_cost[r[cost_key]].append(r)
    ids = set()
    best = float("-inf") if higher_better else float("inf")
    for s in sorted(by_cost):
        vals = [r[key] for r in by_cost[s]]
        extremum = max(vals) if higher_better else min(vals)
        if (higher_better and extremum > best) or (
            not higher_better and extremum < best
        ):
            for r in by_cost[s]:
                if r[key] == extremum:
                    ids.add(id(r))
            best = extremum
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

    # "Same sampled p" is optional (patched llama-perplexity only): drop the
    # column entirely rather than showing one full of blanks.
    has_coll = any(r.get("coll") is not None for r in runs)

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

    frontier_mean = _stat_frontier(eligible_runs, "mean", cost_key)
    frontier_p999 = _stat_frontier(eligible_runs, "p999", cost_key)
    frontier_rms = _stat_frontier(eligible_runs, "rms_dp", cost_key)
    frontier_top1 = _stat_frontier(eligible_runs, "top1", cost_key, higher_better=True)
    frontier_coll = _stat_frontier(eligible_runs, "coll", cost_key, higher_better=True)
    frontier_ppl_cor = _stat_frontier(
        eligible_runs, "ppl_cor", cost_key, higher_better=True
    )
    FRONTIERS = {
        "mean": frontier_mean,
        "p999": frontier_p999,
        "rms_dp": frontier_rms,
        "top1": frontier_top1,
        "coll": frontier_coll,
        "ppl_cor": frontier_ppl_cor,
    }
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
        if not r.get("baseline")
        and not r.get("logits")
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
        elif r.get("logits"):
            label += " (logits)"
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
            f"<td>{_fmt(r['p999'])}</td>"
            f"<td>{_fmt_tol(r.get('rms_dp'), r.get('rms_dp_tol'), 3)}</td>"
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
    def _point(r, key, flag, clamp_log, tol_key):
        y = r[key]
        if clamp_log and y <= 0:
            y = 1e-10
        return {
            "x": r[cost_key],
            "y": y,
            "_label": r["label"],
            "_frontier": flag,
            "_speed": r["speed"],
            "_speed_pct": r["speed_pct"],
            "_tol": r.get(tol_key) if tol_key else None,
        }

    def _line_ds(label, pts, color, extra=None):
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
        }
        if extra:
            d.update(extra)
        return d

    def _sub_ds(label, pts, color):
        return {
            "label": label,
            "data": pts,
            "borderColor": color,
            "backgroundColor": color,
            "showLine": False,
            "fill": False,
            "pointRadius": 4,
            "pointHoverRadius": 6,
            "pointBackgroundColor": color + "44",
            "_sub": True,
        }

    def _faint(color):
        r_, g_, b_ = (
            int(color[1:3], 16),
            int(color[3:5], 16),
            int(color[5:7], 16),
        )
        return f"rgba({r_}, {g_}, {b_}, 0.35)"

    def _build_datasets(stat_keys, clamp_log, combined):
        """Datasets for one chart over ``stat_keys``. ``combined`` selects the
        shared KLD suboptimal set (log chart); otherwise each stat uses its
        own frontier + speed cutoff."""
        hidden = suboptimal_ids if combined else slow_ids
        ds = []
        for key in stat_keys:
            label, color, _ = STAT_STYLE[key]
            frontier = FRONTIERS[key]
            tol_key = STAT_TOL_KEY.get(key)
            best_pts, sub_pts = [], []
            for r in sorted_runs:
                if r.get("aborted") or r[key] is None:
                    continue
                if combined:
                    flag = id(r) not in suboptimal_ids
                else:
                    flag = id(r) in frontier and id(r) not in slow_ids
                pt = _point(r, key, flag, clamp_log, tol_key)
                if id(r) in frontier and id(r) not in hidden:
                    best_pts.append(pt)
                else:
                    sub_pts.append(pt)
            ds.append(_line_ds(label, best_pts, color))
            ds.append(_sub_ds(label, sub_pts, color))
        # Extra lines: k=f16 (dashed) and v=f16 (dotted)
        for subset_name, dash_pat, pred in [
            ("k=f16", [6, 3], lambda r: r["ctk"] == "f16"),
            ("v=f16", [2, 3], lambda r: r["ctv"] == "f16"),
        ]:
            subset = [r for r in sorted_runs if pred(r) and not r.get("aborted")]
            if not subset:
                continue
            for key in stat_keys:
                label, color, _ = STAT_STYLE[key]
                tol_key = STAT_TOL_KEY.get(key)
                pts = []
                for r in subset:
                    if r[key] is None:
                        continue
                    if combined:
                        flag = id(r) not in suboptimal_ids
                    else:
                        flag = id(r) in FRONTIERS[key] and id(r) not in slow_ids
                    pts.append(_point(r, key, flag, clamp_log, tol_key))
                if not pts:
                    continue
                faint = _faint(color)
                # Reference lines clutter the legend: hide them from it
                # (the lines and their point labels stay on the plot).
                ds.append(
                    _line_ds(
                        f"{subset_name} {label}",
                        pts,
                        faint,
                        {
                            "pointRadius": 4,
                            "pointHoverRadius": 6,
                            "borderDash": dash_pat,
                            "_nolegend": True,
                        },
                    )
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
    x_max = max(r[cost_key] for r in sorted_runs) * 1.05  # 5% headroom past widest

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
    }
    for key, cid in [
        ("rms_dp", "chartRms"),
        ("top1", "chartTop1"),
        ("coll", "chartColl"),
        ("ppl_cor", "chartPplCor"),
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
                "datasets": _build_datasets([key], False, False),
            }
        )

    containers_html = ""
    for d in chart_defs:
        containers_html += (
            '<div class="chart-container">\n'
            '  <label class="label-toggle">'
            '<input type="checkbox" class="hide-labels-toggle" checked>'
            " Hide labels of non-frontier points</label>\n"
            f'  <h2 class="chart-title">{_esc(d["title"])}</h2>\n'
            f'  <canvas id="{d["id"]}" width="1000" height="600"></canvas>\n'
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

    html = (
        HTML_HEAD.replace("{chart_js_src}", chart_js_src)
        .replace("{ctx_label}", _esc(ctx_label))
        .replace("{coll_th}", "  <th>Same sampled (%)</th>\n" if has_coll else "")
    )
    html += common_html
    html += chunks_html
    html += tbl_rows
    html += HTML_TABLE_END
    html += containers_html
    html += (
        HTML_SCRIPT.replace("{chart_defs_json}", json.dumps(chart_defs))
        .replace("{x_max_json}", json.dumps(x_max))
        .replace("{x_axis_label_json}", json.dumps(x_axis_label))
        .replace("{baseline_label_json}", baseline_label_json)
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
  .chart-title { font-size: 1.05rem; margin: 0 0 10px 0; }
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
  <th>99.9% KLD</th>
  <th>RMS Δp</th>
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
Size = bpw(ctk) + bpw(ctv).  Each point labelled with its ctk/ctv pair.  Scroll wheel to zoom, drag to pan, double-click to reset.
The "hide labels" tickmarks are linked: toggling one toggles all five plots.</p>
<script>
var CHART_DEFS = {chart_defs_json};
var X_MAX = {x_max_json};
var X_AXIS_LABEL = {x_axis_label_json};
var BASELINE_LABEL = {baseline_label_json};
var HIDE_NON_FRONTIER = true;
var charts = [];

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
  // Point labels
  ctx.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillStyle = '#333';
  for (var d = 0; d < chart.data.datasets.length; d++) {
    var pts = chart.data.datasets[d].data;
    for (var p = 0; p < pts.length; p++) {
      var pt = pts[p];
      if (HIDE_NON_FRONTIER && !pt._frontier) continue;
      var xPos = xScale.getPixelForValue(pt.x);
      if (xPos < 0 || xPos > chart.width) continue;
      var y = yScale.getPixelForValue(pt.y) - 10;
      ctx.fillText(pt._label, xPos, y);
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

function attachZoomPan(canvas, chart, def) {
  var origXMin = 0, origXMax = X_MAX, origYMin = def.yMin, origYMax = def.yMax;

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
    // Y zoom
    if (def.yType === 'logarithmic') {
      var logMin = Math.log10(yScale.min);
      var logMax = Math.log10(yScale.max);
      var logRange = (logMax - logMin) * factor;
      var logCenter = Math.log10(yScale.getValueForPixel(mouseY));
      chart.options.scales.y.min = Math.pow(10, logCenter - logRange / 2);
      chart.options.scales.y.max = Math.pow(10, logCenter + logRange / 2);
    } else {
      var yRange = (yScale.max - yScale.min) * factor;
      var yCenter = yScale.getValueForPixel(mouseY);
      chart.options.scales.y.min = yCenter - yRange / 2;
      chart.options.scales.y.max = yCenter + yRange / 2;
    }
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
    // Y pan
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
}

CHART_DEFS.forEach(function(def) {
  var canvas = document.getElementById(def.id);
  var chart = new Chart(canvas.getContext('2d'), {
    type: 'scatter',
    data: { datasets: def.datasets },
    options: {
      responsive: true,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            filter: function(item, data) {
              var ds = data.datasets[item.datasetIndex];
              return !ds._sub && !ds._nolegend;
            }
          }
        },
        tooltip: {
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
              if (speed !== null && speed !== undefined) {
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
          min: 0,
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
  charts.push(chart);
});

// The "hide labels of non-frontier points" tickmark is replicated on every
// plot for convenience; toggling any one of them toggles all five plots.
document.querySelectorAll('.hide-labels-toggle').forEach(function(cb) {
  cb.addEventListener('change', function() {
    HIDE_NON_FRONTIER = cb.checked;
    document.querySelectorAll('.hide-labels-toggle').forEach(function(o) {
      o.checked = cb.checked;
    });
    charts.forEach(function(c) { c.draw(); });
  });
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
    cost_key: str = "size",
    higher_better: bool = False,
) -> tuple[set[int], set[int]]:
    """Return (frontier_ids, suboptimal_ids) for a given stat key.

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

    frontier = _stat_frontier(eligible_runs, key, cost_key, higher_better)
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
    frontier_mean, _ = _frontier_groups(sorted_runs, "mean", cost_key=cost_key)
    frontier_p999, _ = _frontier_groups(sorted_runs, "p999", cost_key=cost_key)

    sorted_runs = [r for r in sorted_runs if not r.get("logits")]
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
                # Reference lines stay out of the legend (lines and point
                # labels remain on the plot)
                label=None,
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


def generate_stat_svg(
    runs: list[dict],
    key: str,
    width=1000,
    height=600,
    dpi=100,
    speed_cutoff_factor: float = 0.33,
    ctx_label: str = "256k",
) -> str:
    """Generate a linear-Y SVG plot of a single stat (mean / p999 / rms_dp /
    top1 / ppl_cor) vs size using matplotlib. Stats with a tolerance (rms_dp,
    top1) get error-bar tick brackets. The frontier is per-stat (Top-1 and
    Perplexity: higher is better)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    label, color, higher_better = STAT_STYLE[key]
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

    frontier, _ = _frontier_groups(
        runs,
        key,
        speed_cutoff_factor,
        cost_key=cost_key,
        higher_better=higher_better,
    )
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
    ax.set_xlim(0, max(r[cost_key] for r in sorted_runs) * 1.05)

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

    if best_pts:
        best_pts.sort(key=lambda p: p[0])
        ax.plot(
            [p[0] for p in best_pts],
            [p[1] for p in best_pts],
            color=color,
            linewidth=1.5,
            marker="o",
            markersize=6,
            label=label,
            zorder=5,
        )
    if sub_pts:
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
    for subset_name, dash_style, pred in [
        ("k=f16", (0, (6, 3)), lambda r: r["ctk"] == "f16"),
        ("v=f16", (0, (2, 3)), lambda r: r["ctv"] == "f16"),
    ]:
        pts = sorted(
            [(r[cost_key], r[key]) for r in sorted_runs if pred(r)],
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
            # Single-stat plot: reference lines stay out of the legend
            label=None,
            zorder=4,
        )

    ax.legend(fontsize=8, loc="best", framealpha=0.9)
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

    # "Same sampled p" is optional (patched llama-perplexity only): drop the
    # column entirely rather than showing one full of blanks.
    has_coll = any(r.get("coll") is not None for r in runs)
    headers = [
        "ctk / ctv",
        "Size (bpw)",
        f"Context (MiB) @{ctx_label}",
        "Mean KLD",
        "99.9% KLD",
        "RMS Δp",
        "Top-1 (%)",
        *(["Same sampled (%)"] if has_coll else []),
        "Perplexity (%)",
        "Speed (tok/s)",
        "Speed (%)",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))

    for r in sorted(runs, key=lambda r: r[cost_key], reverse=True):
        label = r["label"]
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
        ctx_fmt = f"{r['ctx_mib']:,.0f}" if r.get("ctx_mib") is not None else ""
        lines.append(
            f"| {label}{frontier_mark} | {_fmt_size(r['size'])} | {ctx_fmt} |"
            f" {_fmt(r['mean'])} | {_fmt(r['p999'])} |"
            f" {_fmt_tol(r.get('rms_dp'), r.get('rms_dp_tol'), 3)} |"
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
        help="Output basename (no extension). Generates BASENAME.html, BASENAME.md, BASENAME.log-kld.svg and per-stat BASENAME.<stat>.svg (default: kv-kld-report)",
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
        default=None,
        metavar="N[k|M]",
        help="Projected context size for the Context (MiB) column; accepts "
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
    if args.whitelist:
        whitelisted = set(args.whitelist)
        runs = [
            r
            for r in runs
            if r["label"] in whitelisted or r.get("baseline") or r.get("logits")
        ]
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
        p999_fmt = f"{r['p999']:.6f}" if r["p999"] is not None else "       -"
        rms_fmt = f"{r['rms_dp']:.3f}" if r.get("rms_dp") is not None else "     -"
        top1_fmt = f"{r['top1']:.3f}" if r.get("top1") is not None else "     -"
        aborted_tag = " (aborted)" if r.get("aborted") else ""
        ctx_fmt = f"{r['ctx_mib']:>8,.0f} MiB" if r.get("ctx_mib") is not None else ""
        cor_fmt = f"{r['ppl_cor']:.2f}" if r.get("ppl_cor") is not None else "     -"
        print(
            f"  {r['label']:25s}{aborted_tag}  size={r['size']:6.2f} bpw  "
            f"ctx@{ctx_label}={ctx_fmt:>12s}  "
            f"mean={mean_fmt}  p999={p999_fmt}  "
            f"rms_dp={rms_fmt}  top1={top1_fmt}  ppl_cor={cor_fmt}  "
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

    # Extra per-stat linear SVGs: mean KLD, 99.9% KLD, RMS Δp, Top-1, Perplexity
    extra_plots: list[tuple[str, Path]] = []
    stat_keys = ["mean", "p999", "rms_dp", "top1", "ppl_cor"]
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
