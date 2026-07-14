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
# available. It also injects (once) the herdr keybindings that summon the viewer
# into ~/.config/herdr/config.toml, but only when they are not already present.
set -euo pipefail

PLUGIN_ROOT="${CONDA_PREFIX}/home/.config/herdr/plugins/herdr-file-viewer"
ENTRY_FILE="${PLUGIN_ROOT}/entry.json"
REGISTRY="${HOME}/.config/herdr/plugins.json"
CONFIG="${HOME}/.config/herdr/config.toml"

if [ ! -f "${ENTRY_FILE}" ]; then
    echo "inject-herdr-file-viewer: ${ENTRY_FILE} not found (herdr-file-viewer package not installed in this env)" >&2
    exit 0
fi

REGISTRY_DIR="$(dirname "${REGISTRY}")"
mkdir -p "${REGISTRY_DIR}"

# Merge the plugin entry into the registry array, replacing any existing entry
# with the same plugin_id. Use node (always present in the agents env) instead
# of jq, which is not packaged for Windows on conda-forge. Prints CHANGED only
# when the registry file actually changes.
if node -e '
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
const prev = JSON.stringify(list, null, 2) + "\n";
const next = JSON.stringify(list.filter((p) => p.plugin_id !== entry.plugin_id).concat(entry), null, 2) + "\n";
if (next !== prev) {
    fs.writeFileSync(registry, next);
    console.log("CHANGED");
}
' "${ENTRY_FILE}" "${PLUGIN_ROOT}" "${REGISTRY}" | grep -q CHANGED; then
    echo "inject-herdr-file-viewer: registered ${PLUGIN_ROOT} in ${REGISTRY}"
fi


# --- inject the herdr keybindings that summon the viewer (once) ---------------------------
# Append a [[keys.command]] entry only when its plugin action command is not already present
# in config.toml, so user customizations are never clobbered. TOML appends array-of-tables
# entries at end-of-file regardless of other [keys] content.
inject_keybinding() {
    local key="$1" cmd="$2" comment="$3"
    if grep -qF "$cmd" "${CONFIG}" 2>/dev/null; then
        return
    fi
    # Separate from any prior content with a single blank line (don't prepend to an empty file).
    if [ -s "${CONFIG}" ]; then
        printf '\n' >> "${CONFIG}"
    fi
    {
        printf '[[keys.command]]              # %s\n' "$comment"
        printf 'key = "%s"\n' "$key"
        printf 'type = "shell"\n'
        printf 'command = "%s"\n' "$cmd"
    } >> "${CONFIG}"
    echo "inject-herdr-file-viewer: added keybinding '${key}' -> '${cmd}' to ${CONFIG}"
}

mkdir -p "$(dirname "${CONFIG}")"
# Gnome-terminal notes:
# - Ctrl+Alt+<letter> requires Ubuntu >=24.10
# - Ctrl+Shift+F is bound by default to "find"
inject_keybinding "ctrl+f" "herdr plugin action invoke open-file-viewer --plugin herdr-file-viewer"     "herdr-file-viewer: open in a split beside your work"
inject_keybinding "alt+f"  "herdr plugin action invoke open-file-viewer-tab --plugin herdr-file-viewer" "herdr-file-viewer: open in its own tab"