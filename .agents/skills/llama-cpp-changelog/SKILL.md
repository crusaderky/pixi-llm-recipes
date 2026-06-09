---
name: llama-cpp-changelog
description: Summarize changes between two versions of llama.cpp. Initial version defaults to the one pinned in pixi-recipes/llama-cpp-source/cpu/recipe.yaml; final version defaults to the latest upstream release. Both can be overridden with arbitrary git refs via `from=<ref>` and `to=<ref>` args.
compatibility: Requires network access to api.github.com. Designed for the pixi-llm-recipes project.
allowed-tools: WebFetch Read
---

## Arguments (space-separated, all optional)

- `from=<ref>` — starting git ref (tag such as `b9518`, or a commit SHA). Defaults to `context.version` in `pixi-recipes/llama-cpp-source/cpu/recipe.yaml`.
- `to=<ref>` — ending git ref. Defaults to the latest upstream release tag.

## Steps

### 1. Resolve `from` ref

- If `from` was **not** supplied, read `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` and extract `context.version` (e.g. `b9518`). Use that value as `FROM`.
- Otherwise use the supplied value as-is as `FROM`.

### 2. Resolve `to` ref

- If `to` was **not** supplied:
  - GET `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest`
  - Extract `.tag_name` → `TO`.
- Otherwise use the supplied value as-is as `TO`.

### 3. Early-exit check

If `FROM` == `TO`, print "Already at the latest version (`FROM`). Nothing to summarize." and stop.

### 4. Collect per-release notes (when both refs are `bNNNN`-style tags)

When both `FROM` and `TO` match the pattern `b<digits>` (e.g. `b9518`, `b9520`):

- Parse the numeric suffix: `from_num` and `to_num`.
- If `from_num` > `to_num`: warn "FROM appears to be newer than TO — refs may be reversed." and swap them.
- Paginate through `https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100` (add `&page=N` for subsequent pages).
  - GitHub returns releases newest-first.
  - For each release, parse its `tag_name` numeric suffix.
  - **Collect** releases where `from_num < tag_num <= to_num`.
  - **Stop paginating** once you encounter a release with `tag_num <= from_num` (you've gone past the range).
- Store each collected release's `.tag_name`, `.published_at` (date only), and `.body` (markdown release notes).

### 5. Collect commit-level diff (for arbitrary refs, or to supplement step 4)

GET `https://api.github.com/repos/ggml-org/llama.cpp/compare/{FROM}...{TO}`

- If `.status` is `"behind"` or `"diverged"`, warn the user that `FROM` appears newer than `TO`.
- Extract:
  - `.commits[].commit.message` — first line of each commit message (up to 200 commits; note if truncated).
  - `.files[].filename` — list of changed files, for context on which subsystems were touched.
- Use this data to cross-check and supplement the release notes in step 4, or as the primary source when release notes are unavailable (arbitrary refs, or tags with no associated GitHub release).

### 6. Synthesize and present the summary

Produce a structured markdown summary with the following sections (omit any section that has no relevant content):

#### Header
```
## llama.cpp changelog: {FROM} (Released: {FROM_DATE}) → {TO} (Released: {TO_DATE})
{N} release(s) spanning {date_range} — {total_commits} commits
```

#### Per-release breakdown (only for `bNNNN` ranges)

For each release in chronological order (oldest → newest):

```
### {tag_name} — {date}
{release body, trimmed to key bullet points — skip boilerplate, keep substance}
```

#### Cross-cutting themes

Group notable changes across all releases into theme buckets (include only non-empty buckets):

- **New models / backends** — new model architectures, new hardware backends, new quantization types
- **Performance improvements** — throughput, latency, memory usage wins; include rough numbers if cited
- **API / interface changes** — renamed flags, new/removed CLI args, server API changes, breaking changes (flag prominently)
- **Build system** — CMake changes, new dependencies, platform support changes
- **Bug fixes** — notable correctness or stability fixes (not exhaustive)
- **New tools / examples** — new executables, scripts, example programs
- **Documentation** — significant doc or README changes

#### Upgrade recommendation

End with a concise one-line verdict:
- "Safe to upgrade — no breaking changes detected."
- "Review before upgrading — `<flag/API>` was renamed/removed: <details>."
- "Significant changes — test your workload before upgrading."
