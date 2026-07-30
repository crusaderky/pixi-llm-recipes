#!/usr/bin/env bash
set -euo pipefail

# Install the rtk integration for Claude Code.
# Generates CLAUDE.md, RTK.md, and patches settings.json.
# Settings are deployed to the host by scripts/inject-claude-extensions.sh.

export HOME="${PREFIX}/home"
# Pin rtk's global Claude config dir to the prefix (rather than relying on
# $HOME resolution) so it writes RTK.md/CLAUDE.md/settings.json into the package.
export CLAUDE_CONFIG_DIR="${PREFIX}/home/.claude"
mkdir -p "${HOME}/.claude/hooks"

rtk init -g --auto-patch
rm -f "${HOME}/.claude/settings.json.bak"
