---
name: update-all
description: Update everything — llama.cpp (source + binary recipes), pi-extensions npm packages, Claude Code conda recipe, herdr (stable+preview) recipe, and run `pixi update` to refresh the lockfile. Triggered by "update yourself", "update everything", or "do a full update".
compatibility: Requires network access to api.github.com and registry.npmjs.org. Designed for the pixi-llm-recipes project.
allowed-tools: Bash, Read, Edit, WebFetch
---

## CRITICAL: never reinstall the agents env inside the sandbox

> **NEVER run `pixi install -e agents` (or `pixi r install -e agents`) from
> inside the bwrap sandbox.** It is **impossible** there: the sandbox
> bind-mounts the host's `~/.pi/agent/settings.json` over the package-owned
> copy in `$CONDA_PREFIX/home/.pi/agent/`, so any reinstall of pi-extensions
> fails when rattler-build tries to replace that file (`unlink` returns EBUSY
> on a mountpoint). The install hard-fails and the env is left half-extracted
> (missing binaries like `claude`, `pi`; `conda-meta` incomplete):
>
> ```
> Error:   × failed to unlink pi-extensions-1-hb0f4dca_0.conda
>   ╰─▶ failed to delete file: home/.pi/agent/settings.json
> ```
>
> This update's Phase 6 only runs `pixi update` (lockfile refresh) — pixi
> never re-extracts the envs there. But beware that ANY `pixi run <task>` in
> the `agents` env (e.g. the `llama-cpp-changelog` task in Phase 1/4) triggers
> an implicit `pixi install -e agents` whenever a local recipe (claude,
> pi-extensions, pi-home, …) changed, which hits the same failure and blocks
> the task from running.
>
> Workarounds, in order of preference:
>
> 1. Run the env reinstall from the **host** (outside the sandbox):
>    `pixi install -e agents`. The bind mounts do not exist there, so the
>    unlink succeeds.
> 2. If only running the changelog, pass `--repo`/refs and run it via the
>    script directly if the agents env is already installed, or wait for a
>    host-side `pixi install -e agents`.
>
> If an env is left half-extracted, repair it from the host with
> `pixi install -e agents` before continuing.

## Trigger phrases

- "update yourself"
- "update everything"
- "do a full update"
- "update all recipes"

## Steps

### Phase 1 — Update llama.cpp

Run the **update-llama-cpp** skill:

1. Fetch the latest beellama stable tag AND the latest mainline `bNNNN` tag.
2. Update the active (beellama) `version:` in both source and binary recipes.
3. Update the commented-out (mainline) `# version:` variant in both recipes.
4. Report old → new versions and release notes.

### Phase 2 — Update pi-extensions

Run the **update-pi-extensions** skill:

1. Extract pinned npm packages from the `PLUGINS` list in `pixi-recipes/pi-extensions/recipe.yaml`.
2. Fetch latest versions from the npm registry.
3. Update any out-of-date versions in `recipe.yaml`.
4. Report the summary of what was updated.

### Phase 3 — Update herdr-file-viewer

Run the **update-herdr-file-viewer** skill:

1. Read the current pinned version and SHA-256 digests from `pixi-recipes/herdr-file-viewer/recipe.yaml`.
2. Fetch the latest GitHub release tag from `smarzban/herdr-file-viewer`.
3. If a newer version is available, download the prebuilt Linux/Windows binaries, compute their SHA-256, and update the recipe.
4. Report old → new version.

### Phase 4 — Update Claude Code

Run the **update-claude** skill:

1. Read the current pinned version from `pixi-recipes/claude/recipe.yaml`.
2. Fetch the latest stable version from the npm registry.
3. If a newer version is available, download the tarball, compute its sha256, and update the recipe.
4. Report old → new version.

### Phase 5 — Update herdr

Run the **update-herdr** skill:

1. Read the current pinned versions from `pixi-recipes/herdr/recipe.yaml`.
2. Fetch the latest stable manifest from `https://herdr.dev/latest.json` and preview manifest from `https://herdr.dev/preview.json`.
3. If a newer stable or preview build is available, update the recipe.
4. Report old → new versions.

### Phase 6 — Refresh pixi lockfile

Run `pixi update` from the project root:

```bash
pixi update
```

This refreshes the `pixi.lock` file with the latest resolved versions of all dependencies.

### Phase 7 — Final summary

Present a consolidated report:

```
## Update Complete

### llama.cpp
- Source: <old> → <new>
- Binary: <old> → <new>

### pi-extensions
- Updated: <list of packages with version bumps>
- Up to date: <list of packages>

### herdr-file-viewer
- <old> → <new>   (or "already at latest <version>")

### Claude Code
- <old> → <new>   (or "already at stable <version>")

### herdr
- Stable: <old> → <new>   (or "already at latest stable <version>")
- Preview: <old> → <new>   (or "already at latest preview <tag>")

### pixi.lock
- Refreshed via `pixi update`.
```
