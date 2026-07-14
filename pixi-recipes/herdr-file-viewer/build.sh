#!/usr/bin/env bash
set -euo pipefail

# Install the herdr-file-viewer plugin into the conda prefix (Linux only;
# Windows is handled by build.bat).
#
# The plugin is laid down as a herdr "plugin root" under
#   ${PREFIX}/home/.config/herdr/plugins/herdr-file-viewer/
# containing the manifest (herdr-plugin.toml), the launcher scripts, the
# example config, and the prebuilt viewer binary at target/release/. At
# runtime `scripts/inject-herdr-file-viewer.sh` registers it in
# ~/.config/herdr/plugins.json pointing at this prefix, so all software stays
# in $CONDA_PREFIX and only config (the registry + the user's config.toml)
# lives in ~/.
#
# VERSION, SHA256_LINUX_X86_64 are set by recipe.yaml (build.script.env).

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64) TRIPLE="x86_64-unknown-linux-musl" ;;
    *) echo "herdr-file-viewer: no prebuilt binary for Linux/${ARCH} (only x86_64)." >&2; exit 1 ;;
esac

REPO="smarzban/herdr-file-viewer"
RELEASE="https://github.com/${REPO}/releases/download/v${VERSION}"
RAW="https://raw.githubusercontent.com/${REPO}/v${VERSION}"

ROOT="${PREFIX}/home/.config/herdr/plugins/herdr-file-viewer"
mkdir -p "${ROOT}/scripts" "${ROOT}/target/release"

# --- download + verify the prebuilt viewer binary -----------------------------------------
ASSET="herdr-file-viewer-${TRIPLE}"
echo "Downloading ${RELEASE}/${ASSET}"
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 "${RELEASE}/${ASSET}" -o "${ROOT}/target/release/herdr-file-viewer"
echo "Verifying sha256..."
echo "${SHA256_LINUX_X86_64}  ${ROOT}/target/release/herdr-file-viewer" | sha256sum -c - --strict
chmod +x "${ROOT}/target/release/herdr-file-viewer"

# --- fetch the manifest, launcher scripts, and example config from the tagged source -------
curl -fsSL "${RAW}/herdr-plugin.toml"      -o "${ROOT}/herdr-plugin.toml"
curl -fsSL "${RAW}/config.example.toml"   -o "${ROOT}/config.example.toml"
for s in fetch-or-build.sh open-file-viewer.sh open-file-viewer-tab.sh install-renderers.sh \
         fetch-or-build.ps1 open-file-viewer.ps1 open-file-viewer-tab.ps1; do
    curl -fsSL "${RAW}/scripts/${s}" -o "${ROOT}/scripts/${s}"
done

# --- a portable registry snippet (no absolute paths) for the inject script to merge --------
# Keep version/description/min_herdr_version here so the inject script does not
# hardcode them; manifest_path/plugin_root are filled at runtime from $CONDA_PREFIX.
# min_herdr_version/description are stable strings; version tracks $VERSION.
cat > "${ROOT}/entry.json" <<EOF
{
  "plugin_id": "herdr-file-viewer",
  "name": "herdr-file-viewer",
  "version": "${VERSION}",
  "min_herdr_version": "0.7.0",
  "description": "A git-aware, read-only file viewer: a keyboard-driven TUI in a herdr split pane."
}
EOF

echo "herdr-file-viewer plugin installed to ${ROOT}"