---
name: update-all
description: Update everything — llama.cpp (source + binary recipes), pi-extensions npm packages, Claude Code conda recipe, herdr and herdr-file-viewer recipes, then run `pixi update` to refresh the lockfile. Triggered by "update yourself", "update everything", or "do a full update".
compatibility: Requires network access to api.github.com, registry.npmjs.org and herdr.dev. Designed for the pixi-llm-recipes project.
allowed-tools: Bash, Read, Edit, WebFetch
---

Trigger phrases: "update yourself", "update everything", "do a full update", "update all recipes".

## pixi from inside the bwrap sandbox — what is and is not safe

Verified mechanics (mount layout in `scripts/bwrap-pi.sh`): `$CONDA_PREFIX/home/.pi`
is exposed rw as `~/.pi`, and the host's `~/.pi/agent/{auth,trust,settings}.json`

- `sessions/` are mounted over the package-owned copies in the env prefix. The
  `--ro-bind $CONDA_PREFIX` line is **shadowed**: bwrap applies the later
  `--bind $DIR $DIR` workdir bind (in `$ARGS`) over an ancestor of the env prefix,
  which detaches the earlier read-only submount — the env prefix is therefore
  **effectively read-write** in the sandbox, and so is the rest of the repo's
  `.pixi/` (bld, scratch, artifacts).

* **`pixi lock` / `pixi update` are lockfile-only.** They write `pixi.lock` (repo
  root is rw in the sandbox) and never sync an environment or rebuild local
  recipes — even when the lock changes, the env is left as-is. Safe from the
  sandbox, and that is all phase 6 needs. Beware the two sub-commands differ:
  `pixi lock` only checks lock validity and keeps the locked versions (it reports
  "already up-to-date" even when newer packages exist); `pixi update` bumps to the
  latest. Refreshing the lockfile means `pixi update`.
* **`pixi install -e agents` (explicit or implicit via `pixi run <task>`) must
  never run in the sandbox — it leaves a half-extracted env.** The rebuild of the
  changed local recipe succeeds in `.pixi/bld`, then the env sync begins deleting
  the old extracted files (bin/, lib/, the npm plugin tree, conda-meta) and only
  then hits the host-mounted `home/.pi/agent/settings.json`, whose unlink fails
  with **EBUSY** — the kernel treats the package file as busy because of the
  `~/.pi` bind chain, so nothing inside the namespace can remove it. The sync
  aborts mid-flight: the env loses old files (binaries, libraries, plugin
  package.jsons) while the new ones were never fully written. A working
  reproduction leaves e.g. `bin/git` unable to load (`libiconv.so.2: cannot open
  shared object file`) and every plugin's `package.json` gone.
* **Consequences.** Phase 6 (`pixi update`) runs fine from the sandbox. Any
  `pixi run <task>` in the `agents` env (including the `llama-cpp-changelog` task
  in phase 4) triggers the implicit install and destroys the env in the sandbox
  once a local recipe has changed — run it from the host, or run the underlying
  script directly (`python3 scripts/llama-cpp-changelog.py …`, stdlib-only).
* **Picking up the recipe changes, and repairing a half-extracted env, happen
  only via `pixi install -e agents` from the host** (no bind mounts there, so the
  EBUSY file deletes fine and the env syncs fully; the sandbox rebuild in
  `.pixi/bld` is reused). If a sandbox install ran anyway, tell the user to run
  it from the host before launching `pi`/`claude`/`herdr` again.

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
