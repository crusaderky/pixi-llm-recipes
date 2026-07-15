#!/bin/bash
set -o errexit
set -o nounset

cd "$(dirname "$0")"/..
SRC=pixi-recipes/llama-cpp-source/recipe.yaml
BIN=pixi-recipes/llama-cpp-binary/recipe.yaml

# With the flag-based refactor (one recipe per build kind, backends selected
# via `variant`/`flags` in variants.yaml), version drift between backends is
# impossible: the active `version:` lives in a single place. This script now
# just confirms the source and binary recipes agree on the upstream tag.

echo "--- active source version (should match binary VERSION) ---"
grep -E '^  version:' "$SRC"
grep -E '^  version:' "$BIN"

echo "--- source backends exposed as flags (variants.yaml) ---"
grep -E 'variant:' pixi-recipes/llama-cpp-source/variants.yaml
echo "--- binary backends exposed as flags (variants.yaml) ---"
grep -E 'variant:' pixi-recipes/llama-cpp-binary/variants.yaml
