---
name: update-llama-cpp
description: Update the llama-cpp conda recipe to the latest upstream release from github.com/ggml-org/llama.cpp. Use when the user wants to bump llama-cpp to a newer version, or asks to update/upgrade the llama-cpp recipe.
compatibility: Requires network access to api.github.com. Designed for the pixi-llm-recipes project.
allowed-tools: WebFetch Read Edit
---

## Steps

1. **Fetch the latest release tag** via the GitHub API:
   - GET `https://api.github.com/repos/ggml-org/llama.cpp/releases/latest`
   - Extract `.tag_name` (e.g. `b9520`) — this becomes the new `context.version`.

2. **Resolve the commit SHA** for that tag:
   - GET `https://api.github.com/repos/ggml-org/llama.cpp/git/ref/tags/<tag_name>`
   - If `.object.type` is `"commit"`, use `.object.sha` directly.
   - If `.object.type` is `"tag"` (annotated tag), follow `.object.url` with another GET and use the returned `.object.sha`.

3. **Read the current recipe** at `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` and note the existing `context.version` and `source.rev`.

4. **Check if already up to date**: if both match, report that and stop.

5. **Update the recipe**:
   - Set `context.version` to the new tag name.
   - Set `source.rev` to the resolved commit SHA.

6. **Update recipe variants**:
   - Repeat step 5 for `pixi-recipes/llama-cpp-source/cuda/recipe.yaml`.
   - Repeat step 5 for `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml`.

7. **Report differences between variants**: Run `bash pixi-recipes/llama-cpp/diff_variants.sh` and show the output.

8. **Report the change**: show old → new version and old → new commit SHA.
