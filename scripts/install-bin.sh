#!/bin/bash
 
mkdir -p ~/.local/bin
mkdir -p ~/.local/share/applications
mkdir -p ~/.local/share/icons
ln -fsv $PWD/scripts/install/{claude,herdr,pi} ~/.local/bin/
cp -fv $PWD/scripts/install/herdr.png ~/.local/share/icons/
desktop-file-install --dir ~/.local/share/applications/ scripts/install/herdr.desktop
