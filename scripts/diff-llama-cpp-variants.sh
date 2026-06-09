set -o xtrace
cd "$(dirname "$0")"/../pixi-recipes

echo "=== Source builds ==="
diff llama-cpp-source/cpu/recipe.yaml llama-cpp-source/cuda/recipe.yaml
diff llama-cpp-source/cpu/recipe.yaml llama-cpp-source/vulkan/recipe.yaml

echo "=== Binary builds ==="
diff llama-cpp-binary/cpu/recipe.yaml llama-cpp-binary/vulkan/recipe.yaml
diff llama-cpp-binary/cpu/recipe.yaml llama-cpp-binary/rocm/recipe.yaml
