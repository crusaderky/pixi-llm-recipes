#!/usr/bin/env bash
set -euo pipefail

# Copy bundled skills into the Claude Code skills directory (flat copy)
mkdir -p "${PREFIX}/home/.claude"
cp -a skills "${PREFIX}/home/.claude/"
