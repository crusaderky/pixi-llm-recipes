#!/usr/bin/env bash
set -euo pipefail

export HOME="${PREFIX}/home"
rm -rf "${PREFIX}/home/.pi/agent/npm"
rm -f "${PREFIX}/home/.pi/agent/settings.json"

# PLUGINS is set in recipe.yaml
for plugin in ${PLUGINS}; do
    pi install "npm:${plugin}"
done

npm approve-scripts --allow-scripts-pending

# Install rtk integration for pi
rtk init -g --agent pi --auto-patch

# Install herdr integration. Download the herdr binary from its latest GitHub
# release via the manifest, run the integration install, then discard the binary.
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
/tmp/herdr integration install pi
rm /tmp/herdr
