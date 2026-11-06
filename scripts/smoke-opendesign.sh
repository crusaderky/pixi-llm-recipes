#!/usr/bin/env bash
# Smoke-test the installed Linux OpenDesign package without build tools,
# ambient home configuration, or any runtime package manager. Run as:
#   pixi run -e designer bash scripts/smoke-opendesign.sh
set -euo pipefail

PREFIX="${CONDA_PREFIX:?run through pixi run -e designer so CONDA_PREFIX is set}"
APP="${PREFIX}/opt/opendesign"
PORT="${OD_SMOKE_PORT:-7456}"
BASE_URL="http://127.0.0.1:${PORT}"

[ -x "${PREFIX}/bin/node" ]
[ -x "${PREFIX}/bin/opendesign" ]
[ -f "${APP}/apps/web/out/index.html" ]
[ "$(readlink "${PREFIX}/bin/opendesign")" = "../opt/opendesign/apps/daemon/bin/od.mjs" ]
case "$("${PREFIX}/bin/node" --version)" in
    v24.*) ;;
    *) echo "OpenDesign requires Node 24, got $("${PREFIX}/bin/node" --version)" >&2; exit 1 ;;
esac
for tool in pnpm node-gyp gcc g++ cc make python python3; do
    if [ -e "${PREFIX}/bin/${tool}" ]; then
        echo "unexpected build/runtime tool in designer env: ${PREFIX}/bin/${tool}" >&2
        exit 1
    fi
done
command -v ss >/dev/null || { echo "smoke test requires ss from iproute2" >&2; exit 1; }
if [ -n "$(ss -ltnH "( sport = :${PORT} )")" ]; then
    echo "TCP port ${PORT} is already in use" >&2
    exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/opendesign-smoke.XXXXXX")"
pid=""
cleanup() {
    status=$?
    trap - EXIT
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        kill "${pid}" 2>/dev/null || true
        wait "${pid}" 2>/dev/null || true
    fi
    if [ "${status}" -ne 0 ] && [ -f "${TMP}/daemon.log" ]; then
        echo "OpenDesign daemon log (${TMP}/daemon.log):" >&2
        cat "${TMP}/daemon.log" >&2
    fi
    rm -rf "${TMP}"
    exit "${status}"
}
trap cleanup EXIT

mkdir -p "${TMP}/bin" "${TMP}/home" "${TMP}/data"
ln -s "${PREFIX}/bin/node" "${TMP}/bin/node"
ln -s "${PREFIX}/bin/opendesign" "${TMP}/bin/opendesign"

env -i \
    PATH="${TMP}/bin" \
    HOME="${TMP}/home" \
    OD_DATA_DIR="${TMP}/data" \
    OD_RESOURCE_ROOT="${APP}/resources" \
    OD_BIND_HOST=127.0.0.1 \
    OD_PORT="${PORT}" \
    NODE_ENV=production \
    "${TMP}/bin/opendesign" daemon start --serve-web --no-open >"${TMP}/daemon.log" 2>&1 &
pid=$!

export OD_SMOKE_BASE_URL="${BASE_URL}"
export OD_SMOKE_DATA_DIR="${TMP}/data"
export OD_SMOKE_HOME="${TMP}/home"
"${PREFIX}/bin/node" --input-type=module <<'JS'
import fs from "node:fs";

const base = process.env.OD_SMOKE_BASE_URL;
const dataDir = process.env.OD_SMOKE_DATA_DIR;
const home = process.env.OD_SMOKE_HOME;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function get(path) {
  const response = await fetch(`${base}${path}`, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response;
}

let health;
for (let attempt = 0; attempt < 120; attempt++) {
  try {
    health = await (await get("/api/health")).json();
    break;
  } catch (error) {
    if (attempt === 119) throw error;
    await pause(250);
  }
}
if (health?.ok !== true) throw new Error(`unexpected health response: ${JSON.stringify(health)}`);

const html = await (await get("/")).text();
if (!html.includes("<title>OpenDesign</title>")) throw new Error("static UI title is missing");

const resources = [
  ["/api/skills", "skills"],
  ["/api/design-systems", "designSystems"],
  ["/api/design-templates", "designTemplates"],
  ["/api/craft", "craft"],
  ["/api/prompt-templates", "promptTemplates"],
  ["/api/plugins", "plugins"],
];
const counts = {};
for (const [path, key] of resources) {
  const payload = await (await get(path)).json();
  const entries = payload[key];
  if (!Array.isArray(entries) || entries.length === 0) throw new Error(`${path}: empty ${key}`);
  counts[key] = entries.length;

  if (key === "plugins") {
    let checkedSources = 0;
    for (const plugin of entries) {
      if (plugin && typeof plugin.source === "string" && plugin.source.startsWith("/")) {
        if (!fs.existsSync(plugin.source)) throw new Error(`missing plugin source: ${plugin.source}`);
        checkedSources++;
      }
    }
    if (checkedSources === 0) throw new Error("no absolute bundled plugin source paths found");
    counts.pluginSourcePaths = checkedSources;
  }
}

const previews = await (await get("/api/plugin-previews/manifest.json")).json();
if (!previews || typeof previews !== "object" || Object.keys(previews).length === 0) {
  throw new Error("plugin-preview manifest is empty");
}
if (!fs.existsSync(`${dataDir}/app.sqlite`)) throw new Error("OD_DATA_DIR/app.sqlite was not created");
if (fs.readdirSync(home).length !== 0) throw new Error("daemon wrote into its empty HOME");
console.log(JSON.stringify({ health: health.ok, ...counts }, null, 2));
JS

listeners="$(ss -ltnH "( sport = :${PORT} )")"
local_addresses="$(awk '{print $4}' <<<"${listeners}")"
printf 'Listening socket:\n%s\n' "${listeners}"
# Column 4 is the local address; the peer's 0.0.0.0:* wildcard is normal.
if [ -z "${local_addresses}" ] || grep -Evqx "127\.0\.0\.1:${PORT}" <<<"${local_addresses}"; then
    echo "daemon is not listening exclusively on 127.0.0.1:${PORT}" >&2
    exit 1
fi

kill "${pid}"
wait "${pid}" || true
pid=""
if [ -n "$(ss -ltnH "( sport = :${PORT} )")" ]; then
    echo "port ${PORT} is still occupied after SIGTERM" >&2
    exit 1
fi

echo "OpenDesign empty-environment smoke test passed; daemon stopped cleanly."
