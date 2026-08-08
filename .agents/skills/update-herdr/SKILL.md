---
name: update-herdr
description: Update the herdr conda recipe to the latest stable (Linux) and preview (Windows) releases. Use when asked to "update herdr", "bump herdr", or "update the herdr recipe".
compatibility: Requires network access to herdr.dev and github.com. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

The recipe pins two independent release channels: **stable** for Linux and
**preview** for Windows (Windows builds are preview-only). Either may move alone.

## Steps

1. Read `pixi-recipes/herdr/recipe.yaml` `context:` — `version_stable` (plain, no `v`
   prefix), `sha256_stable_x86_64`, `sha256_stable_aarch64`, `version_preview` (the full
   `preview-<build_id>` tag), `sha256_preview_win_64`.

2. Fetch both manifests; abort on failure:

   ```bash
   curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 https://herdr.dev/latest.json
   curl -fsSL --retry 3 --connect-timeout 10 --max-time 20 https://herdr.dev/preview.json
   ```

   From `latest.json` take `version`. From `preview.json` take `build_id` (the tag is
   `preview-{build_id}`) and `assets.windows-x86_64.sha256`.

3. If neither `version` nor `preview-{build_id}` differs from the pins, report "already
   at the latest versions" and stop.

4. If stable moved, digest both Linux binaries:

   ```bash
   REL=https://github.com/herdrdev/herdr/releases/download/v{version}
   curl -fsSL "$REL/herdr-linux-x86_64"  | sha256sum
   curl -fsSL "$REL/herdr-linux-aarch64" | sha256sum
   ```

5. Edit `recipe.yaml` in place — stable channel: `version_stable` (quoted, no `v`) plus
   the two Linux digests; preview channel: `version_preview` (`preview-{build_id}`) plus
   `sha256_preview_win_64` taken straight from the manifest. Preserve comments,
   indentation and ordering.

6. Report each field as `<old> → <new>`, or "unchanged" per channel.
