---
name: update-claude
description: Update the Claude Code conda recipe to the latest npm release. Use when asked to "update claude", "bump claude-code", or "update the claude recipe".
compatibility: Requires network access to registry.npmjs.org. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

## Steps

1. Read `pixi-recipes/claude/recipe.yaml`: note `context.version` and `source.sha256`.

2. Fetch the `latest` dist-tag:

   ```bash
   curl -fsSL https://registry.npmjs.org/@anthropic-ai/claude-code | grep -oE '"latest":"[^"]+"'
   ```

   Abort without touching the recipe if the request fails. If it equals the pinned
   version, report "already at the latest version (<version>)" and stop.

3. Compute the tarball digest:

   ```bash
   curl -sL "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-<new>.tgz" | sha256sum
   ```

4. Edit `recipe.yaml` in place: replace `context.version` (keep the quotes) and
   `source.sha256`. Preserve every comment, blank line and indentation.

5. Report `version: <old> → <new>` and `sha256: <old prefix>… → <new>`.
