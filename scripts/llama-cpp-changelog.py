#!/usr/bin/env python3
"""Dump a deterministic llama.cpp changelog between two git refs.

Works against any llama.cpp fork (default: the active, uncommented `fork:`
in the source recipe; override with `--repo owner/name`). Handles both
upstream `bNNNN` tags and beellama `vX.Y.Z` semver tags (preview releases
are ignored by default resolution).

Resolves defaults from pixi-recipes/llama-cpp-source/recipe.yaml:
  - from: the `# Last sync with main at <tag>` comment, else the active
    fork's `version`, else the commented-out `# version:` variant.
  - to:   the latest upstream release tag of the selected repo.

Output (markdown) sections:
  1. Header (from -> to, dates, counts)
  2. Tags in range (chronological, with release dates)
  3. PRs merged in range (number, title, URL, body excerpt)
  4. Direct commits with no associated PR (short hash, subject, URL)

GitHub access: no `gh` CLI dependency. Uses urllib with an optional
GITHUB_TOKEN/GH_TOKEN env var for the REST + GraphQL calls (PR section).
When no token is set, the script falls back to a local commits-only git
clone (`--filter=tree:0`) cached under
`~/.cache/llama-cpp-changelog/llama.cpp.git`. Tags, commit subjects, and
dates are read from git instead of the REST API (no rate limit). The PR
section is skipped without auth (it requires GraphQL).

Usage:
    scripts/llama-cpp-changelog.py [FROM] [TO]
    scripts/llama-cpp-changelog.py --from b9688 --to b9789
    scripts/llama-cpp-changelog.py b9789            # from default, to = b9789
    scripts/llama-cpp-changelog.py                 # both defaults
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_REPO = "ggml-org/llama.cpp"
# Overridden in main() from --repo or the active fork in the source recipe.
REPO = DEFAULT_REPO
REPO_URL = f"https://github.com/{REPO}"
RECIPE = (
    Path(__file__).resolve().parent.parent
    / "pixi-recipes"
    / "llama-cpp-source"
    / "recipe.yaml"
)

# Upstream uses `bNNNN`; beellama uses `vX.Y.Z`. `preview-vX.Y.Z` and other
# pre-release tags deliberately do not match.
TAG_RE = re.compile(r"^(b\d+|v\d+\.\d+\.\d+)$")


def _tag_key(name: str) -> tuple[int, ...] | None:
    """Sort key for a release tag (`bNNNN` or `vX.Y.Z`); None if not a tag."""
    if not TAG_RE.match(name):
        return None
    return tuple(int(p) for p in re.findall(r"\d+", name))


def _git_cache() -> Path:
    """Per-fork cache for the commits-only git clone (no-auth fallback)."""
    override = os.environ.get("LLAMA_CPP_CHANGELOG_CACHE")
    if override:
        return Path(override)
    repo_name = REPO.split("/", 1)[1]
    return Path.home() / ".cache" / "llama-cpp-changelog" / f"{repo_name}.git"


# --------------------------------------------------------------------------- #
# GitHub API access
# --------------------------------------------------------------------------- #
def _urllib_get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        remaining = e.headers.get("X-RateLimit-Remaining", "?")
        if e.code in (403, 429) and remaining == "0":
            sys.exit(
                f"GitHub rate limit exceeded (unauthenticated). "
                f"Set GITHUB_TOKEN/GH_TOKEN in the env, or run without auth "
                f"(the script falls back to a local git clone). URL: {url}"
            )
        sys.exit(f"HTTP {e.code} for {url}:\n{e.read().decode(errors='replace')[:500]}")


def api_get(path: str, query: dict | None = None) -> object:
    """GET a REST endpoint via urllib (token-authenticated if available)."""
    url = f"https://api.github.com/{path.lstrip('/')}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return _urllib_get_json(url)


def graphql(query: str) -> dict:
    """Run a GraphQL query via urllib (requires GITHUB_TOKEN/GH_TOKEN)."""
    url = "https://api.github.com/graphql"
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit(
            "GraphQL requires GITHUB_TOKEN/GH_TOKEN in the env. "
            "Without it, the PR section is skipped (tags/commits still print via git)."
        )
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# --------------------------------------------------------------------------- #
# Git fallback (no GitHub auth) — commits-only cached clone
# --------------------------------------------------------------------------- #
def _have_token() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))


def _git_cache_ready() -> bool:
    return (_git_cache() / "config").exists()


def _ensure_git_cache() -> None:
    """Ensure a commits-only clone of the repo exists and is up to date."""
    cache = _git_cache()
    if not _git_cache_ready():
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        subprocess.run(
            [
                "git",
                "clone",
                "--bare",
                "--filter=tree:0",
                f"https://github.com/{REPO}.git",
                str(tmp),
            ],
            check=True,
        )
        tmp.replace(cache)
    # Refresh refs (commits-only fetch is cheap).
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(cache),
            "fetch",
            "--filter=tree:0",
            "origin",
            "+refs/tags/*:refs/tags/*",
            "+refs/heads/*:refs/heads/*",
        ],
        check=True,
        capture_output=True,
    )


def _git(args: list[str], capture: bool = True) -> str:
    _ensure_git_cache()
    r = subprocess.run(
        ["git", "--git-dir", str(_git_cache()), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def _git_tag_sha(tag: str) -> str | None:
    out = _git(["rev-list", "-n", "1", tag], capture=True).strip()
    return out or None


def _git_tag_date(tag: str) -> str | None:
    # Committer date (ISO) of the commit the tag points at.
    out = _git(["log", "-1", "--format=%cI", tag]).strip()
    return out[:10] if out else None


def _git_tags_in_range(lo: tuple[int, ...], hi: tuple[int, ...]) -> list[str]:
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/tags"])
    names: list[str] = []
    for line in out.splitlines():
        k = _tag_key(line.strip())
        if k and lo < k <= hi:
            names.append(line.strip())
    names.sort(key=lambda n: _tag_key(n) or ())
    return names


def _git_latest_tag() -> str | None:
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/tags"])
    best, best_k = None, None
    for line in out.splitlines():
        k = _tag_key(line.strip())
        if k and (best_k is None or k > best_k):
            best, best_k = line.strip(), k
    return best


def _git_compare_commits(from_ref: str, to_ref: str) -> list[dict]:
    """Commits in from..to as dicts with sha/subject/date (REST-shaped)."""
    sep = "\x01"
    out = _git(
        [
            "log",
            "--format=%H" + sep + "%s" + sep + "%cI",
            f"{from_ref}..{to_ref}",
        ]
    )
    commits: list[dict] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, subject, date = line.split(sep, 2)
        commits.append(
            {
                "sha": sha,
                "commit": {
                    "message": subject,
                    "committer": {"date": date},
                },
            }
        )
    # compare API returns oldest-first; git log is newest-first → reverse.
    commits.reverse()
    return commits


# --------------------------------------------------------------------------- #
# Recipe parsing — default `from`
# --------------------------------------------------------------------------- #
def resolve_default_repo() -> str:
    """Active (uncommented) fork from the source recipe, else upstream mainline."""
    try:
        text = RECIPE.read_text()
    except OSError:
        return DEFAULT_REPO
    m = re.search(r"^\s*fork:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else DEFAULT_REPO


def resolve_default_from() -> str:
    text = RECIPE.read_text()
    m = re.search(r"#\s*Last sync with main at (\S+)", text)
    if m:
        return m.group(1)
    # Active (uncommented) fork/version pair in the context block
    blk = re.search(r"^\s*fork:\s*(\S+)\s*\n\s*version:\s*(\S+)", text, re.MULTILINE)
    if blk and blk.group(1) == REPO:
        return blk.group(2)
    # Commented-out variant of the requested repo
    blk = re.search(
        r"^\s*#\s*fork:\s*" + re.escape(REPO) + r"\s*\n\s*#\s*version:\s*(\S+)",
        text,
        re.MULTILINE,
    )
    if blk:
        return blk.group(1)
    sys.exit(f"Could not derive default `from` for {REPO} from {RECIPE}")


def resolve_default_to() -> str:
    if not _have_token():
        tag = _git_latest_tag()
        if tag:
            return tag
        sys.exit(f"Could not derive default `to` from git cache of {REPO}")
    # Latest STABLE release: the releases list is newest-first and may be led
    # by pre-releases (e.g. beellama `preview-vX.Y.Z`), which TAG_RE rejects.
    page = 1
    while True:
        data = api_get(
            f"repos/{REPO}/releases",
            query={"per_page": "30", "page": str(page)},
        )
        if not data:
            break
        for rel in data:
            tag = rel.get("tag_name", "")
            if _tag_key(tag):
                return tag
        if len(data) < 30:
            break
        page += 1
    sys.exit(f"No releases found on {REPO}")


# --------------------------------------------------------------------------- #
# Tag / date helpers
# --------------------------------------------------------------------------- #
def tag_date(tag: str) -> str | None:
    """Release `created_at` (YYYY-MM-DD) for a tag, else commit committer date."""
    if not _have_token():
        try:
            return _git_tag_date(tag)
        except SystemExit:
            return None
    try:
        rel = api_get(f"repos/{REPO}/releases/tags/{urllib.parse.quote(tag)}")
        created = rel.get("created_at") or rel.get("published_at")
        if created:
            return created[:10]
    except SystemExit:
        pass
    try:
        c = api_get(f"repos/{REPO}/commits/{urllib.parse.quote(tag)}")
        d = c["commit"]["committer"]["date"]
        return d[:10]
    except (SystemExit, KeyError, TypeError):
        return None


def tags_in_range(from_tag: str, to_tag: str) -> list[str]:
    """All release tags strictly between from and to (exclusive from, inclusive to)."""
    lo, hi = _tag_key(from_tag), _tag_key(to_tag)
    if lo is None or hi is None:
        return []
    if lo > hi:
        lo, hi = hi, lo
    if not _have_token():
        return _git_tags_in_range(lo, hi)
    out: list[str] = []
    page = 1
    while True:
        data = api_get(
            f"repos/{REPO}/tags", query={"per_page": "100", "page": str(page)}
        )
        if not data:
            break
        for t in data:
            name = t["name"]
            k = _tag_key(name)
            if k and lo < k <= hi:
                out.append(name)
        if len(data) < 100:
            break
        page += 1
    out.sort(key=lambda n: _tag_key(n) or ())
    return out


# --------------------------------------------------------------------------- #
# Compare commits
# --------------------------------------------------------------------------- #
def compare_commits(from_ref: str, to_ref: str) -> list[dict]:
    if not _have_token():
        return _git_compare_commits(from_ref, to_ref)
    data = api_get(f"repos/{REPO}/compare/{from_ref}...{to_ref}")
    status = data.get("status")
    if status == "behind":
        print(
            f"WARNING: {from_ref} is behind {to_ref} — refs may be reversed.",
            file=sys.stderr,
        )
    return data.get("commits", [])


# --------------------------------------------------------------------------- #
# PRs merged in range (GraphQL, filtered by mergeCommit SHA set)
# --------------------------------------------------------------------------- #
def _have_auth() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))


def fetch_prs(from_date: str, to_date: str, commit_shas: set[str]) -> list[dict]:
    if not _have_auth():
        print(
            "WARNING: no GitHub auth (no GITHUB_TOKEN/GH_TOKEN). "
            "Skipping PR section; showing commits only (from local git clone).",
            file=sys.stderr,
        )
        return []

    # Widen the date window by one day each side to dodge boundary drift,
    # then filter precisely by mergeCommit.oid membership.
    def shift(d: str, days: int) -> str:
        return (dt.date.fromisoformat(d) + dt.timedelta(days=days)).isoformat()

    q = (
        f"repo:{REPO} type:pr is:merged "
        f"merged:>={shift(from_date, -1)} merged:<={shift(to_date, +1)}"
    )
    prs: list[dict] = []
    cursor = None
    while True:
        after = f', after: "{cursor}"' if cursor else ""
        gql = f"""
        {{
          search(query: "{q}", type: ISSUE, first: 100{after}) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              ... on PullRequest {{
                number title url body mergedAt
                mergeCommit {{ oid }}
              }}
            }}
          }}
        }}"""
        data = graphql(gql)
        if data.get("errors"):
            sys.exit(f"GraphQL error: {data['errors']}")
        search = data["data"]["search"]
        for node in search["nodes"]:
            sha = (node.get("mergeCommit") or {}).get("oid")
            if sha and sha in commit_shas:
                prs.append(node)
        if not search["pageInfo"]["hasNextPage"]:
            break
        cursor = search["pageInfo"]["endCursor"]
    prs.sort(key=lambda p: p["mergedAt"])
    return prs


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def shorten_body(body: str | None, limit: int = 1200) -> str:
    if not body:
        return "(no description)"
    body = body.strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "\n…[truncated]"


def fmt_pr(pr: dict) -> str:
    body = shorten_body(pr.get("body"))
    return f"### #{pr['number']} — {pr['title']}\n{pr['url']}\n\n```\n{body}\n```"


def fmt_commit(c: dict) -> str:
    sha = c["sha"][:7]
    full = c["sha"]
    subj = c["commit"]["message"].split("\n", 1)[0]
    url = f"{REPO_URL}/commit/{full}"
    return f"- `{sha}` {subj} — {url}"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="llama.cpp changelog between two refs.")
    ap.add_argument("from_pos", nargs="?", default=None, help="starting ref")
    ap.add_argument("to_pos", nargs="?", default=None, help="ending ref")
    ap.add_argument("--from", dest="from_opt", default=None, help="starting ref")
    ap.add_argument("--to", dest="to_opt", default=None, help="ending ref")
    ap.add_argument(
        "--repo",
        default=None,
        help="GitHub owner/repo (default: the active fork in the source recipe)",
    )
    args = ap.parse_args()

    global REPO, REPO_URL
    REPO = args.repo or resolve_default_repo()
    REPO_URL = f"https://github.com/{REPO}"

    from_ref = args.from_opt or args.from_pos
    to_ref = args.to_opt or args.to_pos

    if from_ref is None:
        from_ref = resolve_default_from()
    if to_ref is None:
        to_ref = resolve_default_to()

    if from_ref == to_ref:
        print(f"Already at {from_ref}. Nothing to summarize.")
        return 0

    from_date = tag_date(from_ref) or "?"
    to_date = tag_date(to_ref) or "?"

    commits = compare_commits(from_ref, to_ref)
    commit_shas = {c["sha"] for c in commits}

    prs = []
    if from_date != "?" and to_date != "?":
        prs = fetch_prs(from_date, to_date, commit_shas)
    pr_shas = {(p.get("mergeCommit") or {}).get("oid") for p in prs}
    direct = [c for c in commits if c["sha"] not in pr_shas]

    tags = tags_in_range(from_ref, to_ref)

    # Header
    print(f"## {REPO} changelog: `{from_ref}` ({from_date}) → `{to_ref}` ({to_date})")
    print(f"- Tags in range: {len(tags)}")
    print(f"- Commits in compare: {len(commits)}")
    print(f"- PRs merged: {len(prs)}")
    print(f"- Direct commits (no PR): {len(direct)}")
    print()

    # Tags
    if tags:
        print("### Tags")
        for t in tags:
            d = tag_date(t) or "?"
            print(f"- `{t}` ({d}) — {REPO_URL}/releases/tag/{t}")
        print()

    # PRs
    if prs:
        print("### Pull requests")
        for p in prs:
            print(fmt_pr(p))
            print()
    elif commits:
        print("### Pull requests")
        print("(none found in range; relying on commit list below)")
        print()

    # Direct commits
    if direct:
        print("### Direct commits (no associated PR)")
        for c in direct:
            print(fmt_commit(c))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
