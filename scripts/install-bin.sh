#!/bin/bash

mkdir -p ~/.local/bin
mkdir -p ~/.local/share/applications
mkdir -p ~/.local/share/icons
ln -fsv $PWD/scripts/install/{claude,gh,herdr,pi} ~/.local/bin/
cp -fv scripts/install/herdr.png ~/.local/share/icons/
cp -fv scripts/install/herdr.desktop ~/.local/share/applications/
