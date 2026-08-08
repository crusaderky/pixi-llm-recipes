---
name: update-herdr-file-viewer
description: Update the herdr-file-viewer conda recipe to the latest GitHub release — bump the pinned version and refresh the SHA-256 digests for the Linux (x86_64) and Windows (x86_64) prebuilt binaries. Use when asked to "update herdr-file-viewer", "bump herdr-file-viewer", or "update the herdr-file-viewer recipe".
compatibility: Requires network access to github.com / api.github.com. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

## Steps

1. Read `pixi-recipes/herdr-file-viewer/recipe.yaml` `context:` — `version` (plain, no `v`
   prefix), `sha256_linux_x86_64`, `sha256_win_64`.

2. Fetch the latest release tag (`v<version>`); abort on failure:

   ```bash
   curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 \
     https://api.github.com/repos/smarzban/herdr-file-viewer/releases/latest \
     | grep -oE '"tag_name": *"[^"]+"'
   ```

   Strip the leading `v`. If it equals the pinned version, report "already at the latest
   version" and stop.

3. Digest both prebuilts:

   ```bash
   REL=https://github.com/smarzban/herdr-file-viewer/releases/download/v${latest}
   curl -fsSL "$REL/herdr-file-viewer-x86_64-unknown-linux-musl"     | sha256sum | cut -d' ' -f1
   curl -fsSL "$REL/herdr-file-viewer-x86_64-pc-windows-msvc.exe"    | sha256sum | cut -d' ' -f1
   ```

   Upstream ships no Linux aarch64 prebuilt — that is why the dependency is gated to
   `linux-64` + `win-64` in `pixi.toml`.

4. Edit `recipe.yaml` in place: `version` (quoted, no `v`), `sha256_linux_x86_64`,
   `sha256_win_64`. Change nothing else — comments, blank lines and ordering stay put.

5. Report each of the three fields as `<old> → <new>`.
