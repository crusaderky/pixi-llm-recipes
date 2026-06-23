#!/usr/bin/env bash
set -euo pipefail

# Copy bundled skills into the pi agent skills directory (flat copy)
mkdir -p "${PREFIX}/home/.pi/agent"
cp -a agents "${PREFIX}/home/.pi/agent/"
cp -a skills "${PREFIX}/home/.pi/agent/"
cp -a AGENTS.md "${PREFIX}/home/.pi/agent/"
