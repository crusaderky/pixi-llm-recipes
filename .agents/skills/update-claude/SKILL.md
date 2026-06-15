---
name: update-claude
description: Update the Claude Code conda recipe to the latest stable npm release. Use when asked to "update claude", "bump claude-code", or "update the claude recipe".
compatibility: Requires network access to registry.npmjs.org. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

## Steps

### 1. Read the current pinned version

Read `pixi-recipes/claude/recipe.yaml`. Extract:
- **current version** — `context.version` (e.g. `"2.1.153"`)
- **current sha256** — `source.sha256`

### 2. Fetch the latest stable version from npm

Query the npm registry for the `stable` dist-tag:

```
GET https://registry.npmjs.org/@anthropic-ai/claude-code
```

Extract `.dist-tags.stable` from the JSON response. This is the version to pin
(not `latest`, which may include pre-releases).

If the request fails, abort with an error — do **not** modify the recipe.

### 3. Compare versions

- If `stable_version` == `current_version`: print "Claude Code is already at the latest stable version (<version>). Nothing to do." and stop.
- Otherwise proceed.

### 4. Compute the sha256 of the new tarball

Download the tarball and compute its sha256:

```bash
curl -sL "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-<new_version>.tgz" | sha256sum
```

Extract the hex digest (first field of output).

### 5. Update recipe.yaml

Edit `pixi-recipes/claude/recipe.yaml` in-place:
- Replace the `context.version` value with the new version string (keep the surrounding quotes).
- Replace the `source.sha256` value with the new hex digest.

Preserve all other content, comments, and formatting exactly.

**Example edit**:

```yaml
# Old:
context:
  name: claude
  version: "2.1.153"
...
source:
  url: https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-${{ version }}.tgz
  sha256: 1870f640a84bda437a06808ecb8beadedfe3cf06ab0f541797b778cc90ba18e8

# New:
context:
  name: claude
  version: "2.1.177"
...
source:
  url: https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-${{ version }}.tgz
  sha256: <new_sha256>
```

### 6. Report the result

```
Updated Claude Code recipe:
  version: 2.1.153 → 2.1.177
  sha256:  1870f640... → <new_sha256>
```
