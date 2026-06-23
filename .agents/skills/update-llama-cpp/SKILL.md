---
name: update-llama-cpp
description: Update the llama-cpp conda recipes. Updates the active turboquant fork version + the commented-out main branch version + the last-sync comment in all recipe.yaml files. Use when the user wants to bump llama-cpp to a newer version, or asks to update/upgrade the llama-cpp recipe.
compatibility: Requires network access to api.github.com. Designed for the pixi-llm-recipes project.
allowed-tools: WebFetch Bash Read Edit
---

## Context

Each `pixi-recipes/llama-cpp-source/{cpu,cuda,vulkan}/recipe.yaml` has a `context:` block with multiple entries:

- **Main branch** — `fork: ggml-org/llama.cpp`, `version: bNNNN`.
- **Turboquant fork** — `fork: TheTom/llama-cpp-turboquant`, `version: feature-turboquant-kv-cache-bNNNN-XXXXXXX`.

Either of the two above is commented out.

- The `source:` block uses `${{ fork }}` and `${{ version }}` — there is NO separate `source.rev` / commit SHA field.

When updating only the turboquant branch (no new upstream merge), the `Last sync with main at bNNNN` comment does NOT change.

When a new upstream merge occurred on the turboquant fork, you must also update the `Last sync with main at bNNNN` comment — see Phase 3 below.

## Steps

### Phase 1 — Fetch latest info

1. **Fetch the latest main branch tag** via the GitHub API:
   - GET `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest`
   - Extract `.tag_name` (e.g. `b9718`) — this updates the **commented-out** main branch version.

2. **Fetch the latest tag on TheTom/llama-cpp-turboquant, branch feature/turboquant-kv-cache**:
   - Run: `git ls-remote --tags --refs https://github.com/TheTom/llama-cpp-turboquant.git | grep feature-turboquant-kv-cache | sort -t'b' -k2 -n`
   - The latest tag name (highest b-number) is the new turboquant version.
   - Also check the branch HEAD: `git ls-remote https://github.com/TheTom/llama-cpp-turboquant.git refs/heads/feature/turboquant-kv-cache`
   - If the branch HEAD matches the tag SHA, the branch hasn't moved since the tag — the tag is still current.

3. **Check if the turboquant fork has merged upstream main since the last update**:
   - Run: `curl -s "https://api.github.com/repos/TheTom/llama-cpp-turboquant/commits?sha=feature/turboquant-kv-cache&per_page=30" | python3 -c "..."` and look for a commit with message `Merge upstream/master into feature/turboquant-kv-cache` or similar.
   - If a new such merge exists, note the parent 2 SHA (the main branch commit that was merged in).
   - Fetch tags on main to find the latest tag that is an ancestor of that commit.

### Phase 2 — Update the main branch version (commented out)

4. **Read one source recipe** at `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` and note the existing commented-out `# version: bNNNN` under `# Main branch`.

5. **Check if already up to date**: if the commented-out version matches the latest main tag, report that and skip this phase.

6. **Update the commented-out main branch version** in all **three** source recipes:
   - `pixi-recipes/llama-cpp-source/cpu/recipe.yaml`
   - `pixi-recipes/llama-cpp-source/cuda/recipe.yaml`
   - `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml`
   - Only change the `# version:` line under `# Main branch` — do NOT uncomment it or change any other commented-out fork.

7. **Report**: `Main branch version (commented out): bOLD → bNEW`

### Phase 3 — Update the turboquant fork version (active)

8. **Read one source recipe** and note the active (uncommented) `fork: TheTom/llama-cpp-turboquant` and `version: feature-turboquant-kv-cache-bNNNN-XXXXXXX`.

9. **Check if already up to date**: If the active turboquant version matches the latest tag from step 2, report that and skip this phase.

10. **Update the turboquant version** in all three source recipes — change the active `version:` line (the one with `fork: TheTom/llama-cpp-turboquant`).

11. **If a new upstream merge was found in step 3**, update the `# Last sync with main at bNNNN` comment to the latest main branch tag that was merged in. Otherwise leave the comment unchanged.

12. **Report**: `Turboquant fork (active): bOLD → bNEW`

### Phase 4 — Update binary builds

13. **Read the binary recipe** at `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml` and note `context.version`.

14. **Check if already up to date**: if `context.version` matches the latest main tag, report that and skip.

15. **Update binary version** in all three binary recipes:
    - `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml`
    - `pixi-recipes/llama-cpp-binary/vulkan/recipe.yaml`
    - `pixi-recipes/llama-cpp-binary/rocm/recipe.yaml`

### Phase 5 — Report

16. **Report differences between source variants**: Run `bash scripts/diff-llama-cpp-variants.sh` and show the output.

17. **Report all changes**:
    - Main branch version (active|commented out): old → new
    - Turboquant fork (active|commented out): old → new
    - Last sync comment: old → new (if changed)
    - Binary versions: old → new (if changed)
    - Clarify which changes are **in effect** and which are **commented out**.
