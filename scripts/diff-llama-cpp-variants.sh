#!/bin/bash
set -o errexit
set -o nounset

cd "$(dirname "$0")"/..
SRC=pixi-recipes/llama-cpp-source
BIN=pixi-recipes/llama-cpp-binary

echo "=== Source builds ==="
# cpu/cuda/vulkan are structurally identical (only `backend:` differs), so a full
# diff cleanly shows any version drift between them.
diff -u0 $SRC/cpu/recipe.yaml $SRC/cuda/recipe.yaml || true
diff -u0 $SRC/cpu/recipe.yaml $SRC/vulkan/recipe.yaml || true

# rocm intentionally diverges (system-ROCm build: extra gpu_targets / dynamic_linking
# keys, no conda GPU deps), so a full diff would be noise. It shares the same `context:`
# version block though, so just confirm the active `version:` line agrees across all four.
echo "--- active version: across all source variants (should be identical) ---"
grep -E '^  version:' $SRC/cpu/recipe.yaml $SRC/cuda/recipe.yaml $SRC/vulkan/recipe.yaml $SRC/rocm/recipe.yaml

echo "=== Binary builds ==="
diff -u0 $BIN/cpu/recipe.yaml $BIN/vulkan/recipe.yaml || true
diff -u0 $BIN/cpu/recipe.yaml $BIN/rocm/recipe.yaml || true
