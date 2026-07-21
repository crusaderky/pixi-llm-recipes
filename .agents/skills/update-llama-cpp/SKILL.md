---
name: update-llama-cpp
description: Update both llama-cpp conda recipes (source + binary) to the latest upstream release. Updates the version pin in the two recipe.yaml files. Use when the user wants to bump llama-cpp to a newer version.
compatibility: Uses `scripts/llama-cpp-changelog.py` for changelog/merge detection. No `gh` CLI or GitHub token needed — the script works from a local commits-only git clone (cached at `~/.cache/llama-cpp-changelog/llama.cpp.git`). The PR section is skipped without GitHub auth; tags/commits/dates come from git.
allowed-tools: Bash Read Edit
---

## Context

`pixi-recipes/llama-cpp-source/recipe.yaml` is a **single** recipe whose backends
(cpu, cuda, vulkan, rocm) are selected via the `backend` matrix in
`variants.yaml` and exposed as build `flags`. The `context:` block pins the
active fork:

- **Main branch** — `fork: ggml-org/llama.cpp`, `version: bNNNN`.

The `source:` block uses `${{ fork }}` and `${{ version }}` — there is NO
separate `source.rev` / commit SHA field.

## Steps

### Phase 1 — Fetch latest version

1. **Get latest upstream tag** (do NOT use GitHub REST API — rate-limited without auth):
   ```bash
   git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
     | grep -oE 'refs/tags/b[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
   ```
   → `LATEST`.

### Phase 2 — Update source build recipe

2. Read `pixi-recipes/llama-cpp-source/recipe.yaml` and note `context.version`.

3. If already equals `LATEST`, report "Source version already up to date" and skip.

4. Otherwise update `context.version` in the single source recipe `pixi-recipes/llama-cpp-source/recipe.yaml`.

5. Report: `Source version: bOLD → bNEW`

### Phase 3 — Update binary build recipe

6. Read `pixi-recipes/llama-cpp-binary/recipe.yaml` and note `context.version`.

7. If already equals `LATEST`, report "Binary version already up to date" and skip.

8. Otherwise update `context.version` in the single binary recipe `pixi-recipes/llama-cpp-binary/recipe.yaml`.

9. Report: `Binary version: bOLD → bNEW`

### Phase 4 — Verify & report

10. Run `bash scripts/diff-llama-cpp-variants.sh` and show the output to confirm all source recipes agree and all binary recipes agree.

11. **Show the changelog** for the range being updated. Run:
    ```bash
    pixi r llama-cpp-changelog <old_version> <LATEST>
    ```
    and present the script's output (or a themed summary derived from it, citing PR numbers + URLs). Do not hand-roll `curl`/compare calls — the script is the canonical dumper.

12. **Report all changes**:
    - Source version: old → new
    - Binary version: old → new (if changed)

13. Run `pixi lock` to regenerate the lockfile, then `pixi r lint` and fix any issues.
