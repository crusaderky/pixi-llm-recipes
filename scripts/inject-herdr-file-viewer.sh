#!/bin/bash
# Register the conda-packaged herdr-file-viewer plugin in herdr's plugin
# registry (~/.config/herdr/plugins.json), pointing at the plugin root shipped
# under $CONDA_PREFIX. All software (binary, manifest, scripts, example config)
# stays in $CONDA_PREFIX; only the registry file and the user's per-plugin
# config.toml live in ~/.config/herdr — no plugin files are copied into ~/.
#
# Idempotent: re-runs replace any existing registry entry with the same plugin_id
# (the conda package owns it) so version/path updates win. Runs from run-herdr.sh
# before the PIXI/CONDA environment is stripped, so $CONDA_PREFIX and node are
# available.
set -euo pipefail

PLUGIN_ROOT="${CONDA_PREFIX}/home/.config/herdr/plugins/herdr-file-viewer"
ENTRY_FILE="${PLUGIN_ROOT}/entry.json"
REGISTRY="${HOME}/.config/herdr/plugins.json"

if [ ! -f "${ENTRY_FILE}" ]; then
    echo "inject-herdr-file-viewer: ${ENTRY_FILE} not found (herdr-file-viewer package not installed in this env)" >&2
    exit 0
fi

REGISTRY_DIR="$(dirname "${REGISTRY}")"
mkdir -p "${REGISTRY_DIR}"

# Merge the plugin entry into the registry array, replacing any existing entry
# with the same plugin_id. Use node (always present in the agents env) instead
# of jq, which is not packaged for Windows on conda-forge.
node -e '
const fs = require("fs");
const [entryFile, pluginRoot, registry] = process.argv.slice(1);
const base = JSON.parse(fs.readFileSync(entryFile, "utf8"));
const entry = {
    ...base,
    manifest_path: `${pluginRoot}/herdr-plugin.toml`,
    plugin_root: pluginRoot,
    enabled: true,
    platforms: ["linux", "macos", "windows"],
    source: { kind: "local" },
};
let list;
try {
    list = JSON.parse(fs.readFileSync(registry, "utf8"));
    if (!Array.isArray(list)) list = [];
} catch {
    list = [];
}
list = list.filter((p) => p.plugin_id !== entry.plugin_id);
list.push(entry);
fs.writeFileSync(registry, JSON.stringify(list, null, 2) + "\n");
' "${ENTRY_FILE}" "${PLUGIN_ROOT}" "${REGISTRY}"

echo "inject-herdr-file-viewer: registered ${PLUGIN_ROOT} in ${REGISTRY}"