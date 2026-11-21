---
name: llama-cpp-changelog
description: Summarize changes between two versions of llama.cpp (any fork). Repo defaults to the active fork pinned in pixi-recipes/llama-cpp-source/recipe.yaml (override with `--repo owner/name`); initial version defaults to that fork's pinned version; final version defaults to its latest stable release. Refs can be overridden with arbitrary git refs. Handles mainline `bNNNN` and beellama `vX.Y.Z` tags.
compatibility: Tags/commits print without GitHub auth from a cached commits-only git clone (`LLAMA_CPP_CHANGELOG_CACHE` overrides its path); the PR section needs GITHUB_TOKEN/GH_TOKEN for GraphQL. A `--no-git` session blocks the network clone fallback — use the default mode.
allowed-tools: Bash Read
---

## Arguments

All optional, given as `from=<ref>`, `to=<ref>`, `repo=<owner/name>`.

- **`from`** — starting ref (a tag such as `b12613` / `v0.5.0`, or a SHA). Defaults, in
  order, to the source recipe's `# Last sync with main at <tag>` comment (when present),
  the active `version:` for the selected repo, then its commented-out `# version:`.
- **`to`** — ending ref. Defaults to the repo's latest stable `bNNNN` / `vX.Y.Z` release
  (`preview-*` and other pre-release tags are skipped).
- **`repo`** — defaults to the active (uncommented) `fork:` in
  `pixi-recipes/llama-cpp-source/recipe.yaml`. Use `ggml-org/llama.cpp` for mainline.

## 1. Run the script

```bash
pixi r llama-cpp-changelog [<from>] [<to>]        # or --from <ref> --to <ref>
pixi r llama-cpp-changelog --repo <owner/name> …
```

It is slow on first use (commits-only clone cached per fork), so **dump stdout to a temp
file once** and re-read that file instead of re-running.

Output: a header naming the repo and the refs/dates/counts, tags in range with dates and
URLs, PRs merged in range (`#NNNN — title`, URL, body excerpt, filtered by merge-commit
SHA), and direct commits with no PR.

If `from == to` it prints "Already at <ref>. Nothing to summarize." and exits.

Without `GITHUB_TOKEN`/`GH_TOKEN` the PR section is skipped; tags and commit subjects
still print from the git cache. **Do not** hand-fetch PRs with `gh` or raw REST — the
script already does that when authenticated. Set a token and re-run if PR bodies matter.

## 2. Summarize

Work only from the script's output; never fetch PRs yourself.

```
## {owner/repo} changelog: `{FROM}` ({FROM_DATE}) → `{TO}` ({TO_DATE})
{N} release(s) — {commits} commits — {P} PRs
```

Then group notable changes into whichever of these buckets are non-empty, citing PR
numbers with URLs: new models/backends/quant types · performance (quote figures from PR
bodies) · API and CLI changes (flag renames/removals — flag these prominently) · build
system · notable bug fixes · new tools or examples · documentation.

Close with a one-line verdict: "Safe to upgrade", "Review before upgrading — `<flag>`
was renamed/removed: …", or "Significant changes — test your workload first."
