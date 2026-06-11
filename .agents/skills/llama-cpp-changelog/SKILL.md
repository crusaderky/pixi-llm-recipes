---
name: llama-cpp-changelog
description: Summarize changes between two versions of llama.cpp. Initial version defaults to the one pinned in pixi-recipes/llama-cpp-source/cpu/recipe.yaml; final version defaults to the latest upstream release. Both can be overridden with arbitrary git refs via `from=<ref>` and `to=<ref>` args.
compatibility: Uses GitHub API — no git clone needed. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read
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
  - Run: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=1" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": "\(.*\)".*/\1/'` → `TO`.
  - Run: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=1" | grep '"created_at"' | head -1 | sed 's/.*"created_at": "\([0-9-]*\).*/\1/'` → `TO_DATE`.
- Otherwise use the supplied value as-is as `TO`.
  - Run: `git ls-remote --tags https://github.com/ggml-org/llama.cpp.git | grep -E "refs/tags/${TO}$"` → verify tag exists.
  - For date: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100" | python3 -c "import json,sys,re; tags=json.load(sys.stdin); [print(r['created_at'][:10]) for r in tags if r['tag_name']=='${TO}']"` → `TO_DATE`.

### 3. Early-exit check

If `FROM` == `TO`, print "Already at the latest version (`FROM`). Nothing to summarize." and stop.

### 4. Collect per-release commit titles (when both refs are `bNNNN`-style tags)

When both `FROM` and `TO` match the pattern `b<digits>` (e.g. `b9518`, `b9520`):

- Parse the numeric suffix: `from_num` and `to_num`.
- If `from_num` > `to_num`: warn "FROM appears to be newer than TO — refs may be reversed." and swap them.
- Run: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=100" | python3 -c "import json,sys; tags=json.load(sys.stdin); [print(r['created_at'][:10],r['tag_name']) for r in tags if r['tag_name']=='${FROM}' or r['tag_name']=='${TO}' or int(r['tag_name'][1:]) in range(${from_num}+1,${to_num}+1)]"` → extract dates.
- Set `prev = FROM`.
- For each tag `bN` where `from_num < N <= to_num`:
  - Run: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/compare/${prev}...bN" | python3 -c "import json,sys; data=json.load(sys.stdin); [print(c['commit']['message'].split(chr(10))[0]) for c in data.get('commits',[])]"` → collect commit subject lines.
  - Set `prev = bN`.

### 5. Collect commit-level diff (for arbitrary refs, or to supplement step 4)

Run: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/compare/${FROM}...${TO}" | python3 -c "import json,sys; data=json.load(sys.stdin); commits=data.get('commits',[]); [print(c['sha'][:7], c['commit']['message'].split(chr(10))[0]) for c in commits]"`.

- If `data.get('status')` is `'behind'`, warn that `FROM` appears newer than `TO`.
- Extract:
  - Each commit's short hash and subject line.
  - For file context: `curl -s "https://api.github.com/repos/ggml-org/llama.cpp/compare/${FROM}...${TO}" | python3 -c "import json,sys; data=json.load(sys.stdin); [print(f['filename']) for f in data.get('files',[])]"` → see which subsystems were touched.
- Use this as the primary source when release notes are unavailable (arbitrary refs, or tags with no associated GitHub release).

### 6. Synthesize and present the summary

Produce a structured markdown summary with the following sections (omit any section that has no relevant content):

#### Header
```
## llama.cpp changelog: {FROM} (Released: {FROM_DATE}) → {TO} (Released: {TO_DATE})
{N} release(s) — {total_commits} commits
```

#### Per-release breakdown (only for `bNNNN` ranges)

For each release in chronological order (oldest → newest):

```
### {tag_name}
- List of commit subject lines (trimmed, grouped by theme)
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
