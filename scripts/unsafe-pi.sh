#!/bin/bash
# Run Pi with full access to the whole host
set -o errexit
set -o nounset

DIR="$(realpath "$1")"

mkdir -p ~/.pi/agent
cp -f models.json ~/.pi/agent/
rm -rf ~/.pi/agent/npm
ln -s $CONDA_PREFIX/home/.pi/agent/npm ~/.pi/agent/npm
cp -f $CONDA_PREFIX/home/.pi/agent/settings.json ~/.pi/agent/

function cleanup {
  rm ~/.pi/agent/models.json
  rm ~/.pi/agent/npm
  rm ~/.pi/agent/settings.json
}
trap cleanup EXIT

# Unset all PIXI_* and CONDA_* environment variables
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD

cd $DIR
pi ${@:2}
