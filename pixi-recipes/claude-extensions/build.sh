#!/usr/bin/env bash
set -euo pipefail

# Install herdr integration for Claude Code.
# Downloads the herdr binary from its latest GitHub release, runs
# `herdr integration install claude`, then deploys the output to the prefix.

export HOME="${PREFIX}/home"
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
