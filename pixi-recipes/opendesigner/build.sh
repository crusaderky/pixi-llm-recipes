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

# rattler-build extracts the source into <work>/open-design (source.target_directory),
# and SRC_DIR is the work dir -- but that layout has moved between versions, so both
# are accepted (mirrors the strata recipe's two-line check).
SRC="${SRC_DIR}/open-design"
if [ ! -f "${SRC}/pnpm-workspace.yaml" ]; then
    SRC="${SRC_DIR}"
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
# node-gyp: use the conda Python/toolchain and never reach for a system compiler.
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

# better-sqlite3's install script prefers prebuild-install, whose Debian-built
# Node-ABI tarball is not covered by the lockfile's package integrity hash. Replace
# it with the pinned package source, compiled by the conda toolchain, before the
# production deploy copies the addon. Scope build_from_source to this one command:
# sharp treats any non-empty value as a request to compile sharp as well.
echo "=== rebuild better-sqlite3 from pinned source ==="
rm -rf "${SRC}/.npm-cache/_prebuilds"
npm_config_build_from_source=true pnpm --filter @open-design/daemon rebuild better-sqlite3

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
mkdir -p "${APP}/apps/web"

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
# source->dest pairs, one per upstream BUNDLED_RESOURCE_TREES entry. The count is
# cross-checked against tools/pack below so an upstream addition cannot ship silently.
declare -a RESOURCE_TREES=(
    "skills:skills"
    "design-templates:design-templates"
    "design-systems:design-systems"
    "craft:craft"
    "plugins/_official:plugins/_official"
    "plugins/registry:plugins/registry"
    "assets/frames:frames"
    "assets/community-pets:community-pets"
    "prompt-templates:prompt-templates"
    "data/plugin-previews:data/plugin-previews"
)
for pair in "${RESOURCE_TREES[@]}"; do
    from="${pair%%:*}"; to="${pair#*:}"
    mkdir -p "$(dirname "${RES}/${to}")"
    cp -a "${SRC}/${from}" "${RES}/${to}"
done

# Resource-drift tripwire (DESIGN.md step 6): a renamed source already fails loudly
# (set -e + cp of a missing path), but an upstream-*added* tree would be dropped
# silently and ship a different product. Assert every installed tree is non-empty
# and that the number of trees declared upstream equals the number this script
# installs. grep/sed arithmetic only -- no jq, no node.
for pair in "${RESOURCE_TREES[@]}"; do
    to="${pair#*:}"
    if [ -z "$(ls -A "${RES}/${to}" 2>/dev/null)" ]; then
        echo "resource tree ${to} is empty after install" >&2
        exit 1
    fi
done
IDX="${SRC}/tools/pack/src/resources/index.ts"
declared="$(sed -n '/const BUNDLED_RESOURCE_TREES = \[/,/^] as const;/p' "${IDX}" | grep -c '{ from:')"
if [ "${declared}" -ne "${#RESOURCE_TREES[@]}" ]; then
    echo "resource drift: ${IDX} declares ${declared} BUNDLED_RESOURCE_TREES but build.sh installs ${#RESOURCE_TREES[@]}" >&2
    echo "update RESOURCE_TREES in build.sh to match upstream before packaging" >&2
    exit 1
fi

# Strip dev/build intermediates from the deployed node_modules, mirroring the
# Dockerfile's cleanup. Production modules and their licenses/notices stay.
DM="${APP}/apps/daemon/node_modules"
rm -rf "${DM}/.cache" "${DM}/@types"
rm -rf "${DM}"/.pnpm/@types+* || true
rm -rf "${DM}"/.pnpm/better-sqlite3@*/node_modules/better-sqlite3/deps || true
rm -rf "${DM}"/.pnpm/better-sqlite3@*/node_modules/better-sqlite3/src || true

# Remove host absolute paths from packaged content (DESIGN.md step 8). pnpm records
# the build work area's store path in .modules.yaml and writes a NODE_PATH pointing
# at the deploy work dir into every .bin shim. The prod daemon resolves modules
# through dist/ (0 references to .bin/), so both are dead weight that would otherwise
# bake "/home/<builder>/..." into the package.
rm -f "${DM}/.modules.yaml"
find "${DM}" -type d -name .bin -prune -exec rm -rf '{}' +

# Drop native payload that cannot load on this package's only platform. The recipe
# is linux-x86_64-only (build.skip + the feature's platforms), so the loaders can
# never select these: onnxruntime picks bin/napi-v3/<platform>/<arch> and node-pty's
# lib/utils.js loadNativeModule() picks prebuilds/${platform}-${arch}. node-pty ships
# no linux-x64 prebuild at all, so its whole prebuilds/ tree is dead here. Nothing
# under linux is touched.
#
# napi-v3/linux/arm64 (34 MiB) is equally unloadable on an x86_64-only package, but it
# is deliberately left in: the installed tree is already under DESIGN.md's ~1 GiB
# guardrail at 960 MiB, so this stays the first lever to pull if that changes rather
# than widening this pass's diff for 3%.
rm -rf "${DM}"/.pnpm/onnxruntime-node@*/node_modules/onnxruntime-node/bin/napi-v3/darwin
rm -rf "${DM}"/.pnpm/onnxruntime-node@*/node_modules/onnxruntime-node/bin/napi-v3/win32
rm -rf "${DM}"/.pnpm/node-pty@*/node_modules/node-pty/prebuilds/darwin-*
rm -rf "${DM}"/.pnpm/node-pty@*/node_modules/node-pty/prebuilds/win32-*

find "${DM}" -type d \( \
    -name test -o -name tests -o -name "__tests__" -o \
    -name docs -o -name doc -o -name example -o -name examples -o \
    -name ".github" \
    \) -prune -exec rm -rf '{}' +
# READMEs/changelogs go; licenses and third-party notices stay (DESIGN.md step 7
# "Preserve licenses and bundled third-party notices") -- the exclusion keeps the 28
# license/notice markdown files the sweep would otherwise take, e.g. tar's, jose's and
# pdf-lib's LICENSE.md and lie's and express-rate-limit's license.md. (@img/sharp-
# libvips-linux-x64 ships no root notice at all -- only lib/, package.json and
# versions.json -- so it is not an example of this, only of the README going.)
find "${DM}" -type f \( -name "*.md" -o -name "*.markdown" \) \
    ! -iname 'licen[cs]e*' ! -iname 'notice*' ! -iname '*third*party*' ! -iname 'copying*' \
    -delete
find "${DM}" -type f \( \
    -name "*.d.ts" -o -name "*.d.cts" -o -name "*.d.mts" -o \
    -name "*.map" -o -name "*.tsbuildinfo" -o -name "binding.gyp" \
    \) -delete

# DESIGN.md step 7 also names the app's own TypeScript sources/maps. The daemon's
# compiled dist/ ships declaration files and sourcemaps it never loads at runtime;
# strip them (keep the emitted .js/.cjs/.mjs and dist/ itself) and the tsc build
# intermediates under apps/daemon. Third-party *.ts sources under node_modules are
# left in place deliberately (upstream fidelity: a dep's published src/*.ts can be
# its entry point).
find "${APP}/apps/daemon/dist" -type f \( \
    -name "*.d.ts" -o -name "*.d.cts" -o -name "*.d.mts" -o \
    -name "*.map" -o -name "*.tsbuildinfo" \
    \) -delete
find "${APP}/apps/daemon" -maxdepth 1 -type f -name "*.tsbuildinfo" -delete

# Verify the exact deployed addon, after all cleanup, under the build's Node 24 ABI.
# This catches both a broken production deploy and accidental removal of native files.
echo "=== smoke-test deployed better-sqlite3 ==="
node - "${DM}/better-sqlite3" <<'JS'
const Database = require(process.argv[2]);
const db = new Database(":memory:");
const row = db.prepare("SELECT sqlite_version() AS version").get();
db.close();
console.log(`better-sqlite3 loaded; SQLite ${row.version}`);
JS

# Relative symlink so `od` is on PATH in an activated env (prefix-portable).
mkdir -p "${PREFIX}/bin"
chmod +x "${APP}/apps/daemon/bin/od.mjs"
ln -sf "../opt/opendesigner/apps/daemon/bin/od.mjs" "${PREFIX}/bin/od"

echo "opendesigner installed to ${APP}"
