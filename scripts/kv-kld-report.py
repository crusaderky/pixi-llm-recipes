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
import html as html_mod
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

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
    except Exception as e:
        print(f"Warning: could not fetch Chart.js ({e}), using CDN", file=sys.stderr)
        return f'<script src="{CHART_JS_CDN}"></script>'


# ---------------------------------------------------------------------------
#  Bytes-per-parameter conversion table
#  Includes block-overhead where relevant.
#  Values for standard llama.cpp types + Turboquant variants
#  (https://github.com/TheTom/llama-cpp-turboquant)
# ---------------------------------------------------------------------------
BPP = {
    "f32": 4.0,        # 32-bit float
    "f16": 2.0,        # 16-bit float
    "bf16": 2.0,       # bfloat16
    "q8_0": 1.0625,    # d(fp16)=2 + qs(int8)[32]=32 => 34/32
    "q5_1": 0.75,      # d(fp16)=2 + m(fp16)=2 + qh[4]=4 + qs[16]=16 => 24/32
    "q5_0": 0.6875,    # d(fp16)=2 + qh[4]=4 + qs[16]=16 => 22/32
    "q4_1": 0.625,     # d(fp16)=2 + m(fp16)=2 + qs[16]=16 => 20/32
    "q4_0": 0.5625,    # d(fp16)=2 + qs[16]=16 => 18/32
    "iq4_nl": 0.5625,  # d(fp16)=2 + qs[16]=16 => 18/32 (same size as q4_0)
    # TurboQuant+ KV cache (TheTom/llama-cpp-turboquant)
    # Values from https://github.com/TheTom/turboquant_plus
    "turbo4": 0.53125,   # 4.25 bits/val
    "turbo3": 0.4375,    # 3.5 bits/val
    "turbo2": 0.3125,    # 2.5 bits/val
    # K-quant types (block of 256) — not used for KV cache but kept for reference
    "q2_k": 0.3125,
    "q3_k_s": 0.3125,
    "q3_k_m": 0.375,
    "q3_k_l": 0.4375,
    "q4_k_s": 0.5,
    "q4_k_m": 0.5625,
    "q5_k_s": 0.625,
    "q5_k_m": 0.6875,
    "q6_k": 0.75,
    # Turboquant+ model quantization
    "tq3_1s": 0.5,
    "tq4_1s": 0.625,
}

BPP_LOOKUP = {k.lower(): v for k, v in BPP.items()}


def resolve_bpp(name: str) -> float:
    key = name.strip().lower()
    if key in BPP_LOOKUP:
        return BPP_LOOKUP[key]
    print(f"WARNING: unknown quant '{name}', using 1.0", file=sys.stderr)
    return 1.0


# ---------------------------------------------------------------------------
#  Log parser
# ---------------------------------------------------------------------------
CMD_RE = re.compile(r"llama-perplexity\s+.*?-ctk\s+(\S+)\s+-ctv\s+(\S+)")
CTX_SIZE_RE = re.compile(r"--ctx-size\s+(\d+)")
SECONDS_PER_PASS_RE = re.compile(r"kl_divergence: (\d+\.?\d*) seconds per pass")
TOTAL_MINUTES_RE = re.compile(r"(\d+\.?\d*)\s+minutes$")
FULL_CMD_RE = re.compile(r"^(llama-perplexity\s+.*)$", re.MULTILINE)
# Summary statistics from the "====== KL divergence statistics ======" block
SUMMARY_HDR = re.compile(r"^=+\s+KL divergence statistics\s+=+")
SUMMARY_LINE = re.compile(r"^\s*(Mean|Median|([\d.]+)%)\s+KLD:\s+([\d.-]+)")
# turboquant auto-asymmetric: the fork silently upgrades the K cache on high-GQA
# models (e.g. "upgrading K from turbo4 to q8_0"). Such a run does NOT measure
# its requested -ctk, and its cache size is wrong, so it must be excluded.
AUTO_ASYM_RE = re.compile(r"auto-asymmetric:.*?upgrading K from (\S+) to (\S+)")


def _common_params(text: str) -> str:
    """Extract CLI parameters (excluding -ctk/-ctv --kl-divergence --kl-divergence-base)."""
    skip_keys = {"--kl-divergence-base", "--kl-divergence", "-ctk", "-ctv"}
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


def parse_log(path: str) -> tuple[list[dict], str]:
    text = Path(path).read_text()
    sections = re.split(r"^-{30,}", text, flags=re.MULTILINE)
    common_params = _common_params(text)

    runs = []
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
            has_kld = bool(re.search(r'(?:^|\s)--kl-divergence(?:\s|$)', cmd_line))

            # Detect turboquant auto-asymmetric K-cache upgrade (bogus run).
            auto_asym = None
            for ln in lines:
                aam = AUTO_ASYM_RE.search(ln)
                if aam:
                    auto_asym = (aam.group(1), aam.group(2))  # (from, to)
                    break

            # Extract ctx-size
            ctx_match = CTX_SIZE_RE.search(cmd_line)
            ctx_size = int(ctx_match.group(1)) if ctx_match else 0

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
            elif total_minutes is not None:
                if n_chunks_tmp > 0:
                    speed = (n_chunks_tmp * ctx_size) / (total_minutes * 60)

            if not has_kld:
                # Baseline run — KLD = 0 by definition
                size = resolve_bpp(ctk) + resolve_bpp(ctv)
                runs.append({
                    "ctk": ctk,
                    "ctv": ctv,
                    "label": f"{ctk}/{ctv}",
                    "size": size,
                    "n_chunks": 0,
                    "mean": 0.0,
                    "median": 0.0,
                    "p90": 0.0,
                    "p999": 0.0,
                    "speed": speed,
                    "baseline": True,
                    "auto_asymmetric": auto_asym,
                })
                continue

            # Count chunks for display; parse summary for statistics
            # Find last chunk's index (not count — chunks 2-575 may be collapsed to [...])
            n_chunks = 0
            for ln in reversed(lines):
                m = re.match(r"^\s*(\d+)", ln)
                if m and "±" in ln:
                    n_chunks = int(m.group(1))
                    break
            stats = {"mean": 0.0, "median": 0.0, "p90": 0.0, "p999": 0.0}
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
                    elif pct == "90.0":
                        stats["p90"] = val
                elif key == "Mean":
                    stats["mean"] = val
                elif key == "Median":
                    stats["median"] = val

            if not in_summary:
                # Aborted run — no KLD stats. Still list in table with blank metrics.
                if is_aborted:
                    runs.append({
                        "ctk": ctk,
                        "ctv": ctv,
                        "label": f"{ctk}/{ctv}",
                        "size": resolve_bpp(ctk) + resolve_bpp(ctv),
                        "n_chunks": 0,
                        "mean": None,
                        "median": None,
                        "p90": None,
                        "p999": None,
                        "speed": speed,
                        "aborted": True,
                        "auto_asymmetric": auto_asym,
                    })
                    continue
                print(f"WARNING: --kl-divergence flag found but no summary stats for {ctk}/{ctv}", file=sys.stderr)
                continue

            runs.append(
                {
                    "ctk": ctk,
                    "ctv": ctv,
                    "label": f"{ctk}/{ctv}",
                    "size": resolve_bpp(ctk) + resolve_bpp(ctv),
                    "n_chunks": n_chunks,
                    "mean": stats["mean"],
                    "median": stats["median"],
                    "p90": stats["p90"],
                    "p999": stats["p999"],
                    "speed": speed,
                    "auto_asymmetric": auto_asym,
                }
            )
    return runs, common_params


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
    return f"{v:.4f}"


def generate_html(runs: list[dict], common_params: str = "", chart_js_src: str = "",
                  speed_cutoff_factor: float = 0.33) -> str:
    sorted_runs = sorted(runs, key=lambda r: r["size"])

    # ---- Pareto frontiers (separate per stat; group-by-size so same-size runs compete) ----
    def _frontier(key: str, candidate_runs: list[dict]) -> set[int]:
        from collections import defaultdict
        by_size: dict[float, list[dict]] = defaultdict(list)
        for r in candidate_runs:
            by_size[r["size"]].append(r)
        ids = set()
        best = float("inf")
        for s in sorted(by_size):
            min_kld = min(r[key] for r in by_size[s])
            if min_kld < best:
                for r in by_size[s]:
                    if r[key] == min_kld:
                        ids.add(id(r))
                best = min_kld
        return ids

    baseline_run = next((r for r in runs if r.get("baseline")), None)
    speed_cutoff = baseline_run["speed"] * speed_cutoff_factor if baseline_run and baseline_run["speed"] is not None else None
    eligible_runs = [r for r in runs if not r.get("aborted") and (speed_cutoff is None or r["speed"] is None or r["speed"] >= speed_cutoff)]

    frontier_mean = _frontier("mean", eligible_runs)
    frontier_p999 = _frontier("p999", eligible_runs)

    # A run is suboptimal if it's NOT on either frontier, OR if it's too slow
    suboptimal_ids = {id(r) for r in runs
                      if id(r) not in frontier_mean and id(r) not in frontier_p999}
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
            f'<code>llama-perplexity {_esc(common_params)}</code>'
            "</div>\n"
        )

    # ---- table sorted by size descending ----
    tbl_rows = ""
    for r in sorted(runs, key=lambda r: r["size"], reverse=True):
        label = r["label"]
        if r.get("baseline"):
            label += " (baseline)"
        elif r.get("aborted"):
            label += " (aborted)"
        cls = ' class="suboptimal"' if id(r) in suboptimal_ids else ""
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}" if r["speed_pct"] is not None else "N/A"
        tbl_rows += (
            f"<tr{cls}>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_fmt_size(r['size'])}</td>"
            f"<td>{_fmt(r['mean'])}</td>"
            f"<td>{_fmt(r['median'])}</td>"
            f"<td>{_fmt(r['p90'])}</td>"
            f"<td>{_fmt(r['p999'])}</td>"
            f"<td>{speed_fmt}</td>"
            f"<td>{pct_fmt}</td>"
            f"</tr>\n"
        )

    # ---- chart datasets ----
    # Split each stat into frontier (connected) + non-frontier (floating)
    # Each stat uses its own frontier
    stat_specs = [
        ("Mean KLD", "mean", "#e74c3c", frontier_mean),
        ("99.9% KLD", "p999", "#9b59b6", frontier_p999),
    ]

    ds_parts = []
    for label, key, color, frontier in stat_specs:
        best_pts = []
        sub_pts = []
        for r in sorted_runs:
            if r.get("aborted"):
                continue
            y = r[key] if r[key] > 0 else 1e-10
            pt = {"x": r["size"], "y": y, "_label": r["label"], "_suboptimal": id(r) in suboptimal_ids,
                    "_speed": r["speed"], "_speed_pct": r["speed_pct"]}
            # A point is on the "best line" only if it's in the calculated frontier 
            # AND it's not too slow.
            if id(r) in frontier and id(r) not in suboptimal_ids:
                best_pts.append(pt)
            else:
                sub_pts.append(pt)

        # Best line
        ds_parts.append(
            "{ label: '%s', data: %s,"
            " borderColor: '%s', backgroundColor: '%s',"
            " showLine: true, fill: false, tension: 0,"
            " pointRadius: 6, pointHoverRadius: 8 }"
            % (label, json.dumps(best_pts), color, color)
        )
        # Suboptimal floating points (hidden from legend via filter)
        ds_parts.append(
            "{ label: '%s', data: %s,"
            " borderColor: '%s', backgroundColor: '%s',"
            " showLine: false, fill: false,"
            " pointRadius: 4, pointHoverRadius: 6,"
            " pointBackgroundColor: '%s44', _sub: true }"
            % (label, json.dumps(sub_pts), color, color, color)
        )
    # ---- extra lines: k=f16 (dashed) and v=f16 (dotted) ----
    for subset_name, dash_pat, subset_runs in [
        ("k=f16", [6, 3], [r for r in sorted_runs if r["ctk"] == "f16" and not r.get("aborted")]),
        ("v=f16", [2, 3], [r for r in sorted_runs if r["ctv"] == "f16" and not r.get("aborted")]),
    ]:
        if not subset_runs:
            continue
        for stat_label, key, base_color, _ in stat_specs:
            r_, g_, b_ = int(base_color[1:3], 16), int(base_color[3:5], 16), int(base_color[5:7], 16)
            faint = f"rgba({r_}, {g_}, {b_}, 0.35)"
            pts = sorted(
                [{"x": r["size"], "y": r[key] if r[key] > 0 else 1e-10, "_label": r["label"], "_suboptimal": id(r) in suboptimal_ids}
                 for r in subset_runs],
                key=lambda p: p["x"]
            )
            ds_parts.append(
                "{ label: '%s %s', data: %s,"
                " borderColor: '%s', backgroundColor: '%s',"
                " showLine: true, fill: false, tension: 0,"
                " pointRadius: 4, pointHoverRadius: 6,"
                " borderDash: %s }"
                % (subset_name, stat_label, json.dumps(pts),
                   faint, faint, json.dumps(dash_pat))
            )

    datasets_js = ",\n      ".join(ds_parts)



    # Compute y-axis range so smallest non-zero point sits at 1/3 from bottom
    # (baseline at y=1e-10 shoots out of plot)
    max_y = max(r[key] for key in ["mean", "p999"]
                for r in sorted_runs if r[key] is not None and r[key] > 0)
    min_nonzero = min(r[key] for key in ["mean", "p999"]
                      for r in sorted_runs if r[key] is not None and r[key] > 0)
    import math
    # Position min_nonzero at 1/10 from bottom of log scale (90% down):
    # (log(min_nonzero) - log(y_min)) / (log(max_y) - log(y_min)) = 0.1
    log_max = math.log10(max_y)
    log_min = math.log10(min_nonzero)
    log_y_min = (10 * log_min - log_max) / 9
    y_min = 10 ** log_y_min
    y_max = max_y * 1.15  # 15% headroom above max
    x_max = max(r["size"] for r in sorted_runs)

    baseline_label = next((r["label"] for r in sorted_runs if r.get("baseline")), None)
    baseline_label_json = json.dumps(baseline_label) if baseline_label else "null"

    # Chunk count — same for all non-baseline runs
    n_chunks = next((r["n_chunks"] for r in sorted_runs if r.get("n_chunks", 0) > 0), None)
    chunks_html = (
            '<div class="common-params">'
            '<h2>Chunks per run</h2>'
            f'{n_chunks}'
            '</div>\n'
        ) if n_chunks else ""

    html = HTML_HEAD.replace("{chart_js_src}", chart_js_src)
    html += common_html
    html += chunks_html
    html += tbl_rows
    html += HTML_MID % (y_min, y_max, x_max, baseline_label_json, datasets_js)
    if speed_cutoff is not None:
        pct = speed_cutoff_factor * 100
        html += f'<p class="note">Runs with speed &lt; {pct:.0f}% of baseline excluded from frontier determination.</p>\n'
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
  .label-toggle { display: block; margin-bottom: 8px; font-size: 0.9rem; cursor: pointer; user-select: none; }
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
  <th>Size (B/param)</th>
  <th>Mean KLD</th>
  <th>Median KLD</th>
  <th>90.0% KLD</th>
  <th>99.9% KLD</th>
  <th>Speed (tok/s)</th>
  <th>Speed (%)</th>
</tr>
</thead>
<tbody>
"""

HTML_MID = """\
</tbody>
</table>
<div class="chart-container">
  <label class="label-toggle">
    <input type="checkbox" id="hideNonFrontierLabels" checked>
    Hide labels of non-frontier points
  </label>
  <canvas id="kldChart" width="1000" height="600"></canvas>
</div>
<p class="note">Y-axis: log scale.  Size = bytes-per-parameter(ctk) + bytes-per-parameter(ctv).  Each point labelled with its ctk/ctv pair.  Scroll wheel to zoom, drag to pan, double-click to reset.</p>
<script>
var Y_MIN  = %s;
var Y_MAX  = %s;
var X_MAX  = %s;
var BASELINE_LABEL = %s;

var ctx = document.getElementById('kldChart').getContext('2d');
var chart = new Chart(ctx, {
  type: 'scatter',
  data: {
    datasets: [%s]
  },
  options: {
    responsive: true,
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          filter: function(item, data) {
            return !data.datasets[item.datasetIndex]._sub;
          }
        }
      },
      tooltip: {
        callbacks: {
          label: function(ctx) {
            var lbl = ctx.raw._label || '';
            var speed = ctx.raw._speed;
            var pct = ctx.raw._speed_pct;
            var speedStr = '';
            if (speed !== null && speed !== undefined) {
              speedStr = '  ' + speed.toFixed(1) + ' tok/s';
              if (pct !== null && pct !== undefined) {
                speedStr += ' (' + pct.toFixed(1) + '%%)';
              }
            }
            return lbl + '  ' + ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(6) + speedStr;
          }
        }
      },
    },
    scales: {
      x: {
        title: { display: true, text: 'Size (bytes / parameter)' },
        type: 'linear',
        min: 0,
        max: X_MAX
      },
      y: {
        title: { display: true, text: 'KL Divergence' },
        type: 'logarithmic',
        min: Y_MIN,
        max: Y_MAX
      }
    },
    elements: {
      point: {
        radius: 6,
        hoverRadius: 8
      }
    }
  },
  plugins: [{
    afterDraw: function(chart) {
      var ctx = chart.ctx;
      var xScale = chart.scales.x;
      var yScale = chart.scales.y;
      var hideNonFrontier = document.getElementById('hideNonFrontierLabels') &&
                            document.getElementById('hideNonFrontierLabels').checked;
      ctx.save();
      ctx.font = '11px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillStyle = '#333';
      // Label every data point at its own position
      for (var d = 0; d < chart.data.datasets.length; d++) {
        var pts = chart.data.datasets[d].data;
        for (var p = 0; p < pts.length; p++) {
          var pt = pts[p];
          if (hideNonFrontier && pt._suboptimal) continue;
          var xPos = xScale.getPixelForValue(pt.x);
          if (xPos < 0 || xPos > chart.width) continue;
          var y = yScale.getPixelForValue(pt.y) - 10;
          ctx.fillText(pt._label, xPos, y);
        }
      }
      ctx.restore();
      // Baseline annotation — bottom-right corner
      if (BASELINE_LABEL) {
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
  }]
});

// ---- Zoom / Pan (native, no external plugins) ----
var origXMin = 0, origXMax = X_MAX, origYMin = Y_MIN, origYMax = Y_MAX;

(function() {
  var canvas = document.getElementById('kldChart');

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
    // Y zoom (log scale — operate in log space)
    var logMin = Math.log10(yScale.min);
    var logMax = Math.log10(yScale.max);
    var logRange = (logMax - logMin) * factor;
    var logCenter = Math.log10(yScale.getValueForPixel(mouseY));
    chart.options.scales.y.min = Math.pow(10, logCenter - logRange / 2);
    chart.options.scales.y.max = Math.pow(10, logCenter + logRange / 2);
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
    // Y pan (log)
    var yPixRange = yScale.bottom - yScale.top;
    if (yPixRange > 0) {
      var yLogMin = Math.log10(panYMin);
      var yLogMax = Math.log10(panYMax);
      var yLogRange = yLogMax - yLogMin;
      var yNewLogMin = yLogMin + (dy / yPixRange) * yLogRange;
      var yNewLogMax = yLogMax + (dy / yPixRange) * yLogRange;
      chart.options.scales.y.min = Math.pow(10, yNewLogMin);
      chart.options.scales.y.max = Math.pow(10, yNewLogMax);
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
})();

document.getElementById('hideNonFrontierLabels').addEventListener('change', function() {
  chart.draw();
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
def _frontier_groups(runs: list[dict], key: str, speed_cutoff_factor: float = 0.33
                    ) -> tuple[set[int], set[int]]:
    """Return (frontier_ids, suboptimal_ids) for a given stat key."""
    from collections import defaultdict
    
    # Only consider runs that are not too slow
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    speed_cutoff = None
    if baseline_run and baseline_run["speed"] is not None:
        speed_cutoff = baseline_run["speed"] * speed_cutoff_factor

    eligible_runs = [r for r in runs if not r.get("aborted")]
    if speed_cutoff is not None:
        eligible_runs = [r for r in eligible_runs if r["speed"] is None or r["speed"] >= speed_cutoff]

    by_size: dict[float, list[dict]] = defaultdict(list)
    for r in eligible_runs:
        by_size[r["size"]].append(r)
    frontier = set()
    best = float("inf")
    for s in sorted(by_size):
        min_kld = min(r[key] for r in by_size[s])
        if min_kld < best:
            for r in by_size[s]:
                if r[key] == min_kld:
                    frontier.add(id(r))
            best = min_kld
    return frontier, {id(r) for r in runs if id(r) not in frontier}


def generate_plot_svg(runs: list[dict], width=1000, height=600, dpi=100,
                      speed_cutoff_factor: float = 0.33) -> str:
    """Generate an SVG plot of KLD vs size using matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import FancyBboxPatch

    sorted_runs = sorted(runs, key=lambda r: r["size"])
    sorted_runs = [r for r in sorted_runs if not r.get("aborted")]

    frontier_mean, _ = _frontier_groups(sorted_runs, "mean")
    frontier_p999, _ = _frontier_groups(sorted_runs, "p999")
    suboptimal_ids = {id(r) for r in runs
                      if id(r) not in frontier_mean and id(r) not in frontier_p999}

    fig, ax = plt.subplots(figsize=(width / dpi, height / dpi), dpi=dpi)
    ax.set_xlabel("Size (bytes / parameter)", fontsize=11)
    ax.set_ylabel("KL Divergence", fontsize=11)
    ax.set_yscale("log")
    ax.set_xlim(0, max(r["size"] for r in sorted_runs) * 1.05)

    # Y range: same logic as chart.js
    max_y = max(r[key] for key in ["mean", "p999"] for r in sorted_runs if r[key] > 0)
    min_nonzero = min(r[key] for key in ["mean", "p999"] for r in sorted_runs if r[key] > 0)
    import math
    log_max = math.log10(max_y)
    log_min = math.log10(min_nonzero)
    log_y_min = (10 * log_min - log_max) / 9
    y_min = 10 ** log_y_min
    ax.set_ylim(y_min, max_y * 1.5)

    ax.tick_params(labelsize=9)
    ax.grid(True, which="both", ls=":", alpha=0.3)

    stat_specs = [
        ("Mean KLD", "mean", "#e74c3c", frontier_mean),
        ("99.9% KLD", "p999", "#9b59b6", frontier_p999),
    ]

    for label, key, color, frontier in stat_specs:
        best_pts = [(r["size"], r[key] if r[key] > 0 else 1e-10, r) for r in sorted_runs if id(r) in frontier and id(r) not in suboptimal_ids]
        sub_pts = [(r["size"], r[key] if r[key] > 0 else 1e-10, r) for r in sorted_runs if id(r) not in frontier or id(r) in suboptimal_ids]

        # Sort by size for connected line
        best_pts.sort(key=lambda p: p[0])

        if best_pts:
            xs = [p[0] for p in best_pts]
            ys = [p[1] for p in best_pts]
            ax.plot(xs, ys, color=color, linewidth=1.5, marker="o",
                    markersize=6, label=label, zorder=5)

        if sub_pts:
            xs = [p[0] for p in sub_pts]
            ys = [p[1] for p in sub_pts]
            ax.scatter(xs, ys, color=color, alpha=0.25, s=20, zorder=3)

        # Labels
        for pt_list, alpha in [(best_pts, 1.0), (sub_pts, 0.4)]:
            for x, y, r in pt_list:
                if id(r) in suboptimal_ids and alpha < 1:
                    ax.annotate(r["label"], (x, y), textcoords="offset points",
                                xytext=(0, -12), fontsize=6, ha="center", va="top",
                                alpha=alpha, zorder=10)
                else:
                    ax.annotate(r["label"], (x, y), textcoords="offset points",
                                xytext=(0, -12), fontsize=6.5, ha="center", va="top",
                                alpha=alpha, zorder=10)

    # Extra lines: k=f16 (dashed), v=f16 (dotted)
    for subset_name, dash_style, subset_runs_list in [
        ("k=f16", (0, (6, 3)), [r for r in sorted_runs if r["ctk"] == "f16"]),
        ("v=f16", (0, (2, 3)), [r for r in sorted_runs if r["ctv"] == "f16"]),
    ]:
        if not subset_runs_list:
            continue
        for stat_label, key, base_color, _ in stat_specs:
            r_, g_, b_ = int(base_color[1:3], 16), int(base_color[3:5], 16), int(base_color[5:7], 16)
            pts = sorted(
                [(r["size"], r[key] if r[key] > 0 else 1e-10, r) for r in subset_runs_list],
                key=lambda p: p[0]
            )
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            ax.plot(xs, ys, color=(r_/255, g_/255, b_/255, 0.35),
                    linewidth=1, linestyle=dash_style, marker="o",
                    markersize=4, label=f"{subset_name} {stat_label}", zorder=4)

    # Baseline annotation
    baseline_label = next((r["label"] for r in sorted_runs if r.get("baseline")), None)
    if baseline_label:
        ax.annotate(f"{baseline_label} (baseline) \u2193",
                    xy=(0.98, 0.04), xycoords="axes fraction",
                    fontsize=10, ha="right", va="bottom", color="#888",
                    zorder=20)

    ax.legend(fontsize=8, loc="upper left", framealpha=0.9)
    fig.tight_layout()

    from io import StringIO
    buf = StringIO()
    fig.savefig(buf, format="svg", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ---------------------------------------------------------------------------
#  Markdown report
# ---------------------------------------------------------------------------
def generate_markdown(runs: list[dict], common_params: str = "",
                      html_path: str | None = None,
                      plot_path: str | None = None,
                      repo: str | None = None,
                      branch: str = "main",
                      speed_cutoff_factor: float = 0.33) -> str:
    """Generate a Markdown report with table and SVG plot (image ref + xref)."""
    sorted_runs = sorted(runs, key=lambda r: r["size"], reverse=True)

    frontier_mean, _ = _frontier_groups(sorted_runs, "mean", speed_cutoff_factor)
    frontier_p999, _ = _frontier_groups(sorted_runs, "p999", speed_cutoff_factor)
    suboptimal_ids = {id(r) for r in runs
                      if id(r) not in frontier_mean and id(r) not in frontier_p999}

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

    n_chunks = next((r["n_chunks"] for r in sorted_runs if r.get("n_chunks", 0) > 0), None)
    if n_chunks:
        lines.append(f"**Chunks per run:** {n_chunks}")
        lines.append("")

    lines.append("| ctk / ctv | Size (B/param) | Mean KLD | Median KLD | 90.0% KLD | 99.9% KLD | Speed (tok/s) | Speed (%) |")
    lines.append("|---|---|---|---|---|---|---|---|")

    for r in sorted(runs, key=lambda r: r["size"], reverse=True):
        label = r["label"]
        if r.get("baseline"):
            label += " (baseline)"
        elif r.get("aborted"):
            label += " (aborted)"
        frontier_mark = " 🟢" if id(r) not in suboptimal_ids else ""
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}" if r["speed_pct"] is not None else "N/A"
        lines.append(
            f"| {label}{frontier_mark} | {_fmt_size(r['size'])} | {_fmt(r['mean'])} |"
            f" {_fmt(r['median'])} | {_fmt(r['p90'])} | {_fmt(r['p999'])} | {speed_fmt} | {pct_fmt} |"
        )

    lines.append("")

    # SVG image + xref links
    if repo and plot_path:
        owner, repo_name = repo.split("/", 1)
        # plot_path is a forward-slash path relative to the git repo root
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}"
        # htmlpreview proxies raw github; passes through with correct mime
        html_preview = f"https://htmlpreview.github.io/?{raw_base}/{html_path}"
        lines.append(f"![KLD Plot]({raw_base}/{plot_path})")
        lines.append("")
        lines.append(f"[Interactive report]({html_preview})")
        lines.append("")
    elif plot_path:
        lines.append(f"![KLD Plot]({plot_path})")
        lines.append("")
        xref_parts = []
        if html_path:
            xref_parts.append(f"[Interactive report]({html_path})")
        if xref_parts:
            lines.append(" | ".join(xref_parts))
            lines.append("")

    # Note about speed cutoff
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    if baseline_run and baseline_run["speed"] is not None:
        pct = speed_cutoff_factor * 100
        lines.append(f"> Runs with speed < {pct:.0f}% of baseline excluded from frontier determination.")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Generate KLD report from kv-perplexity output")
    ap.add_argument("log", nargs="?", default="kv-perplexity.log", help="Input log (default: kv-perplexity.log)")
    ap.add_argument("-o", "--output", default="kv-kld-report",
                    help="Output basename (no extension). Generates BASENAME.html, BASENAME.md, BASENAME.svg (default: kv-kld-report)")
    ap.add_argument("--whitelist", nargs="+", metavar="CTK/CTV",
                    help="Only include these ctk/ctv combos (e.g. q4_0/q4_0). "
                         "f16/f16 always included even if not listed.")
    ap.add_argument("--repo", metavar="owner/repo",
                    help="GitHub repository (e.g. crusaderky/pixi-llm-recipes). "
                         "Auto-detected from git remote if omitted.  Generates "
                         "raw.githubusercontent.com URLs for SVG and "
                         "htmlpreview.github.io link for HTML report.")
    ap.add_argument("--branch", default=None,
                    help="GitHub branch (default: auto-detect from git, fallback main; "
                         "used only with --repo or auto-detected repo)")
    ap.add_argument("--speed-cutoff", type=float, default=0.33,
                    help="Fraction of baseline speed; runs slower than this are excluded "
                         "from frontier determination (default: 0.33)")
    args = ap.parse_args()

    # Auto-detect GitHub repo from git remote
    repo = args.repo
    branch = args.branch or "main"
    repo_root: Path | None = None
    if not repo:
        try:
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=5
            )
            if remote.returncode == 0:
                m = re.match(
                    r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/.]+?)(?:\.git)?$",
                    remote.stdout.strip()
                )
                if m:
                    repo = m.group(1)
                    # Auto-detect branch too
                    br = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        capture_output=True, text=True, timeout=5
                    )
                    if br.returncode == 0 and br.stdout.strip() != "HEAD":
                        branch = br.stdout.strip()
        except Exception:
            pass

    # Find git repo root to compute repo-relative paths for embedded URLs
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5
        )
        if toplevel.returncode == 0:
            repo_root = Path(toplevel.stdout.strip())
    except Exception:
        repo_root = None

    if repo:
        print(f"GitHub repo detected: {repo} (branch: {branch})")


    log_path = Path(args.log)
    if not log_path.exists():
        print(f"ERROR: {log_path} not found", file=sys.stderr)
        sys.exit(1)

    runs, common_params = parse_log(args.log)

    # Drop turboquant auto-asymmetric runs: the fork silently upgraded the K cache
    # to q8_0, so their -ctk label and cache size are wrong. Refuse to report them.
    bogus = [r for r in runs if r.get("auto_asymmetric")]
    if bogus:
        bar = "=" * 79
        print(bar)
        print("!!! TURBOQUANT AUTO-ASYMMETRIC DETECTED — SKIPPING BOGUS RUNS !!!")
        print(bar)
        print("The turboquant fork silently upgraded the K cache for the following runs")
        print("(high-GQA auto-asymmetric). Their -ctk label and cache size are wrong, so")
        print("they are EXCLUDED from the table and the plot:")
        print("")
        for r in bogus:
            frm, to = r["auto_asymmetric"]
            print(f"    {r['label']:20s}  (K silently upgraded {frm} -> {to})")
        print("")
        print("To measure these configs correctly, re-run kv-perplexity with the feature")
        print("disabled (and delete their stale sections from the log first):")
        print("    TURBO_AUTO_ASYMMETRIC=0 pixi run kv-perplexity -c <your-config.yaml>")
        print(bar)
        runs = [r for r in runs if not r.get("auto_asymmetric")]

    # Normalize speed to baseline (baseline = 100%)
    baseline_run = next((r for r in runs if r.get("baseline")), None)
    b_speed = baseline_run["speed"] if baseline_run and baseline_run["speed"] is not None else None
    for r in runs:
        if b_speed is not None and r["speed"] is not None:
            r["speed_pct"] = (r["speed"] / b_speed) * 100.0
        else:
            r["speed_pct"] = None

    runs_unfiltered = runs
    if args.whitelist:
        whitelisted = set(args.whitelist)
        runs = [r for r in runs if r["label"] in whitelisted or r["label"] == "f16/f16"]
    if not runs:
        print("No KLD runs found" + (" (none match whitelist)" if args.whitelist else ""), file=sys.stderr)
        sys.exit(1)

    print(f"Parsed {len(runs)} KLD runs:" + (" (filtered by whitelist)" if args.whitelist and len(runs) != len(runs_unfiltered) else ""))
    for r in runs:
        speed_fmt = f"{r['speed']:.1f}" if r["speed"] is not None else "N/A"
        pct_fmt = f"{r['speed_pct']:.1f}%" if r["speed_pct"] is not None else "N/A"
        mean_fmt = f"{r['mean']:.6f}" if r['mean'] is not None else "       -"
        median_fmt = f"{r['median']:.6f}" if r['median'] is not None else "       -"
        p90_fmt = f"{r['p90']:.6f}" if r['p90'] is not None else "       -"
        p999_fmt = f"{r['p999']:.6f}" if r['p999'] is not None else "       -"
        aborted_tag = " (aborted)" if r.get("aborted") else ""
        print(
            f"  {r['label']:25s}{aborted_tag}  size={r['size']:.4f} B/p  "
            f"mean={mean_fmt}  median={median_fmt}  "
            f"p90={p90_fmt}  p999={p999_fmt}  "
            f"speed={speed_fmt:>7s}  {pct_fmt:>7s}  ({r['n_chunks']} chunks)"
        )

    # Append extensions to -o value as-is (don't strip any existing extension)
    prefix = str(args.output)
    html_path = Path(prefix + ".html")
    md_path = Path(prefix + ".md")
    plot_path = Path(prefix + ".svg")

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

    # Relative URL from markdown to HTML and SVG (sibling, both work locally + on github.com)
    html_rel = str(html_path.relative_to(md_path.parent, walk_up=True))
    plot_rel = str(plot_path.relative_to(md_path.parent, walk_up=True))

    # Generate HTML
    chart_js_src = _fetch_chart_js()
    speed_cutoff_factor = args.speed_cutoff
    html = generate_html(runs, common_params, chart_js_src, speed_cutoff_factor)
    html_path.write_text(html)
    print(f"\nHTML -> {html_path}")

    # Generate Markdown + SVG
    svg = generate_plot_svg(runs, speed_cutoff_factor=speed_cutoff_factor)
    plot_path.write_text(svg)
    print(f"SVG  -> {plot_path}")

    md = generate_markdown(runs, common_params, html_path=html_repo_rel,
                            plot_path=plot_repo_rel, repo=repo, branch=branch,
                            speed_cutoff_factor=speed_cutoff_factor)
    md_path.write_text(md)
    print(f"MD   -> {md_path}")


if __name__ == "__main__":
    main()
