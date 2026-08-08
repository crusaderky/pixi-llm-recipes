---
name: update-all
description: Update everything — llama.cpp (source + binary recipes), pi-extensions npm packages, Claude Code conda recipe, herdr and herdr-file-viewer recipes, then run `pixi update` to refresh the lockfile. Triggered by "update yourself", "update everything", or "do a full update".
compatibility: Requires network access to api.github.com, registry.npmjs.org and herdr.dev. Designed for the pixi-llm-recipes project.
allowed-tools: Bash, Read, Edit, WebFetch
---

Trigger phrases: "update yourself", "update everything", "do a full update", "update all recipes".

## Never reinstall the `agents` env from inside the sandbox

The sandbox bind-mounts the host's `~/.pi/agent/settings.json` over the package-owned
copy in `$CONDA_PREFIX/home/.pi/agent/`, so re-extracting `pi-extensions` fails with
`failed to unlink … home/.pi/agent/settings.json` (EBUSY on a mountpoint) and leaves the
environment half-extracted — missing binaries, incomplete `conda-meta`.

Phase 6 below only runs `pixi update`, which touches the lockfile and never re-extracts.
But **any** `pixi run <task>` in the `agents` env triggers an implicit
`pixi install -e agents` once a local recipe has changed, which hits the same wall —
including the `llama-cpp-changelog` task used in phase 1. Run those from the host, and
repair a half-extracted env with `pixi install -e agents` from the host.

## Phases

Run each sub-skill in order, then report. Skip nothing; report "already at latest" where
nothing moved.

1. **update-llama-cpp** — bumps the active fork pin and the commented-out mainline pin in
   both `llama-cpp-source` and `llama-cpp-binary` (four version strings). Never changes `fork:`.
2. **update-pi-extensions** — npm pins in the `PLUGINS` list.
3. **update-herdr-file-viewer** — version + two SHA-256 digests.
4. **update-claude** — npm version + tarball sha256.
5. **update-herdr** — stable (Linux) and preview (Windows) pins.
6. **`pixi update`** from the project root, to refresh `pixi.lock`.
7. **Report**:

```
## Update Complete

### llama.cpp
- Source: <old> → <new>          (fork pin untouched)
- Binary: <old> → <new>
- Mainline variant: <old> → <new> (source / binary)

### pi-extensions
- Updated: <packages with bumps>   /  Up to date: <rest>

### herdr-file-viewer / Claude Code / herdr
- <old> → <new>, or "already at latest <version>"

### pixi.lock
- Refreshed via `pixi update`.
```

Finish with `pixi r lint`.
