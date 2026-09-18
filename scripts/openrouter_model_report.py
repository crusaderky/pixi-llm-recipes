"""OpenRouter model page -> per-provider CSV + interactive HTML report.

Parses one model page (e.g. https://openrouter.ai/z-ai/glm-5.3) by going through
OpenRouter's own frontend APIs instead of scraping HTML. The official public API
(https://openrouter.ai/api/v1/models/<author>/<slug>/endpoints) carries pricing,
uptime and - when authenticated with an API key - p50 latency/throughput, but no
benchmark scores, error rates, cache hit rates or data policies. The frontend
stats API used by openrouter.ai itself (same endpoints the model page calls) has
everything, unauthenticated, keyed by endpoint id:

    GET /api/frontend/v1/stats/endpoint?permaslug=P&variant=V
        one entry per provider endpoint: id, provider name/display/slug,
        quantization, per-endpoint data_policy (training / retainsPrompts),
        pricing (effective per-token rates + discount), pricing_json (the
        original *listed* prices, undiscounted) and 30-min p50 latency (ms) and
        throughput (tok/s) stats.
    GET /api/frontend/v1/stats/benchmark-scores?permaslug=P
        per-endpoint scores: gpqa_diamond, tau_bench_verified_* (0-1).
    GET /api/frontend/v1/stats/tool-call-error-rate?permaslug=P&timeRange=R
    GET /api/frontend/v1/stats/structured-output-error-rate?permaslug=P&timeRange=R
        daily percentage series per endpoint; averaged into one number.
    GET /api/frontend/v1/stats/effective-pricing?permaslug=P&variant=V&shape=v7
        observed weighted prices + cache hit rate per endpoint.
    GET /api/frontend/v1/stats/uptime-recent?permaslug=P
        daily uptime (%) for the last 3 days per endpoint.

The page URL only carries the model slug ("z-ai/glm-5.3"); the stats API needs
the dated "permaslug" ("z-ai/glm-5.3-20260816"), which is extracted from the
model page HTML (regex, no DOM parsing).

Privacy column (per endpoint data policy):
    private             zero retention (ZDR) and no training
    no ZDR              retains prompts (e.g. abuse scanning) but does not train
    trains on your data provider may train on the traffic

Effective price ($/M) blends the effective input and output prices at 99%/1%
(agentic token split; see AGENTIC_INPUT_SHARE for the consensus sources).
Effective input/output are the observed weighted prices from OpenRouter's
"Effective pricing" figure (provider discounts plus realized cache-hit mix),
falling back to the endpoint's post-discount token price when an endpoint has
no observed traffic.

Outputs (default dir ./openrouter-reports, override with -o):
    <author>__<model>.csv    one row per provider endpoint
    <author>__<model>.html   interactive report (3 plots, sortable table,
                             select/deselect, auto-generated ~/.pi/agent/models.json)

Usage:
    openrouter_model_report.py z-ai/glm-5.3
    openrouter_model_report.py https://openrouter.ai/z-ai/glm-5.3 -o /tmp
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

OR_BASE = "https://openrouter.ai"
FRONTEND = f"{OR_BASE}/api/frontend/v1"
UA = "Mozilla/5.0 (X11; Linux x86_64) openrouter-model-report/1.0"

DEFAULT_OUTPUT_DIR = Path("openrouter-reports")

# CSV columns. `provider` disambiguates multiple endpoints of the same provider
# only through the quantization column; the endpoint tag itself stays an HTML-only
# field (hover) and drives the models.json provider slugs.
CSV_COLUMNS = [
    "provider",
    "privacy",
    "effective price ($/M)",
    "quantization",
    "effective input ($/M)",
    "effective output ($/M)",
    "listed input ($/M)",
    "listed output ($/M)",
    "listed cache read ($/M)",
    "cache hit rate (%)",
    "latency p50 (s)",
    "throughput p50 (tok/s)",
    "uptime 3d (%)",
    "GPQA diamond (%)",
    "TAU-Bench (%)",
    "tool-call error rate (avg %)",
    "structured output error rate (avg %)",
]

# Blended price split for agentic workflows: 99% input / 1% output tokens.
# Consensus sources:
#   - Artificial Analysis Intelligence Index cost per task (segmented by token
#     type: input, cache hit, cache write, reasoning, answer): the input-side
#     share hovers around 99% (artificialanalysis.ai/evaluations/
#     artificial-analysis-intelligence-index).
#   - Community token audits (r/ClaudeAI 100M-token Claude Code log): 99.4%
#     of tokens were input.
#   - Vantage agentic-coding session analysis (vantage.sh): typical 50-turn
#     session is ~25:1 input:output (96% input) - the conservative outlier.
#   - Artificial Analysis' generic blended-price definition (7:2:1
#     cache-hit:input:output = 90% input-side, artificialanalysis.ai/methodology)
#     is a conservative lower bound, not agentic-specific.
# The measured agentic consensus is therefore ~99% input / 1% output.
AGENTIC_INPUT_SHARE = 0.99
AGENTIC_OUTPUT_SHARE = 0.01

# HTML-only row fields, in tooltip order after the CSV columns.
EXTRA_COLUMNS = [
    "tag",
    "context",
]


class FetchError(RuntimeError):
    pass


def http_json(url: str, *, retries: int = 3) -> dict | list:
    """GET a URL and parse the JSON body, with retries on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - retry any transport/parse error
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(1.5 * (attempt + 1))
    raise FetchError(f"GET {url} failed: {last_exc}") from last_exc


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Model URL, author/slug or dated permaslug -> (author/slug, permaslug-or-None)."""
    ref = ref.strip().rstrip("/")
    m = re.match(
        r"^(?:https?://)?(?:www\.)?openrouter\.ai/(.+?)(?:/llms\.txt)?/?$", ref
    )
    if m:
        ref = m.group(1)
    if ref.count("/") > 1:
        sys.exit(f"error: cannot parse model reference {ref!r}")
    return ref, None


def resolve_permaslug(model: str) -> tuple[str, str]:
    """Model page HTML -> (permaslug, variant). Raises on failure."""
    url = f"{OR_BASE}/{model}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
        # Final URL after redirects (openrouter.ai canonicalizes spellings).
        canonical = re.sub(
            r"^(https?://)?(www\.)?openrouter\.ai/", "", resp.geturl()
        ).strip("/")
    candidates = re.findall(r'permaslug\\?":\\?"([^"\\]+)', html)
    permaslug = None
    for base in (model, canonical):
        for c in candidates:
            if c == base or re.fullmatch(rf"{re.escape(base)}-\d+", c):
                permaslug = c
                break
        if permaslug:
            break
    if permaslug is None:
        # The page's internal model slug can differ from the public spelling
        # (e.g. anthropic/claude-opus-4-7 serves permaslugs of
        # anthropic/claude-4.7-opus). Fall back to the first dated,
        # variant-less permaslug on the page, which is the page's own model.
        dated = [c for c in candidates if re.fullmatch(r"[\w.~-]+/[\w.~-]+-\d+", c)]
        permaslug = dated[0] if dated else None
    if permaslug is None:
        raise FetchError(
            f"could not find permaslug for {model!r} in {url}; pass --permaslug"
        )
    vm = re.search(
        r'permaslug\\?":\\?"[^"\\]+\\?",\\"?variant\\?":\\?"([a-z0-9_-]+)', html
    )
    variant = vm.group(1) if vm else "standard"
    return permaslug, variant


def privacy(data_policy: dict | None) -> str:
    """Endpoint data policy -> privacy label.

    ZDR (zero data retention) = the provider keeps nothing; that is "private".
    Retaining prompts (abuse scanning etc.) without training is "no ZDR".
    Training on your data is the least private bucket, and implies no ZDR.
    """
    if not data_policy:
        return ""
    if data_policy.get("training") or data_policy.get("trainingOpenRouter"):
        return "trains on your data"
    if data_policy.get("retainsPrompts"):
        return "no ZDR"
    return "private"


def per_m(price: str | float | None) -> float | None:
    """Per-token price string -> $ per million tokens."""
    if price in (None, ""):
        return None
    try:
        return float(price) * 1e6
    except (TypeError, ValueError):
        return None


def pct(v: float | None, ndigits: int = 2) -> float | None:
    return None if v is None else round(v * 100, ndigits)


def avg_series(buckets: list, endpoint_id: str) -> float | None:
    """Mean of one endpoint's values across a {x, y: {endpoint_id: v}} series."""
    vals = [b["y"][endpoint_id] for b in buckets if endpoint_id in b["y"]]
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else None


def collect(
    model: str, permaslug_arg: str | None, variant_arg: str | None, time_range: str
) -> dict:
    """Fetch everything and return the report payload."""
    model, permaslug_hint = parse_model_ref(model)
    if permaslug_hint:
        permaslug, variant = permaslug_hint, (variant_arg or "standard")
    else:
        permaslug, variant = resolve_permaslug(model)
    if variant_arg:
        variant = variant_arg

    endpoints = http_json(
        f"{FRONTEND}/stats/endpoint?permaslug={permaslug}&variant={variant}"
    )
    if isinstance(endpoints, dict) and "data" in endpoints:
        endpoints = endpoints["data"]

    # Per-endpoint scores. Only the benchmark types the model actually has are
    # returned; gpqa_diamond and tau_bench_verified_* are the ones we map.
    scores = http_json(f"{FRONTEND}/stats/benchmark-scores?permaslug={permaslug}")
    if isinstance(scores, dict) and "data" in scores:
        scores = (
            scores["data"]["scores"]
            if isinstance(scores["data"], dict)
            else scores["data"]
        )

    tc_err = http_json(
        f"{FRONTEND}/stats/tool-call-error-rate?permaslug={permaslug}&timeRange={time_range}"
    )
    so_err = http_json(
        f"{FRONTEND}/stats/structured-output-error-rate?permaslug={permaslug}&timeRange={time_range}"
    )
    eff = http_json(
        f"{FRONTEND}/stats/effective-pricing?permaslug={permaslug}&variant={variant}&shape=v7"
    )
    uptime = http_json(f"{FRONTEND}/stats/uptime-recent?permaslug={permaslug}")
    if isinstance(uptime, dict) and "data" in uptime:
        uptime = uptime["data"]

    def unwrap(payload, key="data"):
        if isinstance(payload, dict) and key in payload:
            return payload[key]
        return payload

    tc_err = unwrap(tc_err)
    so_err = unwrap(so_err)
    eff_summaries = unwrap(eff).get("providerSummaries", [])

    by_id: dict[str, dict] = {}

    def bucket(endpoint_id: str) -> dict:
        return by_id.setdefault(endpoint_id, {})

    for s in scores:
        eid = s.get("endpoint_id")
        if not eid:
            continue  # "auto-routing" pseudo entry
        btype = s["benchmark_type"]
        if btype == "gpqa_diamond":
            bucket(eid)["gpqa"] = s["score"]
        elif btype.startswith("tau"):
            # tau_bench_verified_{airline,retail,telecom,...}: average the
            # variants the model actually has into one TAU-Bench column.
            bucket(eid).setdefault("tau_scores", []).append(s["score"])
    for eid, b in list(by_id.items()):
        if b.get("tau_scores"):
            b["tau"] = statistics.mean(b.pop("tau_scores"))

    for e in tc_err:
        for eid, v in e["y"].items():
            if v is not None:
                bucket(eid)["tc_err"] = v
    for e in so_err:
        for eid, v in e["y"].items():
            if v is not None:
                bucket(eid)["so_err"] = v
    for s in eff_summaries:
        if s.get("endpointId"):
            bucket(s["endpointId"])["cache_hit"] = s.get("cacheHitRate")
            bucket(s["endpointId"])["weighted_input"] = s.get("effectiveInputPrice")
            bucket(s["endpointId"])["weighted_output"] = s.get("effectiveOutputPrice")

    rows = []
    for e in endpoints:
        eid = e["id"]
        stats = e.get("stats") or {}
        pricing = e.get("pricing") or {}
        listed = e.get("pricing_json") or {}
        pj = {k.split(":", 1)[1] if ":" in k else k: v for k, v in listed.items()}
        lat = stats.get("p50_latency")
        tput = stats.get("p50_throughput")
        up_series = [
            d["uptime"] for d in (uptime.get(eid) or []) if d.get("uptime") is not None
        ]
        discount = pricing.get("discount") or 0
        cache_read_effective = per_m(pricing.get("input_cache_read"))
        summ = bucket(eid)
        # Provider label: display name plus the service-tier / region suffix the
        # provider slug carries (openai/flex -> OpenAI:flex, azure/us -> Azure:us).
        # A suffix that just restates the quantization (reka/fp8, baseten/fp4) is
        # not a tier — the quantization column already disambiguates those.
        slug = e.get("provider_slug") or e.get("provider_name") or ""
        display = e.get("provider_display_name") or e.get("provider_name") or ""
        suffix = slug.split("/", 1)[1] if "/" in slug else None
        if suffix and suffix == (e.get("quantization") or ""):
            suffix = None
        provider_label = f"{display}:{suffix}" if suffix else display
        # Effective input/output = the observed weighted price per M tokens
        # (OpenRouter's "Effective pricing" figure: provider discounts plus the
        # realized cache-hit mix), not the flat post-discount token rate. For
        # models whose providers share one list price the realized figure is the
        # only differentiator. Endpoints without traffic fall back to the
        # post-discount token price.
        eff_in = summ.get("weighted_input")
        eff_out = summ.get("weighted_output")
        if eff_in is None:
            eff_in = per_m(pricing.get("prompt"))
        if eff_out is None:
            eff_out = per_m(pricing.get("completion"))
        row = {
            "provider": provider_label,
            "quantization": e.get("quantization") or "",
            "eff_in": eff_in,
            "eff_out": eff_out,
            "lst_in": per_m(pj.get("prompt_tokens")),
            "lst_out": per_m(pj.get("completion_tokens")),
            "lst_cache": per_m(pj.get("cached_prompt_tokens")),
            "cache_hit": pct(bucket(eid).get("cache_hit")),
            "latency": round(lat / 1000, 2) if lat is not None else None,
            "throughput": round(tput, 1) if tput is not None else None,
            "uptime": round(statistics.mean(up_series), 2) if up_series else None,
            "gpqa": pct(bucket(eid).get("gpqa"), 1),
            "tau": pct(bucket(eid).get("tau"), 1),
            "tc_err": bucket(eid).get("tc_err"),
            "so_err": bucket(eid).get("so_err"),
            "privacy": privacy(e.get("data_policy")),
            "tag": e.get("provider_slug") or e.get("provider_name") or "",
            "context": e.get("context_length"),
            "endpoint_id": eid,
            "model": model,
            "permaslug": permaslug,
        }
        if discount:
            # display_pricing already carries the effective (post-discount) price;
            # recompute the listed side from it when pricing_json is missing.
            if row["lst_in"] is None and row["eff_in"] is not None and 0 < discount < 1:
                row["lst_in"] = round(row["eff_in"] / (1 - discount), 6)
            if (
                row["lst_out"] is None
                and row["eff_out"] is not None
                and 0 < discount < 1
            ):
                row["lst_out"] = round(row["eff_out"] / (1 - discount), 6)
            if (
                row["lst_cache"] is None
                and cache_read_effective is not None
                and 0 < discount < 1
            ):
                row["lst_cache"] = round(cache_read_effective / (1 - discount), 6)
        # Blended agentic price: AGENTIC_INPUT_SHARE of effective input +
        # AGENTIC_OUTPUT_SHARE of effective output, $ per million tokens.
        row["eff_price"] = (
            round(eff_in * AGENTIC_INPUT_SHARE + eff_out * AGENTIC_OUTPUT_SHARE, 4)
            if eff_in is not None and eff_out is not None
            else None
        )
        rows.append(row)

    if not rows:
        raise FetchError(f"no endpoints returned for {permaslug} (variant {variant!r})")

    # csv-display money fields: trim to 4 decimals
    for r in rows:
        for k in ("eff_in", "eff_out", "lst_in", "lst_out", "lst_cache"):
            if r[k] is not None:
                r[k] = round(r[k], 4)
        r["tc_err"] = round(r["tc_err"], 2) if r["tc_err"] is not None else None
        r["so_err"] = round(r["so_err"], 2) if r["so_err"] is not None else None
        r["cache_hit"] = (
            round(r["cache_hit"], 1) if r["cache_hit"] is not None else None
        )

    return {
        "model": model,
        "name": (endpoints[0].get("model") or {}).get("name") or model,
        "permaslug": permaslug,
        "variant": variant,
        "time_range": time_range,
        "columns": CSV_COLUMNS,
        "rows": rows,
    }


def write_csv(payload: dict, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for r in payload["rows"]:
            w.writerow(
                [
                    r["provider"],
                    r["privacy"],
                    r["eff_price"] if r["eff_price"] is not None else "",
                    r["quantization"],
                    r["eff_in"] if r["eff_in"] is not None else "",
                    r["eff_out"] if r["eff_out"] is not None else "",
                    r["lst_in"] if r["lst_in"] is not None else "",
                    r["lst_out"] if r["lst_out"] is not None else "",
                    r["lst_cache"] if r["lst_cache"] is not None else "",
                    r["cache_hit"] if r["cache_hit"] is not None else "",
                    r["latency"] if r["latency"] is not None else "",
                    r["throughput"] if r["throughput"] is not None else "",
                    r["uptime"] if r["uptime"] is not None else "",
                    r["gpqa"] if r["gpqa"] is not None else "",
                    r["tau"] if r["tau"] is not None else "",
                    r["tc_err"] if r["tc_err"] is not None else "",
                    r["so_err"] if r["so_err"] is not None else "",
                ]
            )


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — OpenRouter provider report</title>
<style>
:root {
  --bg: #f7f7f8; --card: #ffffff; --fg: #17171a; --muted: #6b6b74;
  --border: #e2e2e6; --accent: #3556e0; --danger: #c0392b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #101014; --card: #191922; --fg: #ececf1; --muted: #9494a3;
    --border: #2b2b38; --accent: #7b96ff; --danger: #ff6b5e;
  }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
.wrap { flex: 1; min-width: 0; padding: 16px 20px 60px; }
h1 { font-size: 20px; margin: 6px 0 2px; }
.sub { color: var(--muted); font-size: 12.5px; margin-bottom: 14px; }
.sub a { color: var(--accent); }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; }
button { font: inherit; padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--card); color: var(--fg); cursor: pointer; }
button:hover { border-color: var(--accent); }
.plots { display: flex; flex-wrap: wrap; gap: 14px; }
.plot-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 10px 10px 4px; }
.plot-card h3 { margin: 2px 4px 0; font-size: 13.5px; font-weight: 600; }
svg { display: block; user-select: none; }
svg .cross { stroke: currentColor; stroke-opacity: .35; stroke-dasharray: 3 3; pointer-events: none; display: none; }
svg .bgrad { pointer-events: none; }
.dot { cursor: pointer; }
.dot.off { fill-opacity: .18; stroke-opacity: .25; }
.dot:hover { stroke-width: 2.5; }
#tooltip { position: fixed; display: none; z-index: 10; pointer-events: none;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 12px; box-shadow: 0 4px 18px rgba(0,0,0,.25); font-size: 12.5px; min-width: 240px; }
#tooltip b { font-size: 13px; }
#tooltip table { border-collapse: collapse; width: 100%; }
#tooltip td { padding: 1px 0; }
#tooltip td:first-child { color: var(--muted); padding-right: 12px; }
#tooltip td:last-child { text-align: right; font-variant-numeric: tabular-nums; }
.tblwrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; margin-top: 8px; }
table.tbl { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
.tbl th, .tbl td { padding: 6px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.tbl th { position: sticky; top: 0; background: var(--card); cursor: pointer; text-align: right;
  font-weight: 600; border-bottom: 2px solid var(--border); }
.tbl th:first-child, .tbl td:first-child { text-align: left; position: sticky; left: 0; background: var(--card); }
.tbl th:hover { color: var(--accent); }
.tbl td { text-align: right; }
.tbl tr.row { cursor: pointer; }
.tbl tr.row.off td { opacity: .32; }
.tbl tr.row.off td:first-child { text-decoration: line-through; }
.tbl td.priv { font-size: 11.5px; }
.pill { display: inline-block; padding: 1px 7px; border-radius: 20px; font-size: 11px;
  border: 1px solid var(--border); color: var(--muted); }
.jsonbox { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.jsonbox pre { margin: 8px 0 0; font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; white-space: pre; }
.jsonhead { display: flex; align-items: center; gap: 10px; }
.jsonhead h2 { font-size: 15px; margin: 0; }
.jsonhead .sub { margin: 0; }
.notes { color: var(--muted); font-size: 12px; margin-top: 18px; }
.count { color: var(--muted); font-size: 12.5px; margin-left: auto; }
.layout { display: flex; align-items: flex-start; max-width: 1700px; margin: 0 auto; }
.sidebar { width: 235px; flex: none; position: sticky; top: 0; max-height: 100vh; overflow-y: auto;
  padding: 16px 10px; border-right: 1px solid var(--border); background: var(--card); }
.sbtitle { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); padding: 0 10px 8px; }
.sb-item { padding: 7px 10px; border-radius: 8px; cursor: pointer; }
.sb-item:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }
.sb-item.cur { background: color-mix(in srgb, var(--accent) 16%, transparent); }
.sb-item .nm { display: block; font-weight: 600; font-size: 13px; }
.sb-item .cnt { display: block; color: var(--muted); font-size: 11px; }
.sb-bottom { border-top: 1px solid var(--border); margin-top: 10px; padding-top: 10px; }
@media (max-width: 900px) {
  .layout { flex-direction: column; }
  .sidebar { position: static; width: auto; max-height: none; border-right: none;
    border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 4px; }
  .sbtitle { display: none; }
  .sb-item .cnt { display: inline; margin-left: 6px; }
  .content { padding: 16px; }
}
</style>
</head>
<body>
<div class="layout">
  <nav class="sidebar" id="sidebar">
    <div class="sbtitle">Models</div>
    <div id="sbitems"></div>
  </nav>
  <div class="wrap">
  <div id="modelview">
  <h1 id="curtitle"></h1>
  <div class="sub"><a id="curlink" target="_blank" rel="noopener"></a>
    · permaslug <code id="curperm"></code> · generated __GENERATED__ ·
    <span id="selcount"></span></div>

  <div class="toolbar">
    <button id="b-all">Select all</button>
    <button id="b-none">Deselect all</button>
    <button id="b-nozdr">Deselect providers without ZDR</button>
    <button id="b-train">Deselect providers that train on your data</button>
    <span class="count" id="btninfo"></span>
  </div>

  <div class="plots" id="plots"></div>

  <h2 style="font-size:15px; margin:22px 0 4px;">Providers <span class="sub">(click a row to deselect)</span></h2>
  <div class="tblwrap"><table class="tbl" id="tbl"></table></div>
  </div>

  <div id="summaryview" style="display:none">
    <h2 style="font-size:15px; margin:22px 0 4px;">Cheapest selected provider per model <span class="sub">(click a row or point to deselect that provider)</span></h2>
    <div class="plots" id="splots"></div>
    <div class="tblwrap" style="margin-top:8px"><table class="tbl" id="stbl"></table></div>
  </div>

  <div class="jsonbox" id="jsonbox" style="margin-top:22px">
    <div class="jsonhead">
      <h2>~/.pi/agent/models.json</h2>
      <span class="sub" id="curmodelnote"></span>
      <span class="sub">live preview — non-deselected providers in current sort order (openRouterRouting)</span>
      <button id="b-copy">Copy</button>
      <button id="b-dl">Download</button>
      <span id="copymsg" class="sub"></span>
    </div>
    <pre id="modelsjson"></pre>
  </div>

  <div class="notes">
    Sources: OpenRouter frontend stats API — stats/endpoint (pricing, data policy, p50 latency/throughput),
    benchmark-scores (GPQA Diamond, TAU-Bench), tool-call-error-rate + structured-output-error-rate
    (daily avg over __TIMERANGE__), effective-pricing (cache hit rate, and the
    effective input/output prices — the observed weighted price per M tokens,
    provider discounts plus the realized cache-hit mix; endpoints without traffic
    fall back to the post-discount token price), uptime-recent (3-day mean).
    Privacy is per endpoint data policy: <b>private</b> = zero retention (ZDR), <b>no ZDR</b> = retains
    prompts but does not train, <b>trains on your data</b> = provider may train on the traffic.
    <b>Effective price</b> blends effective input and output at __SPLIT_LABEL__ tokens — the measured
    consensus split for agentic workflows: Artificial Analysis Intelligence Index cost per task
    (segmented by token type) is ~99% input-side (artificialanalysis.ai); a 100M-token Claude Code
    audit measured 99.4% input (r/ClaudeAI); vantage.sh's ~25:1 illustrative session is the
    conservative outlier. AA's generic blended price (7:2:1 cache-hit:input:output = 90% input)
    is a lower bound, not an agentic measurement.
    models.json routing uses openRouterRouting.order (selected routing slugs, cheapest-first in the
    current sort order; tier/region suffixes like openai/flex kept so tier endpoints are opted in)
    plus allow_fallbacks: false, so a deselected provider can never serve — not even as a fallback.
    An ignore list is deliberately not emitted: a base-slug ignore ("openai") is matched against the
    whole provider family and would also kill selected tier endpoints. A model gets an entry once
    anything is deselected; deselecting everything records the intent with an empty order.
  </div>
  </div>
</div>
<div id="tooltip"></div>
<script>
"use strict";
const DATA = __DATA__;

const COLS = [
  {k: "provider",  label: "Provider",           str: true},
  {k: "privacy",   label: "Privacy",             str: true},
  {k: "eff_price", label: "Effective price $/M", num: true, fmt: money},
  {k: "quantization", label: "Quantization",    str: true},
  {k: "eff_in",    label: "Effective input $/M", num: true, fmt: money},
  {k: "eff_out",   label: "Effective output $/M", num: true, fmt: money},
  {k: "lst_in",    label: "Listed input $/M",    num: true, fmt: money},
  {k: "lst_out",   label: "Listed output $/M",   num: true, fmt: money},
  {k: "lst_cache", label: "Listed cache read $/M", num: true, fmt: money},
  {k: "cache_hit", label: "Cache hit rate %",    num: true, fmt: v => f1(v)},
  {k: "latency",   label: "Latency p50 s",       num: true, fmt: v => f2(v)},
  {k: "throughput", label: "Throughput tok/s",   num: true, fmt: v => f1(v)},
  {k: "uptime",    label: "Uptime 3d %",         num: true, fmt: v => f2(v)},
  {k: "gpqa",      label: "GPQA diamond %",      num: true, fmt: v => f1(v)},
  {k: "tau",       label: "TAU-Bench %",         num: true, fmt: v => f1(v)},
  {k: "tc_err",    label: "Tool-call err avg %", num: true, fmt: v => f2(v)},
  {k: "so_err",    label: "Structured out err avg %", num: true, fmt: v => f2(v)},
];

// Per-model UI state; switching models preserves selections and sort order.
const STATES = DATA.models.map(m => ({
  selected: new Set(m.rows.map(r => r.endpoint_id)),
  sortKey: "eff_price",   // cheapest blended agentic price first
  sortDir: 1,
}));
let cur = 0, ROWS = DATA.models[0].rows, selected = STATES[0].selected;
let sortKey = STATES[0].sortKey, sortDir = STATES[0].sortDir;

function setModel(i){
  STATES[cur] = {selected, sortKey, sortDir};
  cur = i;
  view = "model";
  ({selected, sortKey, sortDir} = STATES[cur]);
  ROWS = DATA.models[cur].rows;
  renderSidebar(); renderHeader(); renderAll();
}

function f1(v){ return v == null ? "—" : v.toFixed(1); }
function f2(v){ return v == null ? "—" : v.toFixed(2); }
function money(v){ return v == null ? "—" : "$" + (v >= 100 ? v.toFixed(1) : v.toFixed(4).replace(/0+$/,"").replace(/\.$/,"")); }
function esc(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

function providerSlug(r){ return (r.tag || r.provider).split("/")[0]; }
// Routing slug for models.json: OpenRouter base slugs match every endpoint of
// a provider EXCEPT service-tier/region endpoints (openai/flex, azure/us),
// which need their full tier-suffixed slug. Quantization suffixes (reka/fp8)
// are not endpoint identities — they collapse to the base slug.
function routingSlug(r){
  const t = r.tag || r.provider, parts = t.split("/");
  return parts.length === 2 && parts[1] === r.quantization ? parts[0] : t;
}
function rowsSorted(){
  if (!sortKey) return ROWS.slice();
  const col = COLS.find(c => c.k === sortKey);
  return ROWS.slice().sort((a,b) => {
    let va = a[sortKey], vb = b[sortKey];
    const an = va == null || va === "", bn = vb == null || vb === "";
    if (an && bn) return 0;
    if (an) return 1;               // missing values last, both directions
    if (bn) return -1;
    let c;
    if (col.num) c = va - vb;
    else c = String(va).localeCompare(String(vb));
    return c * sortDir;
  });
}

function colorOf(str){
  let h = 0;
  for (const ch of str) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360} 62% 52%)`;
}
function color(r){ return colorOf(providerSlug(r)); }
// Summary plots: one dot per model — color by model, not provider.
function colorFor(r){ return r.__modelname ? colorOf(r.__model) : color(r); }

/* ---------- plots ---------- */
function niceTicks(min, max, want){
  if (!isFinite(min) || !isFinite(max)) return {lo:0, hi:1, ticks:[0,1]};
  if (min === max) { min -= 1; max += 1; }
  const span = max - min;
  const raw = span / Math.max(2, Math.min(8, 5));
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step * 1e-9; v += step) ticks.push(Math.round(v * 1e8) / 1e8);
  return {lo, hi, ticks};
}

const PLOT_DEFS = [
  {id: "q", title: "GPQA Diamond vs TAU-Bench", x: "gpqa", y: "tau",
   xl: "GPQA Diamond (%)", yl: "TAU-Bench (%)", fx: f1, fy: f1,
   gx: +1, gy: +1},   // higher is better on both axes
  {id: "e", title: "Tool call vs structured output error rate", x: "tc_err", y: "so_err",
   xl: "Tool-call error rate (avg %)", yl: "Structured output error rate (avg %)", fx: f2, fy: f2,
   gx: -1, gy: -1},   // lower error rate is better
  {id: "p", title: "Effective price vs throughput", x: "eff_price", y: "throughput",
   xl: "Effective price (__SPLIT_LABEL__) ($/M)", yl: "Throughput (tok/s)", fx: v => "$"+f2(v), fy: f1,
   gx: -1, gy: +1},   // cheaper input, faster output
];

const W = 470, H = 330, M = {l: 62, t: 34, r: 18, b: 52};

function scales(def, rows){
  const pts = rows.filter(r => r[def.x] != null && r[def.y] != null);
  if (!pts.length) return null;
  const xs = pts.map(r => r[def.x]), ys = pts.map(r => r[def.y]);
  const sx = niceTicks(Math.min(...xs), Math.max(...xs));
  const sy = niceTicks(Math.min(...ys), Math.max(...ys));
  const pad = 0.02;
  const x2p = v => M.l + (W - M.l - M.r) * (v - sx.lo) / (sx.hi - sx.lo) * (1 - 2*pad) + (W - M.l - M.r) * pad;
  const y2p = v => H - M.b - (H - M.t - M.b) * (v - sy.lo) / (sy.hi - sy.lo);
  return {
    sx, sy,
    x2p: v => M.l + (W-M.l-M.r) * ((v - sx.lo) / (sx.hi - sx.lo)) * (1 - 2*pad) + (W-M.l-M.r)*pad,
    y2p,
    invx: px => sx.lo + (px - M.l - (W-M.l-M.r)*pad) * (sx.hi - sx.lo) / ((W-M.l-M.r)*(1-2*pad)),
    invy: py => sy.lo + (H-M.b-py) * (sy.hi - sy.lo) / (H - M.t - M.b),
  };
}

function drawPlots(hostId, rows){
  const host = document.getElementById(hostId);
  host.innerHTML = "";
  for (const def of PLOT_DEFS) {
    const card = document.createElement("div");
    card.className = "plot-card";
    const h = document.createElement("h3"); h.textContent = def.title; card.appendChild(h);
    const sc = scales(def, rows);
    if (!sc) { card.insertAdjacentHTML("beforeend", '<div style="padding:40px;color:var(--muted)">no data</div>'); host.appendChild(card); continue; }
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", W); svg.setAttribute("height", H);
    // Faint desirability gradient: red in the least desirable corner, green in
    // the most desirable one (direction depends on what each axis rewards).
    const wx = def.gx > 0 ? 0 : 1, wy = def.gy > 0 ? 1 : 0;
    const bx = 1 - wx, by = 1 - wy;
    const defs = document.createElementNS(ns, "defs");
    defs.innerHTML =
      `<linearGradient id="grad-${def.id}" x1="${wx}" y1="${wy}" x2="${bx}" y2="${by}">` +
      `<stop offset="0" stop-color="#e05252" stop-opacity=".22"/>` +
      `<stop offset="1" stop-color="#3fae6a" stop-opacity=".28"/></linearGradient>`;
    svg.appendChild(defs);
    const bg = document.createElementNS(ns, "rect");
    bg.setAttribute("x", M.l); bg.setAttribute("y", M.t);
    bg.setAttribute("width", W - M.l - M.r); bg.setAttribute("height", H - M.t - M.b);
    bg.setAttribute("fill", `url(#grad-${def.id})`);
    bg.setAttribute("class", "bgrad");
    svg.appendChild(bg);
    const grid = (v, p, axis) => {
      const l = document.createElementNS(ns, "line");
      if (axis === "x") l.setAttribute("x1",p), l.setAttribute("x2",p), l.setAttribute("y1",M.t), l.setAttribute("y2",H-M.b);
      else l.setAttribute("x1",M.l), l.setAttribute("x2",W-M.r), l.setAttribute("y1",p), l.setAttribute("y2",p);
      l.setAttribute("stroke","currentColor"); l.setAttribute("stroke-opacity",".12"); svg.appendChild(l);
      const t = document.createElementNS(ns, "text");
      t.setAttribute("fill","currentColor"); t.setAttribute("fill-opacity",".55");
      t.setAttribute("font-size","10.5");
      if (axis === "x") { t.setAttribute("x",p); t.setAttribute("y",H-M.b+16); t.setAttribute("text-anchor","middle"); }
      else { t.setAttribute("x",M.l-8); t.setAttribute("y",p+4); t.setAttribute("text-anchor","end"); }
      t.textContent = (axis==="x"?def.fx:def.fy)(v);
      svg.appendChild(t);
    };
    sc.sx.ticks.forEach(v => grid(v, sc.x2p(v), "x"));
    sc.sy.ticks.forEach(v => grid(v, sc.y2p(v), "y"));
    const ax = document.createElementNS(ns,"line");
    ax.setAttribute("x1",M.l); ax.setAttribute("x2",W-M.r); ax.setAttribute("y1",H-M.b); ax.setAttribute("y2",H-M.b);
    ax.setAttribute("stroke","currentColor"); ax.setAttribute("stroke-opacity",".4"); svg.appendChild(ax);
    const ay = document.createElementNS(ns,"line");
    ay.setAttribute("x1",M.l); ay.setAttribute("x2",M.l); ay.setAttribute("y1",M.t); ay.setAttribute("y2",H-M.b);
    ay.setAttribute("stroke","currentColor"); ay.setAttribute("stroke-opacity",".4"); svg.appendChild(ay);
    const xlab = document.createElementNS(ns,"text");
    xlab.setAttribute("x",(M.l+W-M.r)/2); xlab.setAttribute("y",H-8); xlab.setAttribute("text-anchor","middle");
    xlab.setAttribute("font-size","11.5"); xlab.setAttribute("fill","currentColor"); xlab.setAttribute("fill-opacity",".75");
    xlab.textContent = def.xl; svg.appendChild(xlab);
    const ylab = document.createElementNS(ns,"text");
    ylab.setAttribute("transform",`translate(14,${(M.t+H-M.b)/2}) rotate(-90)`);
    ylab.setAttribute("text-anchor","middle"); ylab.setAttribute("font-size","11.5");
    ylab.setAttribute("fill","currentColor"); ylab.setAttribute("fill-opacity",".75");
    ylab.textContent = def.yl; svg.appendChild(ylab);
    for (const r of rows) {
      if (r[def.x] == null || r[def.y] == null) continue;
      const c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", sc.x2p(r[def.x])); c.setAttribute("cy", sc.y2p(r[def.y]));
      c.setAttribute("r", 6); c.setAttribute("fill", colorFor(r)); c.setAttribute("stroke", "var(--bg)");
      c.setAttribute("class", "dot" + (selHas(r.endpoint_id) ? "" : " off"));
      c.dataset.id = r.endpoint_id;
      c.addEventListener("mousemove", ev => showTip(ev, r));
      c.addEventListener("mouseleave", hideTip);
      c.addEventListener("click", () => toggleId(r.endpoint_id));
      svg.appendChild(c);
    }
    // Crosshair: vertical + horizontal bars crossing under the mouse. Clicking
    // the background (not a point) deselects every plotted provider NOT in the
    // region between the cross and the plot's most desirable corner — i.e. any
    // provider at least one axis on the bad side of the cross.
    const cv = document.createElementNS(ns, "line");
    cv.setAttribute("class", "cross");
    const ch = document.createElementNS(ns, "line");
    ch.setAttribute("class", "cross");
    svg.appendChild(cv); svg.appendChild(ch);
    const crossPos = ev => {
      const rc = svg.getBoundingClientRect();
      return [ev.clientX - rc.left, ev.clientY - rc.top];
    };
    svg.addEventListener("mousemove", ev => {
      const [px, py] = crossPos(ev);
      const inX = px >= M.l && px <= W - M.r, inY = py >= M.t && py <= H - M.b;
      cv.style.display = ch.style.display = inX && inY ? "block" : "none";
      if (inX) { cv.setAttribute("x1", px); cv.setAttribute("x2", px);
        cv.setAttribute("y1", M.t); cv.setAttribute("y2", H - M.b); }
      if (inY) { ch.setAttribute("y1", py); ch.setAttribute("y2", py);
        ch.setAttribute("x1", M.l); ch.setAttribute("x2", W - M.r); }
    });
    svg.addEventListener("mouseleave", () => {
      cv.style.display = ch.style.display = "none";
    });
    svg.addEventListener("click", ev => {
      if (ev.target.classList && ev.target.classList.contains("dot")) return;
      const [px, py] = crossPos(ev);
      if (px < M.l || px > W - M.r || py < M.t || py > H - M.b) return;
      const cx = sc.invx(px), cy = sc.invy(py);
      const bad = (v, ref, goodHigh) =>
        v != null && (goodHigh ? v < ref : v > ref);
      setAll(false, r =>
        bad(r[def.x], cx, def.gx > 0) || bad(r[def.y], cy, def.gy > 0));
    });
    card.appendChild(svg);
    host.appendChild(card);
  }
}

/* ---------- tooltip ---------- */
const tt = document.getElementById("tooltip");
function showTip(ev, r){
  const cells = COLS.map(c => `<tr><td>${esc(c.label)}</td><td>${c.fmt ? c.fmt(r[c.k]) : esc(r[c.k] ?? "—")}</td></tr>`);
  const head = r.__modelname
    ? `<b>${esc(r.__modelname)}</b> <span style="color:var(--muted)">${esc(r.provider)} · ${esc(r.tag)}</span>`
    : `<b>${esc(r.provider)}</b> <span style="color:var(--muted)">${esc(r.tag)}${r.quantization ? " · " + esc(r.quantization) : ""}</span>`;
  tt.innerHTML = head + `<table>${cells.join("")}</table>`;
  tt.style.display = "block";
  const pad = 14, w = tt.offsetWidth, h = tt.offsetHeight;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > innerWidth - 8) x = ev.clientX - w - pad;
  if (y + h > innerHeight - 8) y = ev.clientY - h - pad;
  tt.style.left = x + "px"; tt.style.top = y + "px";
}
function hideTip(){ tt.style.display = "none"; }

/* ---------- table ---------- */
function renderTable(){
  const tbl = document.getElementById("tbl");
  const head = "<thead><tr>" + COLS.map(c => {
    const arrow = sortKey === c.k ? (sortDir > 0 ? " ▲" : " ▼") : "";
    return `<th data-k="${c.k}">${esc(c.label)}${arrow}</th>`;
  }).join("") + "</tr></thead>";
  const body = "<tbody>" + rowsSorted().map(r => {
    const off = selected.has(r.endpoint_id) ? "" : " off";
    const tds = COLS.map(c => {
      let v;
      if (c.fmt) v = c.fmt(r[c.k]);
      else v = r[c.k] == null || r[c.k] === "" ? "—" : esc(r[c.k]);
      const cls = c.k === "privacy" ? ' class="priv"' : "";
      return `<td${cls}>${v}</td>`;
    }).join("");
    return `<tr class="row${off}" data-id="${r.endpoint_id}">${tds}</tr>`;
  }).join("") + "</tbody>";
  tbl.innerHTML = head + body;
  tbl.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k;
    if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = 1; }
    renderTable(); updateModelsJson();
  }));
  tbl.querySelectorAll("tr.row").forEach(tr => tr.addEventListener("click", () =>
    toggleId(tr.dataset.id)));
  document.getElementById("selcount").textContent =
    `${selected.size}/${ROWS.length} providers selected`;
}

/* ---------- selection ---------- */
// endpoint_id -> owning model's selection set (a Set is shared with the live
// `selected` global for the active model, so mutations stay in sync).
const STATE_BY_ID = {};
DATA.models.forEach((m, i) =>
  m.rows.forEach(r => { STATE_BY_ID[r.endpoint_id] = STATES[i].selected; }));
function selHas(id){ return STATE_BY_ID[id].has(id); }
function toggleId(id){
  const set = STATE_BY_ID[id];
  set.has(id) ? set.delete(id) : set.add(id);
  renderAll();
}
// In summary mode the scope is each model's chosen provider; otherwise the
// current model's rows.
function scopeRows(){
  if (view !== "summary") return ROWS;
  return summaryRows().filter(r => r.endpoint_id);
}
function setAll(sel, pred){
  for (const r of scopeRows()) {
    if (pred && !pred(r)) continue;
    const set = STATE_BY_ID[r.endpoint_id];
    if (sel) set.add(r.endpoint_id); else set.delete(r.endpoint_id);
  }
  renderAll();
}

/* ---------- models.json ---------- */
let view = "model";   // 'model' | 'merge' | 'summary' — active sidebar tab

function routingFor(i){
  // Build one model's openRouterRouting from its saved state. Only the active
  // model's state lives in globals; switching models flushes it into STATES.
  const st = STATES[i];
  const col = COLS.find(c => c.k === st.sortKey);
  const sorted = DATA.models[i].rows.slice().sort((a, b) => {
    let va = a[st.sortKey], vb = b[st.sortKey];
    const an = va == null || va === "", bn = vb == null || vb === "";
    if (an && bn) return 0;
    if (an) return 1;
    if (bn) return -1;
    const c = col && col.num ? va - vb : String(va).localeCompare(String(vb));
    return c * st.sortDir;
  });
  const bySlug = new Map();
  for (const r of sorted) {
    const s = routingSlug(r);
    if (!bySlug.has(s)) bySlug.set(s, []);
    bySlug.get(s).push(r);
  }
  // Strict allow-list: order carries the selected routing slugs (tier/region
  // slugs opt their endpoint in), and allow_fallbacks: false stops the router
  // from ever serving a deselected provider as a fallback. An `ignore` list is
  // NOT emitted: a base-slug ignore ("openai") is matched against the whole
  // provider family and also kills selected tier endpoints like openai/flex.
  const order = [];
  for (const [slug, rows] of bySlug) {
    if (rows.some(r => st.selected.has(r.endpoint_id))) order.push(slug);
  }
  const routing = {};
  routing.order = order;               // empty when everything is deselected
  routing.allow_fallbacks = false;
  return routing;
}
// An override is emitted whenever the user deselected at least one provider
// (single-provider models never need one). With everything deselected the
// entry records the intent with an empty order — such a request fails at
// OpenRouter rather than silently re-enabling providers.
function hasOverride(m, i){
  if (m.rows.length <= 1) return false;
  return m.rows.some(r => !STATES[i].selected.has(r.endpoint_id));
}
function modelsJson(){
  STATES[cur] = {selected, sortKey, sortDir};   // flush the live model's state
  if (view === "merge") {
    const modelOverrides = {};
    DATA.models.forEach((m, i) => {
      if (!hasOverride(m, i)) return;
      modelOverrides[m.model] = { compat: { openRouterRouting: routingFor(i) } };
    });
    return { providers: { openrouter: { modelOverrides } } };
  }
  if (!hasOverride(DATA.models[cur], cur)) return null;
  return {
    providers: {
      openrouter: {
        modelOverrides: {
          [DATA.models[cur].model]: {
            compat: { openRouterRouting: routingFor(cur) }
          }
        }
      }
    }
  };
}
function updateModelsJson(){
  const j = modelsJson();
  const pre = document.getElementById("modelsjson");
  pre.textContent = j
    ? JSON.stringify(j, null, 2)
    : "— no models.json entry: every provider selected (or single-provider model) —";
}

/* ---------- sidebar + header ---------- */
/* ---------- summary tab: cheapest selected provider per model ---------- */
const S_COLS = [{k: "model", label: "Model", str: true}, ...COLS];
const S_STATE = {sortKey: "eff_price", sortDir: 1};   // cheapest model first

function summaryRows(){
  STATES[cur] = {selected, sortKey, sortDir};   // flush the live model's state
  return DATA.models.map((m, i) => {
    const st = STATES[i];
    const sel = m.rows.filter(r => st.selected.has(r.endpoint_id));
    const pool = (sel.some(r => r.eff_price != null)
      ? sel.filter(r => r.eff_price != null) : sel)
      .slice()
      .sort((a, b) => (a.eff_price ?? Infinity) - (b.eff_price ?? Infinity));
    const chosen = pool[0] || null;
    if (!chosen) return {model: m.model, __model: m.model, __modelname: m.name};
    return {...chosen, __model: m.model, __modelname: m.name};
  });
}
function renderSummary(){
  const rows = summaryRows();
  const cmp = (a, b) => {
    const col = S_COLS.find(c => c.k === S_STATE.sortKey);
    let va = a[S_STATE.sortKey], vb = b[S_STATE.sortKey];
    const an = va == null || va === "", bn = vb == null || vb === "";
    if (an && bn) return 0;
    if (an) return 1;
    if (bn) return -1;
    const c = col && col.num ? va - vb : String(va).localeCompare(String(vb));
    return c * S_STATE.sortDir;
  };
  const head = "<thead><tr>" + S_COLS.map(c => {
    const arrow = S_STATE.sortKey === c.k ? (S_STATE.sortDir > 0 ? " ▲" : " ▼") : "";
    return `<th data-k="${c.k}">${esc(c.label)}${arrow}</th>`;
  }).join("") + "</tr></thead>";
  const body = "<tbody>" + summaryRows().slice().sort(cmp).map(r => {
    const tds = S_COLS.map(c => {
      const v = c.fmt ? c.fmt(r[c.k])
        : (r[c.k] == null || r[c.k] === "" ? "—" : esc(r[c.k]));
      const cls = c.k === "privacy" ? ' class="priv"' : "";
      return `<td${cls}>${v}</td>`;
    }).join("");
    return r.endpoint_id
      ? `<tr class="row" data-id="${r.endpoint_id}">${tds}</tr>`
      : `<tr class="row">${tds}</tr>`;
  }).join("") + "</tbody>";
  const tbl = document.getElementById("stbl");
  tbl.innerHTML = head + body;
  tbl.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k;
    if (S_STATE.sortKey === k) S_STATE.sortDir *= -1;
    else { S_STATE.sortKey = k; S_STATE.sortDir = 1; }
    renderSummary();
  }));
  tbl.querySelectorAll("tr.row").forEach(tr => {
    if (tr.dataset.id) tr.addEventListener("click", () => toggleId(tr.dataset.id));
  });
  drawPlots("splots", summaryRows().filter(r => r.endpoint_id));
}

function renderSidebar(){
  const sb = document.getElementById("sidebar");
  if (DATA.models.length < 2) { sb.style.display = "none"; return; }
  const host = document.getElementById("sbitems");
  host.innerHTML = "";
  DATA.models.forEach((m, i) => {
    const d = document.createElement("div");
    d.className = "sb-item" + (i === cur && view === "model" ? " cur" : "");
    d.innerHTML = `<span class="nm">${esc(m.name)}</span>` +
      `<span class="cnt">${esc(m.model)} · ${m.rows.length} providers</span>`;
    d.addEventListener("click", () => setModel(i));
    host.appendChild(d);
  });
  // Bottom tabs: per-model summary and merged models.json.
  const sum = document.createElement("div");
  sum.className = "sb-item sb-bottom" + (view === "summary" ? " cur" : "");
  sum.innerHTML = `<span class="nm">Σ Cheapest summary</span>` +
    `<span class="cnt">best selected provider per model</span>`;
  sum.addEventListener("click", () => {
    view = view === "summary" ? "model" : "summary";
    renderSidebar(); renderHeader(); renderAll();
  });
  host.appendChild(sum);
  const tab = document.createElement("div");
  tab.className = "sb-item sb-bottom" + (view === "merge" ? " cur" : "");
  tab.innerHTML = `<span class="nm">⇉ Merged models.json</span>` +
    `<span class="cnt">all ${DATA.models.length} models · all selections</span>`;
  tab.addEventListener("click", () => {
    view = view === "merge" ? "model" : "merge";
    renderSidebar(); renderHeader(); renderAll();
  });
  host.appendChild(tab);
}
function renderHeader(){
  const m = DATA.models[cur];
  document.getElementById("curtitle").textContent = m.name;
  const link = document.getElementById("curlink");
  link.href = "https://openrouter.ai/" + m.model;
  link.textContent = "https://openrouter.ai/" + m.model;
  document.getElementById("curperm").textContent = m.permaslug;
  document.getElementById("curmodelnote").textContent = view === "merge"
    ? `merged preview — all ${DATA.models.length} models, per-model selections in current sort order`
    : "live preview — non-deselected providers in current sort order · for " + m.model;
  document.getElementById("modelsjson").setAttribute("data-model", m.model);
}

/* ---------- buttons ---------- */
document.getElementById("b-all").onclick = () => setAll(true);
document.getElementById("b-none").onclick = () => setAll(false);
document.getElementById("b-nozdr").onclick = () => setAll(false, r => r.privacy !== "private");
document.getElementById("b-train").onclick = () => setAll(false, r => r.privacy === "trains on your data");
document.getElementById("b-copy").onclick = async () => {
  if (!modelsJson()) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(modelsJson(), null, 2));
    document.getElementById("copymsg").textContent = "copied ✓";
  } catch (e) {
    document.getElementById("copymsg").textContent = "copy failed";
  }
  setTimeout(() => document.getElementById("copymsg").textContent = "", 1500);
};
document.getElementById("b-dl").onclick = () => {
  if (!modelsJson()) return;
  const blob = new Blob([JSON.stringify(modelsJson(), null, 2) + "\n"], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "models.json";
  a.click();
  URL.revokeObjectURL(a.href);
};

function renderAll(){
  document.getElementById("modelview").style.display = view === "model" ? "block" : "none";
  document.getElementById("summaryview").style.display = view === "summary" ? "block" : "none";
  // Models served by a single provider need no routing config at all.
  document.getElementById("jsonbox").style.display =
    view === "merge" || (view === "model" && hasOverride(DATA.models[cur], cur))
      ? "block" : "none";
  if (view === "summary") { renderSummary(); return; }
  renderTable(); drawPlots("plots", ROWS); updateModelsJson();
}
renderSidebar();
renderAll();
</script>
</body>
</html>
"""


def write_html(payloads: list, path: Path) -> None:
    data = json.dumps({"models": payloads}, ensure_ascii=False).replace("</", "<\\/")
    title = (
        f"{payloads[0]['name']} — provider report"
        if len(payloads) == 1
        else f"OpenRouter provider report ({len(payloads)} models)"
    )
    split_label = (
        f"{round(AGENTIC_INPUT_SHARE * 100)}% input/"
        f"{round(AGENTIC_OUTPUT_SHARE * 100)}% output"
    )
    html = (
        HTML_TEMPLATE.replace("__TITLE__", title)
        .replace("__GENERATED__", time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))
        .replace("__TIMERANGE__", payloads[0]["time_range"])
        .replace("__SPLIT_LABEL__", split_label)
        .replace("__DATA__", data)
    )
    path.write_text(html, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "models",
        nargs="+",
        help="one or more model URLs, author/slug refs or dated permaslugs "
        "(also accepts a single whitespace/comma-separated list)",
    )
    ap.add_argument(
        "--permaslug",
        help="skip page discovery, use this permaslug (single-model runs only)",
    )
    ap.add_argument("--variant", default=None, help="model variant (default: standard)")
    ap.add_argument(
        "--time-range",
        default="30d",
        help="window for error-rate averaging (default: 30d)",
    )
    ap.add_argument(
        "-o",
        "--output",
        default=None,
        help=f"output dir (default: {DEFAULT_OUTPUT_DIR})",
    )
    ap.add_argument("--open", action="store_true", help="open the report in a browser")
    args = ap.parse_args()

    # Accept `model1 model2 ...` and also a single "model1 model2" / "a,b" token,
    # so pixi task arg substitution works regardless of shell splitting.
    models = [
        ref for arg in args.models for ref in re.split(r"[\s,]+", arg.strip()) if ref
    ]
    if len(models) > 1 and args.permaslug:
        sys.exit("error: --permaslug only makes sense for a single model")

    out_dir = Path(args.output).expanduser() if args.output else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    payloads = []
    failures = []
    for model in models:
        try:
            payload = collect(model, args.permaslug, args.variant, args.time_range)
        except FetchError as e:
            print(f"error: {model}: {e}")
            failures.append(model)
            continue
        write_csv(payload, out_dir / f"{payload['model'].replace('/', '__')}.csv")
        payloads.append(payload)

    if not payloads:
        sys.exit("error: no model could be fetched; no report written")

    w = max(len(p["model"]) for p in payloads)
    for p in payloads:
        print(
            f"{p['model']:<{w}}  {len(p['rows']):>3} providers  "
            f"{p['permaslug']} ({p['variant']})"
        )

    stem = (
        payloads[0]["model"].replace("/", "__")
        if len(payloads) == 1
        else "openrouter-report"
    )
    html_path = out_dir / f"{stem}.html"
    write_html(payloads, html_path)

    print(f"{'CSVs':11} : {out_dir}/<author>__<model>.csv ({len(payloads)} models)")
    print(f"{'HTML report':11} : {html_path}")
    if failures:
        print(f"{'failed':11} : {', '.join(failures)}")
    if args.open:
        import webbrowser

        webbrowser.open(html_path.as_uri())


if __name__ == "__main__":
    try:
        main()
    except FetchError as e:
        sys.exit(f"error: {e}")
