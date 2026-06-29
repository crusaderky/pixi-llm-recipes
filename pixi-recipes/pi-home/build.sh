#!/usr/bin/env bash
set -euo pipefail

# This will be populated on first start with downloaded tools
mkdir -p "${PREFIX}/home/.pi/agent/bin"
touch "${PREFIX}/home/.pi/agent/bin/.keep"

# Copy bundled skills into the pi agent skills directory (flat copy)
cp -a skills "${PREFIX}/home/.pi/agent/"
cp -a AGENTS.md "${PREFIX}/home/.pi/agent/"
cp -a keybindings.json "${PREFIX}/home/.pi/agent/"
cp -a web-search.json "${PREFIX}/home/.pi/"  # Not a typo
