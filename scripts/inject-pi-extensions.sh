#!/bin/bash
# Update ~/.pi/agent/settings.json in place and replace the
# 'packages' section with the same section from $CONDA_PREFIX/home/.pi/agent/settings.json

set -o errexit
set -o nounset

CONDA_CFG="$CONDA_PREFIX/home/.pi/agent/settings.json"
HOME_CFG=~/.pi/agent/settings.json

if [ ! -f $HOME_CFG ]; then
  echo '{}' > $HOME_CFG
fi

# Use jq to merge the packages block from KEEP_PACKAGES into KEEP_REST
# First, extract packages from KEEP_PACKAGES as a compact JSON string
PACKAGES=$(jq -c '.packages' "$CONDA_CFG")
# Then, apply that JSON string to the packages field of KEEP_REST
jq --argjson pkg "$PACKAGES" '.packages = $pkg' $HOME_CFG > $HOME_CFG.new
mv $HOME_CFG.new $HOME_CFG
