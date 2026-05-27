#!/bin/bash

set -o errexit
set -o nounset

mkdir -pv ~/.pi/agent/sessions
if [ ! -f ~/.pi/agent/auth.json ]; then
  echo "{}" > ~/home/.pi/agent/auth.json
fi
mkdir -pv "$CONDA_PREFIX/home/.pi/agent"
ln -svf $PWD/models.json "$CONDA_PREFIX/home/.pi/agent/"
ln -svf ~/.pi/agent/sessions "$CONDA_PREFIX/home/.pi/agent/"
ln -svf ~/.pi/agent/auth.json "$CONDA_PREFIX/home/.pi/agent/"
