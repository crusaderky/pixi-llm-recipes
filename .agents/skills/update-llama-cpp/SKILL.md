---
name: update-llama-cpp
description: Update the llama-cpp conda recipe to the latest upstream release from github.com/ggml-org/llama.cpp. Use when the user wants to bump llama-cpp to a newer version, or asks to update/upgrade the llama-cpp recipe.
compatibility: Requires network access to api.github.com. Designed for the pixi-llm-recipes project.
allowed-tools: WebFetch Read Edit
---

## Steps

### Phase 1 — Fetch latest upstream info

1. **Fetch the latest release tag** via the GitHub API:
   - GET `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest`
   - Extract `.tag_name` (e.g. `b9520`) — this becomes the new `context.version`.

2. **Resolve the commit SHA** for that tag:
   - GET `https://api.github.com/repos/ggml-org/llama.cpp/git/ref/tags/<tag_name>`
   - If `.object.type` is `"commit"`, use `.object.sha` directly.
   - If `.object.type` is `"tag"` (annotated tag), follow `.object.url` with another GET and use the returned `.object.sha`.

3. **Fetch the release notes** (for the changelog at the end):
   - GET `https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/<tag_name>`
   - Save `.body` as the release notes for the new version.

### Phase 2 — Update source builds

4. **Read the current source recipe** at `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` and note the existing `context.version` and `source.rev`.

5. **Check if already up to date**: if both match, report that and stop.

6. **Update the source recipe**:
   - Set `context.version` to the new tag name.
   - Set `source.rev` to the resolved commit SHA.

7. **Update source recipe variants**:
   - Repeat step 6 for `pixi-recipes/llama-cpp-source/cuda/recipe.yaml`.
   - Repeat step 6 for `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml`.

### Phase 3 — Update binary builds

8. **Read the binary build script** at `pixi-recipes/llama-cpp-binary/build.sh`.

9. **Check if already up to date**: if the `VERSION` variable matches the new tag, report that and stop.

10. **Update the binary version**:
    - Update the `VERSION="<tag>"` line in `pixi-recipes/llama-cpp-binary/build.sh` to the new tag name.

### Phase 4 — Report

11. **Report differences between source variants**: Run `bash scripts/diff-llama-cpp-variants.sh` and show the output.

12. **Report the change**:
    - Show old → new version and old → new commit SHA for source builds.
    - Show old → new version for binary builds (updated in `build.sh`).
    - Show the release notes from step 3.
