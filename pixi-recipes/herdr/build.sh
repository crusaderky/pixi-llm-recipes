#!/usr/bin/env bash
set -euo pipefail

# Install herdr pre-built binary into the conda prefix (Linux only; Windows is
# handled by build.bat).
#
# VERSION_STABLE, SHA256_STABLE_X86_64, and SHA256_STABLE_AARCH64 are set by
# recipe.yaml (build.script.env).

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64) TARGET="linux-x86_64"; EXPECTED_SHA256="${SHA256_STABLE_X86_64}" ;;
    aarch64|arm64) TARGET="linux-aarch64"; EXPECTED_SHA256="${SHA256_STABLE_AARCH64}" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

URL="https://github.com/ogulcancelik/herdr/releases/download/v${VERSION_STABLE}/herdr-${TARGET}"

echo "Downloading ${URL}"
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 "${URL}" -o herdr

echo "Verifying sha256..."
echo "${EXPECTED_SHA256}  herdr" | sha256sum -c - --strict

chmod +x herdr
mkdir -p "${PREFIX}/bin"
mv herdr "${PREFIX}/bin/herdr"

echo "herdr installed to ${PREFIX}/bin/herdr"
