#!/usr/bin/env bash
set -euo pipefail

# Install herdr integration for Claude Code.
# Downloads the herdr binary from its latest GitHub release, runs
# `herdr integration install claude`, then deploys the output to the prefix.

export HOME="${PREFIX}/home"
# Pin rtk's global Claude config dir to the prefix (rather than relying on
# $HOME resolution) so it writes RTK.md/CLAUDE.md/settings.json into the package.
export CLAUDE_CONFIG_DIR="${PREFIX}/home/.claude"
mkdir -p "${HOME}/.claude/hooks"

# Download herdr binary from the latest stable manifest
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64) TARGET="linux-x86_64" ;;
    aarch64|arm64) TARGET="linux-aarch64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

MANIFEST="$(curl -fsSL https://herdr.dev/latest.json)"
HERDR_URL="$(echo "$MANIFEST" | node -e "process.stdin.setEncoding('utf8');let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>console.log(JSON.parse(d).assets['${TARGET}']))")"
echo "Downloading herdr from ${HERDR_URL}"
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 "${HERDR_URL}" -o /tmp/herdr
chmod +x /tmp/herdr

/tmp/herdr integration install claude
rm /tmp/herdr

# Install the rtk integration last, so its PreToolUse hook merges into the
# settings.json that herdr just wrote rather than being clobbered by it.
# --auto-patch patches settings.json without the interactive prompt (the conda
# build has no TTY). rtk leaves a settings.json.bak we don't want to package.
rtk init -g --auto-patch
rm -f "${HOME}/.claude/settings.json.bak"
