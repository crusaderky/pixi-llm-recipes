#!/bin/bash
# Run Pi with full access to the whole host
set -o errexit
set -o nounset

mkdir -p ~/.pi/agent

rm -rf ~/.pi/agent/npm
ln -s "$CONDA_PREFIX/home/.pi/agent/npm" ~/.pi/agent/npm
function cleanup {
  rm ~/.pi/agent/npm
}
trap cleanup EXIT

bash "$(dirname "$0")/inject-pi-extensions.sh"

# Unset all PIXI_* and CONDA_* environment variables
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD

if [ "$1" == "-" ]; then
  echo "Running in empty temporary directory"
  echo "Use \`pixi run pi <directory>\` to move to a specific directory."
  DIR=$(mktemp -d)
  trap "cleanup && rm -rf $DIR" EXIT
else
  DIR="$1"
fi

cd "$DIR"
pi ${@:2}
