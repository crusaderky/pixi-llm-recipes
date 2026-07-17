"""Doc-00 REPORT helpers.

Every headline run writes ``REPORT-<run_id>.md`` that states the score and
compares it to the external reference narratively (decision Q8: no numeric
tolerance gates). Every REPORT prints, per arm, the one-line summary:

    <endpoint URL> / <model id> -> <score>  (mean <mean_s>s/<unit>, n=<n>)
      config: <model.config>          # second line, only when config is non-empty
"""

from . import RESULTS_DIR


def one_liner(endpoint, model, score, mean_s, n, unit="item", config="") -> str:
    line = f"{endpoint} / {model} -> {score}  (mean {mean_s}s/{unit}, n={n})"
    if config:
        line += f"\n  config: {config}"
    return line


def write(run_id: str, title: str, arm_lines, body: str = "", out_dir=None):
    """Write REPORT-<run_id>.md with the one-line summaries + a narrative body."""
    out_dir = out_dir or RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"REPORT-{run_id}.md"
    chunks = [f"# {title}", ""]
    chunks += list(arm_lines)
    if body:
        chunks += ["", body.rstrip()]
    p.write_text("\n".join(chunks) + "\n")
    return p
