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

# Typically ~/.pixi
export PIXI_ROOT="$(dirname "$(dirname "$PIXI_EXE")")"

mkdir -p ~/.pi/agent
cp -pvf models.json ~/.pi/agent/

bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --ro-bind "$CONDA_PREFIX" "$CONDA_PREFIX" \
  --ro-bind "$PIXI_ROOT" "$PIXI_ROOT" \
  --bind "$HOME/.pi" "$HOME/.pi" \
  --bind "$DIR" "$DIR" \
  --chdir "$DIR" \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${@:2}"
