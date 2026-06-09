cd "$(dirname "$0")"/..
SRC=pixi-recipes/llama-cpp-source
BIN=pixi-recipes/llama-cpp-binary

echo "=== Source builds ==="
diff -u0 $SRC/cpu/recipe.yaml $SRC/cuda/recipe.yaml || true
diff -u0 $SRC/cpu/recipe.yaml $SRC/vulkan/recipe.yaml || true

echo "=== Binary builds ==="
diff -u0 $BIN/cpu/recipe.yaml $BIN/vulkan/recipe.yaml || true
diff -u0 $BIN/cpu/recipe.yaml $BIN/rocm/recipe.yaml || true
