---
name: llama-cpp-changelog
description: Summarize changes between two versions of llama.cpp. Initial version defaults to the one pinned in pixi-recipes/llama-cpp-source/recipe.yaml; final version defaults to the latest upstream release. Both can be overridden with arbitrary git refs.
compatibility: Tags/commits print without GitHub auth; PRs skipped without github token (requires GraphQL).
allowed-tools: Bash Read
---

## Arguments (space-separated, all optional)

- `from=<ref>` — starting git ref (tag such as `b9518`, or a commit SHA). Default: resolved from `pixi-recipes/llama-cpp-source/recipe.yaml`:
  - `# Last sync with main at bNNNN` comment if present (use that `bNNNN`).
  - Else the active `version:` under `# Main branch` (`fork: ggml-org/llama.cpp`).
  - Else the commented-out `# version: bNNNN` under `# Main branch`.
  - **Turboquant fork note**: the active fork's version string (e.g. `feature-turboquant-kv-cache-b9905-4595fff`) is NOT an upstream tag — never use it as `from`. Always use the `Last sync with main at bNNNN` value when the active fork is turboquant.
- `to=<ref>` — ending git ref. Default: latest upstream release tag.

Parse the args: each token is either `from=VALUE` or `to=VALUE`. Pass them to the script as positional or named args. **Tip: call the script once, dump stdout to a temp file, and parse that file for subsequent uses — the script takes a long time to run (it clones the full upstream repo).

## Steps

### 1. Run the changelog script

```bash
pixi r llama-cpp-changelog [<from>] [<to>]
# or
pixi r llama-cpp-changelog --from <from> --to <to>
```

The script writes a deterministic markdown report to stdout with these sections:

1. **Header** — `from → to`, release dates, counts (tags, commits, PRs, direct commits).
2. **Tags** — every `bNNNN` tag strictly between `from` and `to`, with release date and release URL.
3. **Pull requests** — every PR merged in the range (filtered by merge-commit SHA): `#NNNN — title`, PR URL, and a body excerpt (up to 1200 chars) in a fenced block. Sorted oldest → newest.
4. **Direct commits** — commits in the compare with no associated PR: short hash, subject, commit URL.

If `from == to`, the script prints "Already at <ref>. Nothing to summarize." and exits.

If no GitHub auth is available, the script skips the PR section with a warning and still prints tags + commits (subjects come from git, so no PR titles/bodies — only the commit subjects, which usually contain the `(#NNNN)` reference). Do NOT reach for `gh` or raw `curl` to the GitHub REST API: the repo is large, unauthenticated REST is rate-limited, and the script's git fallback already gives you the commit list. If PR bodies are truly required, set `GITHUB_TOKEN` in the env and re-run.

### 2. Synthesize the summary

Use the script's output as your primary source. Produce a structured markdown summary for the user, with these sections (omit empty ones):

#### Header

```
## llama.cpp changelog: {FROM} (Released: {FROM_DATE}) → {TO} (Released: {TO_DATE})
{N} release(s) — {total_commits} commits — {P} PRs
```

#### Cross-cutting themes

Group notable PRs and commits into theme buckets (include only non-empty ones):

- **New models / backends** — new model architectures, hardware backends, quantization types
- **Performance improvements** — throughput, latency, memory wins; cite numbers if in PR body
- **API / interface changes** — renamed flags, new/removed CLI args, server API changes, breaking changes (flag prominently)
- **Build system** — CMake changes, new deps, platform support
- **Bug fixes** — notable correctness/stability fixes (not exhaustive)
- **New tools / examples** — new executables, scripts, examples
- **Documentation** — significant doc changes

Cite PR numbers (`#NNNN`) with their URL when grouping. Read the PR body excerpts from the script output; do NOT fetch PRs yourself — the script already collected them.

#### Upgrade recommendation

End with a one-line verdict:

- "Safe to upgrade — no breaking changes detected."
- "Review before upgrading — `<flag/API>` was renamed/removed: <details>."
- "Significant changes — test your workload before upgrading."

#### Turboquant fork note (if applicable)

When the active recipe uses the turboquant fork, add:

- "Turboquant fork currently at its own `bNNNN`, last synced with main at `bXXXX`. This changelog covers upstream main changes `bXXXX`→`bYYYY` — check the turboquant branch for whether those changes are already cherry-picked."
- Do NOT say "Turboquant already past this" — the fork's version number is its own internal tag, unrelated to upstream release tags.
