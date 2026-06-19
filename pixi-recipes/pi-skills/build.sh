#!/usr/bin/env bash
set -euo pipefail

# Copy bundled skills into the pi agent skills directory (flat copy)
mkdir -p "${PREFIX}/home/.pi/agent/skills"
cp -r skills/* "${PREFIX}/home/.pi/agent/skills/"
