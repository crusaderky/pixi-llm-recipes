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

echo "--- active fork + version (source and binary should agree) ---"
sed -n '/^context:/,/^package:/p' "$SRC" | grep -E '^  (fork|version):'
echo
sed -n '/^context:/,/^package:/p' "$BIN" | grep -E '^  (fork|version):'

echo "--- retained (commented-out) variants ---"
grep -E '^  # (fork|version):' "$SRC" || true
echo
grep -E '^  # (fork|version):' "$BIN" || true

echo "--- source backends exposed as flags (variants.yaml) ---"
cat pixi-recipes/llama-cpp-source/variants.yaml
echo "--- binary backends exposed as flags (variants.yaml) ---"
cat pixi-recipes/llama-cpp-binary/variants.yaml
