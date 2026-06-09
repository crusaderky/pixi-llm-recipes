set -o xtrace
cd "$(dirname "$0")"

diff cpu/recipe.yaml cuda/recipe.yaml
diff cpu/recipe.yaml vulkan/recipe.yaml
