---
name: update-herdr-file-viewer
description: Update the herdr-file-viewer conda recipe to the latest GitHub release — bump the pinned version and refresh the SHA-256 digests for the Linux (x86_64) and Windows (x86_64) prebuilt binaries. Use when asked to "update herdr-file-viewer", "bump herdr-file-viewer", or "update the herdr-file-viewer recipe".
compatibility: Requires network access to github.com / api.github.com. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

## Steps

### 1. Read the current pinned values

Read `pixi-recipes/herdr-file-viewer/recipe.yaml`. Extract from the `context:` block:

- **current version** — `context.version` (e.g. `"1.12.0"` — plain version, NO `v` prefix)
- **current sha256_linux_x86_64** — `context.sha256_linux_x86_64`
- **current sha256_win_64** — `context.sha256_win_64`

### 2. Fetch the latest release tag from GitHub

Query the latest GitHub release of `smarzban/herdr-file-viewer`:

```bash
curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 \
  "https://api.github.com/repos/smarzban/herdr-file-viewer/releases/latest" \
  | grep -oE '"tag_name": *"[^"]+"'
```

The tag is `v<version>` (e.g. `v1.12.0`). Strip the leading `v` to get `latest_version`
(e.g. `1.12.0`).

If the request fails, abort with an error.

### 3. Check if an update is needed

If `latest_version` == current `context.version` (e.g. `1.12.0`): print
"herdr-file-viewer is already at the latest version. Nothing to do." and stop.

Otherwise proceed.

### 4. Download the prebuilt binaries and compute SHA-256

The release assets are named (from tag `v{latest_version}`):

- `herdr-file-viewer-x86_64-unknown-linux-musl` (Linux x86_64)
- `herdr-file-viewer-x86_64-pc-windows-msvc.exe` (Windows x86_64)

Download each and compute its SHA-256:

```bash
REL="https://github.com/smarzban/herdr-file-viewer/releases/download/v${latest_version}"

curl -fsSL "${REL}/herdr-file-viewer-x86_64-unknown-linux-musl" \
  | sha256sum | cut -d' ' -f1

curl -fsSL "${REL}/herdr-file-viewer-x86_64-pc-windows-msvc.exe" \
  | sha256sum | cut -d' ' -f1
```

Extract the two hex digests.

### 5. Update recipe.yaml

Edit `pixi-recipes/herdr-file-viewer/recipe.yaml` in-place:

- Replace `context.version` with `"{latest_version}"` (quoted, NO `v` prefix)
- Replace `context.sha256_linux_x86_64` with the new Linux x86_64 digest
- Replace `context.sha256_win_64` with the new Windows x86_64 digest

**Example edit**:

```yaml
# Old:
  version: "1.12.0"
  sha256_linux_x86_64: ff041cee0e5330082d38521ab8f0cc7c97602aa4add9cce8c6e896d3c83881f5
  sha256_win_64: 735bb021b2d827e98c6783b04c409223301b99de950fde2fb415e6314476c5d2

# New:
  version: "1.13.0"
  sha256_linux_x86_64: <new_linux_x86_64_digest>
  sha256_win_64: <new_windows_x86_64_digest>
```

Preserve all comments, blank lines, indentation, and ordering exactly as they were. Only
change the version string and the two SHA-256 digests.

### 6. Report the result

```
Updated herdr-file-viewer recipe:
  version:            1.12.0 → 1.13.0
  sha256_linux_x86_64: ff041cee... → <new>
  sha256_win_64:       735bb021... → <new>
```
