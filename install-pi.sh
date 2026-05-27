#!/bin/bash

set -o errexit
set -o nounset

mkdir -pv ~/.pi/agent/sessions
if [ ! -f ~/.pi/agent/auth.json ]; then
  echo "{}" > ~/home/.pi/agent/auth.json
fi

# This script runs at every environment activation.
# Exit early if nothing changed since the previous run.
CHECKSUM_FILE="$CONDA_PREFIX/install-pi.check"
CHECKSUM="$(sha256sum "${BASH_SOURCE[0]}")"
if [ -f "$CHECKSUM_FILE" ] && [ "$(cat "$CHECKSUM_FILE")" = "$CHECKSUM" ]; then
  exit 0
fi

mkdir -pv "$CONDA_PREFIX/home/.pi/agent"
ln -svf $PWD/models.json "$CONDA_PREFIX/home/.pi/agent/"
ln -svf ~/.pi/agent/sessions "$CONDA_PREFIX/home/.pi/agent/"
ln -svf ~/.pi/agent/auth.json "$CONDA_PREFIX/home/.pi/agent/"

export HOME="$CONDA_PREFIX/home"

pi install npm:pi-autoresearch@1.4.0
pi install npm:pi-btw@0.4.0
pi install npm:pi-token-speed@0.2.1
pi install npm:pi-web-access@0.10.7
pi install npm:@tmustier/pi-usage-extension@0.3.2

echo "$CHECKSUM" > "$CHECKSUM_FILE"
