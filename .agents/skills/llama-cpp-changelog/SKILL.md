---
name: llama-cpp-changelog
description: Summarize changes between two versions of llama.cpp (any fork). Repo defaults to the active fork pinned in pixi-recipes/llama-cpp-source/recipe.yaml (override with `--repo owner/name`); initial version defaults to that fork's pinned version; final version defaults to the fork's latest stable release. Refs can be overridden with arbitrary git refs. Handles both upstream `bNNNN` and beellama `vX.Y.Z` tags.
compatibility: Tags/commits print without GitHub auth; the PR section is skipped without a GitHub token (it needs GraphQL).
allowed-tools: Bash Read
---

## Arguments

All optional, given as `from=<ref>`, `to=<ref>`, `repo=<owner/name>`.

- **`from`** — starting ref (a tag such as `b9518` / `v0.4.0`, or a SHA). Defaults, in
  order, to the source recipe's `# Last sync with main at <tag>` comment, the active
  `version:` for the selected repo, then its commented-out `# version:`.
- **`to`** — ending ref. Defaults to the repo's latest **stable** release (`preview-*` skipped).
- **`repo`** — defaults to the active (uncommented) `fork:` in
  `pixi-recipes/llama-cpp-source/recipe.yaml`. Use `ggml-org/llama.cpp` for mainline.

## 1. Run the script

```bash
pixi r llama-cpp-changelog [<from>] [<to>]        # or --from <ref> --to <ref>
pixi r llama-cpp-changelog --repo <owner/name> …
```

It is slow (it clones the upstream repo on first use), so **dump stdout to a temp file
once** and re-read that file instead of re-running.

Output: a header (refs, dates, counts), the tags in range with dates and URLs, every PR
merged in range (`#NNNN — title`, URL, body excerpt up to 1200 chars, oldest first,
filtered by merge-commit SHA), and the direct commits with no PR.

If `from == to` it prints "Already at <ref>. Nothing to summarize." and exits.

Without GitHub auth the PR section is skipped with a warning; tags and commit subjects
still print. **Do not** fall back to `gh` or raw REST calls — the repo is large and
unauthenticated REST is rate-limited, and the git fallback already gives the commit list.
Set `GITHUB_TOKEN` and re-run if PR bodies are genuinely needed.

## 2. Summarize

Work only from the script's output; never fetch PRs yourself.

```
## llama.cpp changelog: {FROM} ({FROM_DATE}) → {TO} ({TO_DATE})
{N} release(s) — {commits} commits — {P} PRs
```

Then group notable changes into whichever of these buckets are non-empty, citing PR
numbers with URLs: new models/backends/quant types · performance (quote figures from PR
bodies) · API and CLI changes (flag renames/removals — flag these prominently) · build
system · notable bug fixes · new tools or examples · documentation.

Close with a one-line verdict: "Safe to upgrade", "Review before upgrading — `<flag>`
was renamed/removed: …", or "Significant changes — test your workload first."
