#!/usr/bin/env bash
set -euo pipefail

# Reproduce the daemon/static-web portion of OpenDesigner's own production
# Dockerfile (deploy/Dockerfile at the pinned tag) with conda Node + pnpm
# instead of an Alpine image. No Docker, no Electron, no runtime `pnpm install`.
#
# Upstream's Dockerfile build stage runs, in order:
#   pnpm install --frozen-lockfile
#   pnpm --filter @open-design/daemon build
#   pnpm --filter @open-design/web build
#   pnpm --filter @open-design/daemon deploy --legacy --prod <dir>
# then copies the deployed daemon, apps/web/out, and the bundled resource trees
# into the runtime image. This script does the same, laying the resources out
# the way the daemon resolves them under OD_RESOURCE_ROOT (see below).

# The url source extracts with a single top-level directory; locate the repo
# root by its workspace manifest rather than hardcoding the folder name.
SRC="${SRC_DIR}"
if [ ! -f "${SRC}/pnpm-workspace.yaml" ]; then
    SRC="$(dirname "$(find "${SRC_DIR}" -maxdepth 3 -name pnpm-workspace.yaml -print -quit)")"
fi
if [ ! -f "${SRC}/pnpm-workspace.yaml" ] || [ ! -f "${SRC}/apps/daemon/package.json" ]; then
    echo "could not find the OpenDesigner source root under ${SRC_DIR}" >&2
    exit 1
fi
cd "${SRC}"

# Everything pnpm writes stays inside the throwaway work area. rattler-build
# redirects HOME to the build tree and the agent sandbox mounts $HOME as tmpfs,
# so a default ~/.local/share/pnpm store would either vanish or balloon RAM.
export PNPM_HOME="${SRC}/.pnpm-home"
export npm_config_store_dir="${SRC}/.pnpm-store"
export npm_config_cache="${SRC}/.npm-cache"
export XDG_CACHE_HOME="${SRC}/.cache"
export XDG_DATA_HOME="${SRC}/.local-share"
export PATH="${PNPM_HOME}:${PATH}"
mkdir -p "${PNPM_HOME}"

export CI=true
export NODE_ENV=production
# node-gyp: use the conda Python/toolchain, build the native modules from
# source (no prebuilt download), and never reach for a system compiler.
export npm_config_python="${PYTHON:-$(command -v python3)}"
# The Electron binary is a build-time-only dependency of apps/desktop, which
# this headless recipe never builds or deploys. Skipping its ~150 MB download
# only affects the throwaway store, not the packaged (prod-only) daemon tree.
export ELECTRON_SKIP_BINARY_DOWNLOAD=1
export npm_config_electron_skip_binary_download=1
# The static web export can exceed the default heap; upstream allows 4 GiB.
export NODE_OPTIONS="--max-old-space-size=4096"

echo "=== pnpm install --frozen-lockfile ==="
pnpm install --frozen-lockfile

echo "=== build @open-design/daemon ==="
pnpm --filter @open-design/daemon build
echo "=== build @open-design/web (static export -> apps/web/out) ==="
pnpm --filter @open-design/web build

DEPLOY="${SRC}/deploy/daemon"
rm -rf "${DEPLOY}"
echo "=== deploy production daemon ==="
pnpm --filter @open-design/daemon deploy --legacy --prod "${DEPLOY}"

APP="${PREFIX}/opt/opendesigner"
rm -rf "${APP}"
mkdir -p "${APP}/apps/web" "${APP}/resources/plugins" "${APP}/resources/data"

# Daemon (bin + dist + node_modules + package.json) and the static UI, in the
# relative layout the daemon's project-root resolution expects: PROJECT_ROOT is
# <app>, STATIC_DIR is <app>/apps/web/out (apps/daemon/src/server.ts).
cp -a "${DEPLOY}" "${APP}/apps/daemon"
cp -a "${SRC}/apps/web/out" "${APP}/apps/web/out"

# Bundled resources, laid out under resources/ exactly as the daemon resolves
# each tree when OD_RESOURCE_ROOT points here (resolveDaemonResourceDir in
# apps/daemon/src/daemon-paths.ts + server.ts). The source->dest mapping is
# upstream's own BUNDLED_RESOURCE_TREES (tools/pack/src/resources/index.ts):
# note frames/community-pets come from assets/ and plugins keep their subdir.
RES="${APP}/resources"
cp -a "${SRC}/skills"               "${RES}/skills"
cp -a "${SRC}/design-templates"     "${RES}/design-templates"
cp -a "${SRC}/design-systems"       "${RES}/design-systems"
cp -a "${SRC}/craft"                "${RES}/craft"
cp -a "${SRC}/plugins/_official"    "${RES}/plugins/_official"
cp -a "${SRC}/plugins/registry"     "${RES}/plugins/registry"
cp -a "${SRC}/assets/frames"        "${RES}/frames"
cp -a "${SRC}/assets/community-pets" "${RES}/community-pets"
cp -a "${SRC}/prompt-templates"     "${RES}/prompt-templates"
cp -a "${SRC}/data/plugin-previews" "${RES}/data/plugin-previews"

# Strip dev/build intermediates from the deployed node_modules, mirroring the
# Dockerfile's cleanup. Production modules and their licenses/notices stay.
DM="${APP}/apps/daemon/node_modules"
rm -rf "${DM}/.cache" "${DM}/@types"
rm -rf "${DM}"/.pnpm/@types+* || true
rm -rf "${DM}"/.pnpm/better-sqlite3@*/node_modules/better-sqlite3/deps || true
rm -rf "${DM}"/.pnpm/better-sqlite3@*/node_modules/better-sqlite3/src || true
find "${DM}" -type d \( \
    -name test -o -name tests -o -name "__tests__" -o \
    -name docs -o -name doc -o -name example -o -name examples -o \
    -name ".github" \
    \) -prune -exec rm -rf '{}' +
find "${DM}" -type f \( \
    -name "*.md" -o -name "*.markdown" -o \
    -name "*.d.ts" -o -name "*.d.cts" -o -name "*.d.mts" -o \
    -name "*.map" -o -name "*.tsbuildinfo" -o -name "binding.gyp" \
    \) -delete

# Relative symlink so `od` is on PATH in an activated env (prefix-portable).
mkdir -p "${PREFIX}/bin"
chmod +x "${APP}/apps/daemon/bin/od.mjs"
ln -sf "../opt/opendesigner/apps/daemon/bin/od.mjs" "${PREFIX}/bin/od"

echo "opendesigner installed to ${APP}"
