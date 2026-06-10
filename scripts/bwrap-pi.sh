#!/bin/bash
# Run Pi with read/write permissions in pwd and no access anywhere else
# Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
set -o errexit
set -o nounset

if [ "$1" == "-" ]; then
  echo "Running in empty temporary directory"
  echo "Use \`pixi run pi <directory>\` to move to a specific directory."
  ARGS="--tmpfs /tmp/pi --chdir /tmp/pi"
else
  DIR="$(realpath "$1")"
  ARGS="--bind $DIR $DIR --chdir $DIR"
fi

_PIXI_ROOT="$(dirname "$(dirname "$PIXI_EXE")")"  # Typically ~/.pixi
_CONDA_PREFIX="$CONDA_PREFIX"
MODELS_JSON="$PWD/models.$PIXI_ENVIRONMENT_NAME.json"

mkdir -p ~/.cache/ccache
mkdir -p ~/.cache/pip
mkdir -p ~/.cache/pre-commit
mkdir -p ~/.cache/rattler
mkdir -p ~/.cache/uv
mkdir -p ~/.pi/agent/sessions
mkdir -p ~/.config/rpiv-web-tools

for f in auth trust settings; do
  if [ ! -f ~/.pi/agent/$f.json ]; then
    echo "{}" > ~/.pi/agent/$f.json
  fi
done

bash "$(dirname "$0")/inject-pi-extensions.sh"

# Unset all PIXI_* and CONDA_* environment variables
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD

bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --ro-bind "$_CONDA_PREFIX"              "$_CONDA_PREFIX" \
  --bind "$HOME/.cache/ccache"            "$HOME/.cache/ccache" \
  --bind "$HOME/.cache/pip"               "$HOME/.cache/pip" \
  --bind "$HOME/.cache/pre-commit"        "$HOME/.cache/pre-commit" \
  --bind "$HOME/.cache/rattler"           "$HOME/.cache/rattler" \
  --bind "$HOME/.cache/uv"                "$HOME/.cache/uv" \
  --bind "$HOME/.config/rpiv-web-tools"   "$HOME/.config/rpiv-web-tools" \
  --bind "$_CONDA_PREFIX/home/.pi"        "$HOME/.pi" \
  --bind "$HOME/.pi/agent/auth.json"      "$HOME/.pi/agent/auth.json" \
  --bind "$HOME/.pi/agent/trust.json"     "$HOME/.pi/agent/trust.json" \
  --bind "$HOME/.pi/agent/settings.json"  "$HOME/.pi/agent/settings.json" \
  --bind "$HOME/.pi/agent/sessions"       "$HOME/.pi/agent/sessions" \
  --ro-bind "$_PIXI_ROOT"                 "$_PIXI_ROOT" \
  $ARGS \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${@:2}"
