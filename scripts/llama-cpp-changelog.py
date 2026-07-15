#!/usr/bin/env python3
"""Dump a deterministic llama.cpp changelog between two git refs.

Resolves defaults from pixi-recipes/llama-cpp-source/recipe.yaml:
  - from: the `# Last sync with main at bNNNN` comment, else the active
    main-branch `version`, else the commented-out `# version: bNNNN`.
  - to:   the latest upstream release tag.

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
import subprocess
import sys
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "ggml-org/llama.cpp"
REPO_URL = f"https://github.com/{REPO}"
RECIPE = (
    Path(__file__).resolve().parent.parent
    / "pixi-recipes"
    / "llama-cpp-source"
    / "recipe.yaml"
)

B_TAG = re.compile(r"^b(\d+)$")

# Cache for the commits-only git clone used when GitHub auth is unavailable.
_GIT_CACHE = Path(
    os.environ.get(
        "LLAMA_CPP_CHANGELOG_CACHE",
        str(Path.home() / ".cache" / "llama-cpp-changelog" / "llama.cpp.git"),
    )
)


# --------------------------------------------------------------------------- #
# GitHub API access
# --------------------------------------------------------------------------- #
def _urllib_get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310 — trusted host
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:  # noqa: PERF203
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
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        return json.loads(resp.read())


# --------------------------------------------------------------------------- #
# Git fallback (no GitHub auth) — commits-only cached clone
# --------------------------------------------------------------------------- #
def _have_token() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))


def _git_cache_ready() -> bool:
    return (_GIT_CACHE / "config").exists()


def _ensure_git_cache() -> None:
    """Ensure a commits-only clone of the repo exists and is up to date."""
    if not _git_cache_ready():
        _GIT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _GIT_CACHE.with_suffix(".tmp")
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
        tmp.replace(_GIT_CACHE)
    # Refresh refs (commits-only fetch is cheap).
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(_GIT_CACHE),
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
        ["git", "--git-dir", str(_GIT_CACHE), *args],
        capture_output=True,
        text=True,
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


def _git_tags_b_in_range(lo: int, hi: int) -> list[str]:
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/tags"])
    names: list[str] = []
    for line in out.splitlines():
        m = B_TAG.match(line.strip())
        if m and lo < int(m.group(1)) <= hi:
            names.append(line.strip())
    names.sort(key=lambda n: int(B_TAG.match(n).group(1)))
    return names


def _git_latest_b_tag() -> str | None:
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/tags"])
    best, best_n = None, -1
    for line in out.splitlines():
        m = B_TAG.match(line.strip())
        if m and int(m.group(1)) > best_n:
            best, best_n = line.strip(), int(m.group(1))
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
def resolve_default_from() -> str:
    text = RECIPE.read_text()
    m = re.search(r"#\s*Last sync with main at (b\d+)", text)
    if m:
        return m.group(1)
    # active main-branch version (fork: ggml-org/llama.cpp is uncommented)
    blk = re.search(
        r"# Main branch\s*\n\s*fork:\s*ggml-org/llama\.cpp\s*\n\s*version:\s*(\S+)",
        text,
    )
    if blk:
        return blk.group(1)
    # commented-out main-branch version
    blk = re.search(
        r"# Main branch\s*\n#\s*fork:\s*ggml-org/llama\.cpp\s*\n#\s*version:\s*(\S+)",
        text,
    )
    if blk:
        return blk.group(1)
    sys.exit(f"Could not derive default `from` from {RECIPE}")


def resolve_default_to() -> str:
    if not _have_token():
        tag = _git_latest_b_tag()
        if tag:
            return tag
        sys.exit(f"Could not derive default `to` from git cache of {REPO}")
    data = api_get(f"repos/{REPO}/releases", query={"per_page": "1"})
    if not data:
        sys.exit(f"No releases found on {REPO}")
    return data[0]["tag_name"]


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
    """All bNNNN tags strictly between from and to (exclusive of from, inclusive of to)."""
    fm, tm = B_TAG.match(from_tag), B_TAG.match(to_tag)
    if not (fm and tm):
        return []
    lo, hi = int(fm.group(1)), int(tm.group(1))
    if lo > hi:
        lo, hi = hi, lo
    if not _have_token():
        return _git_tags_b_in_range(lo, hi)
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
            m = B_TAG.match(name)
            if m and lo < int(m.group(1)) <= hi:
                out.append(name)
        if len(data) < 100:
            break
        page += 1
    out.sort(key=lambda n: int(B_TAG.match(n).group(1)))
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
        gql = """
        {
          search(query: "%s", type: ISSUE, first: 100%s) {
            pageInfo { hasNextPage endCursor }
            nodes {
              ... on PullRequest {
                number title url body mergedAt
                mergeCommit { oid }
              }
            }
          }
        }""" % (q, after)
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
    args = ap.parse_args()

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
    print(
        f"## llama.cpp changelog: `{from_ref}` ({from_date}) → `{to_ref}` ({to_date})"
    )
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
