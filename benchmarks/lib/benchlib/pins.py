"""Parse the pin files under docs/benchmarks/pins/ (doc 00 §Subset pins).

Pin-file convention:
  - one item per line; blank lines are ignored;
  - a line whose first non-space char is '#' is COMMENTED OUT (excluded);
  - an inline '# rationale' comment may follow an item (values never contain
    '#', so the first '#' always starts the comment).

Two shapes appear across the panel:
  - LIST pins (scicode ids, tb2 task ids): bare tokens, one item per line.
  - KEY=VALUE pins (livecodebench window; ifbench/evalplus/cruxeval selection):
    ``key = value  # comment`` lines.
"""

import hashlib
import pathlib


def _strip_inline_comment(line: str) -> str:
    return line.split("#", 1)[0].rstrip()


def uncommented_lines(path) -> list[str]:
    """Return the payload of every non-blank, non-commented line (comment stripped)."""
    out = []
    for raw in pathlib.Path(path).read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        val = _strip_inline_comment(raw).strip()
        if val:
            out.append(val)
    return out


def list_items(path) -> list[str]:
    """LIST pin -> first token of each uncommented line (e.g. tb2 task ids)."""
    return [ln.split()[0] for ln in uncommented_lines(path)]


def scicode_ids(path) -> list[int]:
    """SciCode pin -> sorted unique list[int] of main-problem ids."""
    return sorted({int(tok) for tok in list_items(path)})


def kv(path) -> dict[str, str]:
    """KEY=VALUE pin -> dict of uncommented ``key = value`` assignments."""
    d: dict[str, str] = {}
    for ln in uncommented_lines(path):
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        d[k.strip()] = v.strip()
    return d


def sha256(path) -> str:
    """Hex sha256 of the pin file, for the ledger ``pin.sha256`` field."""
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
