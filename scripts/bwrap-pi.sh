#!/bin/bash
# Run Pi with read/write permissions in pwd and no access anywhere else
# Needs AppArmor config. Create file /etc/apparmor.d/bwrap:
#
# abi <abi/4.0>,
# include <tunables/global>
#
# profile bwrap /path/to/pixi-llm-recipes/.pixi/envs/pi/bin/bwrap flags=(unconfined) {
#   userns,
#   include if exists <local/bwrap>
# }
#
# Then run `sudo systemctl reload apparmor` to load the profile.
set -o errexit
set -o nounset

DIR="$(realpath "$1")"

_PIXI_ROOT="$(dirname "$(dirname "$PIXI_EXE")")"  # Typically ~/.pixi
_CONDA_PREFIX="$CONDA_PREFIX"

mkdir -p ~/.cache/ccache
mkdir -p ~/.cache/pip
mkdir -p ~/.cache/pre-commit
mkdir -p ~/.cache/rattler
mkdir -p ~/.cache/uv
mkdir -p ~/.pi/agent/sessions
mkdir -p ~/.config/rpiv-web-tools

if [ ! -f ~/.pi/agent/auth.json ]; then
  echo "{}" > ~/.pi/agent/auth.json
fi
mkdir -p "$CONDA_PREFIX/home/.pi/agent"

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
  --ro-bind "$_CONDA_PREFIX"            "$_CONDA_PREFIX" \
  --bind "$HOME/.cache/ccache"          "$HOME/.cache/ccache" \
  --bind "$HOME/.cache/pip"             "$HOME/.cache/pip" \
  --bind "$HOME/.cache/pre-commit"      "$HOME/.cache/pre-commit" \
  --bind "$HOME/.cache/rattler"         "$HOME/.cache/rattler" \
  --bind "$HOME/.cache/uv"              "$HOME/.cache/uv" \
  --bind "$HOME/.config/rpiv-web-tools" "$HOME/.config/rpiv-web-tools" \
  --bind "$_CONDA_PREFIX/home/.pi"      "$HOME/.pi" \
  --bind "$HOME/.pi/agent/auth.json"    "$HOME/.pi/agent/auth.json" \
  --bind "$HOME/.pi/agent/sessions"     "$HOME/.pi/agent/sessions" \
  --ro-bind "$PWD/models.json"          "$HOME/.pi/agent/models.json" \
  --ro-bind "$_PIXI_ROOT"               "$_PIXI_ROOT" \
  --bind "$DIR"                         "$DIR" \
  --chdir "$DIR" \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${@:2}"
