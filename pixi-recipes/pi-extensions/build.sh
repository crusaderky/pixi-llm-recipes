#!/usr/bin/env bash
set -euo pipefail

export HOME="${PREFIX}/home"
rm -rf "${PREFIX}/home/.pi/agent/npm"
rm -f "${PREFIX}/home/.pi/agent/settings.json"

# PLUGINS is set in recipe.yaml
for plugin in ${PLUGINS}; do
    pi install "npm:${plugin}"
done

rtk init -g --agent pi
