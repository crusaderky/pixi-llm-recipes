---
name: update-llama-cpp
description: Update the llama-cpp conda recipes. Updates the active turboquant fork version + the commented-out main branch version + the last-sync comment in all recipe.yaml files. Use when the user wants to bump llama-cpp to a newer version, or asks to update/upgrade the llama-cpp recipe.
compatibility: Uses `scripts/llama-cpp-changelog.py` for changelog/merge detection. No `gh` CLI or GitHub token needed — the script works from a local commits-only git clone (cached at `~/.cache/llama-cpp-changelog/llama.cpp.git`). The PR section is skipped without GitHub auth; tags/commits/dates come from git.
allowed-tools: Bash Read Edit
---

## Context

Each `pixi-recipes/llama-cpp-source/{cpu,cuda,vulkan,rocm}/recipe.yaml` has a `context:` block with multiple entries:

- **Main branch** — `fork: ggml-org/llama.cpp`, `version: bNNNN`.
- **Turboquant fork** — `fork: TheTom/llama-cpp-turboquant`, `version: feature-turboquant-kv-cache-bNNNN-XXXXXXX`.

Either of the two above is commented out.

- The `source:` block uses `${{ fork }}` and `${{ version }}` — there is NO separate `source.rev` / commit SHA field.

When updating only the turboquant branch (no new upstream merge), the `Last sync with main at bNNNN` comment does NOT change.

When a new upstream merge occurred on the turboquant fork, you must also update the `Last sync with main at bNNNN` comment — see Phase 3 below.

## Steps

### Phase 1 — Fetch latest info

1. **Latest main branch tag** (updates the commented-out main version). Do NOT use the GitHub REST API (rate-limited without auth); use `git ls-remote` instead:
   ```bash
   git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
     | grep -oE 'refs/tags/b[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
   ```
   → `LATEST_MAIN`.

2. **Latest turboquant tag** on `TheTom/llama-cpp-turboquant`, branch `feature/turboquant-kv-cache`:
   ```bash
   git ls-remote --tags --refs https://github.com/TheTom/llama-cpp-turboquant.git \
     | grep feature-turboquant-kv-cache | sort -t'b' -k2 -n | tail -1
   ```
   The tag name (highest b-number) is the new turboquant version → `LATEST_TURBO`.

3. **Detect a new upstream merge** on the turboquant fork and find the new last-sync value:
   ```bash
   git ls-remote https://github.com/TheTom/llama-cpp-turboquant.git refs/heads/feature/turboquant-kv-cache
   ```
   If the branch HEAD matches the `LATEST_TURBO` tag SHA, the branch hasn't moved since the tag. Then use the changelog script to see what upstream main changes the latest turboquant release includes:
   ```bash
   pixi r llama-cpp-changelog <old_last_sync> <LATEST_MAIN>
   ```
   where `<old_last_sync>` is the current `# Last sync with main at bNNNN` value in `pixi-recipes/llama-cpp-source/cpu/recipe.yaml`. The script dumps tags, PRs (title + body excerpt + URL), and direct commits in that range. Inspect the PR list for a `Merge upstream/master into feature/turboquant-kv-cache` style entry — if present, the turboquant fork has synced up to `LATEST_MAIN` and the last-sync comment should be bumped to `LATEST_MAIN`. If no such merge PR appears in the range, the last-sync comment stays unchanged.

   The script is the single source of truth for "what changed since last sync" — do NOT run raw `curl` compare calls yourself; the script already does that deterministically.

### Phase 2 — Update the main branch version (commented out)

4. Read `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` and note the existing commented-out `# version: bNNNN` under `# Main branch`.

5. If it already equals `LATEST_MAIN`, report "Main branch version already up to date" and skip.

6. Otherwise update the `# version:` line under `# Main branch` in all **four** source recipes:
   - `pixi-recipes/llama-cpp-source/cpu/recipe.yaml`
   - `pixi-recipes/llama-cpp-source/cuda/recipe.yaml`
   - `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml`
   - `pixi-recipes/llama-cpp-source/rocm/recipe.yaml`
     Only change that one `# version:` line — do NOT uncomment it or touch other commented-out forks.
     The `rocm` recipe has the **same** `context:` version block as the others (only its
     `backend`, `gpu_targets`, and `dynamic_linking` differ), so it takes the identical edit.

7. Report: `Main branch version (commented out): bOLD → bNEW`

### Phase 3 — Update the turboquant fork version (active)

8. Read one source recipe; note the active `fork: TheTom/llama-cpp-turboquant` and `version: feature-turboquant-kv-cache-bNNNN-XXXXXXX`.

9. If it already equals `LATEST_TURBO`, report "Turboquant fork already up to date" and skip the version bump (but still check the last-sync comment in step 11).

10. Update the active `version:` line (the one with `fork: TheTom/llama-cpp-turboquant`) in all four source recipes.

11. **Update the `# Last sync with main at bNNNN` comment** only if Phase 1 step 3 found a new upstream merge. Set it to the upstream main tag that the merge pulled in (`LATEST_MAIN` when the merge PR's body/reference indicates main was synced to the latest). Otherwise leave the comment unchanged. Keep the `(YYYY-MM-DD)` date in sync with that tag's release date (from the changelog script's Tags section).

12. Report: `Turboquant fork (active): bOLD → bNEW` and `Last sync: bOLD → bNEW (if changed)`.

### Phase 4 — Update binary builds

13. Read `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml` and note `context.version`.

14. If it already equals `LATEST_MAIN`, report "Binary version already up to date" and skip.

15. Otherwise update `context.version` in all three binary recipes:
    - `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml`
    - `pixi-recipes/llama-cpp-binary/vulkan/recipe.yaml`
    - `pixi-recipes/llama-cpp-binary/rocm/recipe.yaml`

16. Report: `Binary version: bOLD → bNEW`

### Phase 5 — Report

17. Run `bash scripts/diff-llama-cpp-variants.sh` and show the output to confirm all source recipes agree and all binary recipes agree.

18. **Show the changelog** for the range the user cares about. If the active fork is turboquant and a new merge was found, run:
    ```bash
    pixi r llama-cpp-changelog <old_last_sync> <new_last_sync>
    ```
    and present the script's output (or a themed summary derived from it, citing PR numbers + URLs). If only the commented-out main version moved, run the script with `<old_commented_main> <LATEST_MAIN>` instead. Do not hand-roll `curl`/compare calls — the script is the canonical dumper.

19. **Report all changes**:
    - Main branch version (commented out): old → new
    - Turboquant fork (active): old → new
    - Last sync comment: old → new (if changed)
    - Binary versions: old → new (if changed)
    - Clarify which changes are **in effect** (active fork) vs **commented out**.

20. Run `pixi lock` to regenerate the lockfile, then `pixi r lint` and fix any issues.
