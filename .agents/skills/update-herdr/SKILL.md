---
name: update-herdr
description: Update the herdr conda recipe to the latest stable (Linux) and preview (Windows) releases. Use when asked to "update herdr", "bump herdr", or "update the herdr recipe".
compatibility: Requires network access to herdr.dev. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

## Steps

### 1. Read the current pinned versions

Read `pixi-recipes/herdr/recipe.yaml`. Extract:

- **current version_stable** — `context.version_stable` (e.g. `"0.7.1"` — plain version, NO `v` prefix)
- **current version_preview** — `context.version_preview` (e.g. `preview-2026-06-22-24c7377de01c`)

### 2. Fetch latest stable manifest (Linux)

```bash
curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 "https://herdr.dev/latest.json"
```

Parse the JSON. Extract:

- **`version`** → e.g. `"0.7.1"`
- **`assets.linux-x86_64`** → download URL for x86_64 binary
- **`assets.linux-aarch64`** → download URL for aarch64 binary

If the request fails, abort with an error.

### 3. Fetch latest preview manifest (Windows)

```bash
curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 "https://herdr.dev/preview.json"
```

Parse the JSON. Extract:

- **`build_id`** → e.g. `"2026-06-22-24c7377de01c"`. Construct the preview tag: `preview-{build_id}`.
- **`assets.windows-x86_64.url`** → download URL
- **`assets.windows-x86_64.sha256`** → expected sha256

If the request fails, abort with an error.

### 4. Check if update needed

- `stable_changed`: `stable.version` != current `context.version_stable` (after stripping quotes)
- `preview_changed`: `preview-{build_id}` != current `context.version_preview`

If neither changed: print "herdr is already at the latest versions. Nothing to do." and stop.

### 5. Compute sha256 for new stable binaries (if stable changed)

Download each stable binary and compute its sha256:

```bash
curl -fsSL "https://github.com/herdrdev/herdr/releases/download/v{version}/herdr-linux-x86_64" | sha256sum
curl -fsSL "https://github.com/herdrdev/herdr/releases/download/v{version}/herdr-linux-aarch64" | sha256sum
```

Extract the hex digests (first field of output).

### 6. Update recipe.yaml

Edit `pixi-recipes/herdr/recipe.yaml` in-place:

- If `stable_changed`:
  - Replace `context.version_stable` with `"{stable.version}"` (quoted, NO `v` prefix)
  - Replace `context.sha256_stable_x86_64` with the new x86_64 digest
  - Replace `context.sha256_stable_aarch64` with the new aarch64 digest
- If `preview_changed`:
  - Replace `context.version_preview` with `preview-{build_id}`
  - Replace `context.sha256_preview_win_64` with the sha256 from the preview manifest

**Example edit**:

```yaml
# Old:
  version_stable: "0.7.1"
  sha256_stable_x86_64: b965acaf...
  sha256_stable_aarch64: 3d757ac3...
  version_preview: preview-2026-06-22-24c7377de01c
  sha256_preview_win_64: 9eb8b028...

# New:
  version_stable: "0.7.2"
  sha256_stable_x86_64: <new_x86_64_digest>
  sha256_stable_aarch64: <new_aarch64_digest>
  version_preview: preview-2026-06-25-deadbeef0001
  sha256_preview_win_64: <new_preview_digest>
```

### 7. Report the result

```
Updated herdr recipe:
  version_stable:   0.7.1 → 0.7.2
  sha256 x86_64:    b965acaf... → <new>
  sha256 aarch64:   3d757ac3... → <new>
  version_preview:  preview-2026-06-22-24c7377de01c → preview-2026-06-25-deadbeef0001
  sha256_preview_win_64:   9eb8b028... → <new>
```
