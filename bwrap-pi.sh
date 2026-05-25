#!/bin/bash
# Run Pi with read/write permissions in pwd and no access anywhere else
# Needs AppArmor config. Create file /etc/apparmor.d/bwrap:
#
# abi <abi/4.0>,
# include <tunables/global>
#
# profile bwrap /usr/bin/bwrap flags=(unconfined) {
#   userns,
#   include if exists <local/bwrap>
# }
set -o errexit
set -o nounset

DIR="$(realpath "$1")"
bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --ro-bind "$CONDA_PREFIX" "$CONDA_PREFIX" \
  --ro-bind "$HOME/.pixi" "$HOME/.pixi" \
  --bind "$HOME/.pi" "$HOME/.pi" \
  --bind "$DIR" "$DIR" \
  --chdir "$DIR" \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${@:2}"
