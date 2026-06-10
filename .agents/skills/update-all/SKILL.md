---
name: update-all
description: Update everything — llama.cpp (source + binary recipes), pi-extensions npm packages, and run `pixi update` to refresh the lockfile. Triggered by "update yourself", "update everything", or "do a full update".
compatibility: Requires network access to api.github.com and registry.npmjs.org. Designed for the pixi-llm-recipes project.
allowed-tools: Bash, Read, Edit, WebFetch
---

## Trigger phrases

- "update yourself"
- "update everything"
- "do a full update"
- "update all recipes"

## Steps

### Phase 1 — Update llama.cpp

Run the **update-llama-cpp** skill:

1. Fetch the latest upstream release tag and commit SHA from GitHub.
2. Update all source build recipes (`cpu`, `cuda`, `vulkan`).
3. Update all binary build recipes (`cpu`, `vulkan`, `rocm`) with new versions and SHA256 hashes.
4. Run `bash scripts/diff-llama-cpp-variants.sh` and show the output.
5. Report old → new versions and release notes.

### Phase 2 — Update pi-extensions

Run the **update-pi-extensions** skill:

1. Extract pinned npm packages from `pixi-recipes/pi-extensions/build.sh`.
2. Fetch latest versions from the npm registry.
3. Update any out-of-date versions in `build.sh`.
4. Report the summary of what was updated.

### Phase 3 — Refresh pixi lockfile

Run `pixi update` from the project root:

```bash
pixi update
```

This refreshes the `pixi.lock` file with the latest resolved versions of all dependencies.

### Phase 4 — Final summary

Present a consolidated report:

```
## Update Complete

### llama.cpp
- Source: <old> → <new>
- Binary (cpu): <old> → <new>
- Binary (vulkan): <old> → <new>
- Binary (rocm): <old> → <new>

### pi-extensions
- Updated: <list of packages with version bumps>
- Up to date: <list of packages>

### pixi.lock
- Refreshed via `pixi update`.

### Diff of source variants
<output from diff-llama-cpp-variants.sh>
```
