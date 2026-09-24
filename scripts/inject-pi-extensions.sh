#!/bin/bash
# Update ~/.pi/agent/settings.json in place and replace the
# 'packages' section with the same section from $CONDA_PREFIX/home/.pi/agent/settings.json

set -o errexit
set -o nounset

CONDA_CFG="$CONDA_PREFIX/home/.pi/agent/settings.json"
HOME_CFG=~/.pi/agent/settings.json

if [ ! -f "$HOME_CFG" ]; then
  echo '{}' > "$HOME_CFG"
fi

# Merge the packages block from CONDA_CFG into HOME_CFG. pi-subagents stays
# installed and updateable, but none of its resources autoload: its extension,
# skills, and prompt templates add roughly 3.5k tokens to every system prompt.
# scripts/bwrap-pi.sh and scripts/pi-unsafe.sh load those resources explicitly
# for `pi --subagents`.
# Use node (always present, as pi itself needs it) instead of jq,
# which is not packaged for Windows on conda-forge.
node -e '
const fs = require("fs");
const [condaCfg, homeCfg] = process.argv.slice(1);
const cfg = JSON.parse(fs.readFileSync(homeCfg, "utf8"));
const packages = JSON.parse(fs.readFileSync(condaCfg, "utf8")).packages ?? null;
const isSubagents = (entry) =>
  typeof entry === "string" &&
  (entry === "npm:pi-subagents" || entry.startsWith("npm:pi-subagents@"));
cfg.packages = packages?.map((entry) =>
  isSubagents(entry)
    ? { source: entry, extensions: [], skills: [], prompts: [] }
    : entry
) ?? null;
fs.writeFileSync(homeCfg, JSON.stringify(cfg, null, 2) + "\n");
' "$CONDA_CFG" "$HOME_CFG"
