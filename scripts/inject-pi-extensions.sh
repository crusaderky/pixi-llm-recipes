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

# Merge the packages block from CONDA_CFG into HOME_CFG.
# Use node (always present, as pi itself needs it) instead of jq,
# which is not packaged for Windows on conda-forge.
node -e '
const fs = require("fs");
const [condaCfg, homeCfg] = process.argv.slice(1);
const cfg = JSON.parse(fs.readFileSync(homeCfg, "utf8"));
cfg.packages = JSON.parse(fs.readFileSync(condaCfg, "utf8")).packages ?? null;
fs.writeFileSync(homeCfg, JSON.stringify(cfg, null, 2) + "\n");
' "$CONDA_CFG" "$HOME_CFG"
