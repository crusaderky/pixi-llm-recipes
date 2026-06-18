---
name: llama-cpp-changelog
description: Summarize changes between two versions of llama.cpp. Initial version defaults to the one pinned in pixi-recipes/llama-cpp-source/cpu/recipe.yaml (may be commented out — use that value anyway); final version defaults to the latest upstream release. Both can be overridden with arbitrary git refs via `from=<ref>` and `to=<ref>` args.
compatibility: Uses GitHub API — no git clone needed. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read
---

## Arguments (space-separated, all optional)

- `from=<ref>` — starting git ref (tag such as `b9518`, or a commit SHA). Defaults based on active fork:
  - **Turboquant fork** (uncommented `fork: TheTom/llama-cpp-turboquant`): read the `# Last sync with main at bNNNN` comment in `context` block and use that `bNNNN` value. The fork's own version string (e.g. `feature-turboquant-kv-cache-b9905-4595fff`) is NOT an upstream tag — never use it as FROM.
  - **Main branch** (uncommented `fork: ggml-org/llama.cpp`): use `context.version` directly.
  - **Other fork**: look for `# Last sync with main at bNNNN` comment first; fall back to commented-out `# version: bNNNN` under `# Main branch`.
  - **No sync comment**: fall back to `# version: bNNNN` under `# Main branch` (commented out, ignore `#` when extracting).
- `to=<ref>` — ending git ref. Defaults to the latest upstream release tag.

## Steps

### 1. Resolve `from` ref

- If `from` was **not** supplied, read `pixi-recipes/llama-cpp-source/cpu/recipe.yaml`.
  - First, look for a `# Last sync with main at bNNNN` comment in the `context` block. If found, extract `bNNNN` and use that as `FROM`.
  - If no sync comment, look for uncommented `context.version`. If present (e.g. `version: b9698`), use that value as `FROM`.
  - If neither, look for `# version: bNNNN` under `# Main branch` (commented out). Ignore the `#` prefix and use that value as `FROM`.
  - **IMPORTANT**: When the active fork is `TheTom/llama-cpp-turboquant`, its version string contains a `bNNNN` (e.g. `feature-turboquant-kv-cache-b9905-4595fff`). This `b9905` is the turboquant fork's own internal tag, NOT an upstream tag — never use it as FROM.
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

#### Turboquant fork note (if applicable)

When the active recipe uses the turboquant fork, add a line clarifying the context:
- "Turboquant fork currently at its own `bNNNN`, last synced with main at `bXXXX`. This changelog covers upstream main changes `bXXXX`→`bYYYY` — check turboquant branch for whether those changes are already cherry-picked."
- Do NOT say "turboquant already past this" — the fork's version number is its own internal tag and has no relation to upstream release tags.
