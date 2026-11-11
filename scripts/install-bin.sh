#!/bin/bash

mkdir -p ~/.local/bin
mkdir -p ~/.local/share/applications
mkdir -p ~/.local/share/icons
# $PWD is the repo root and may contain spaces: unquoted, the brace expansion
# would hand ln a truncated path and the ~/.local/bin wrappers would point at
# nothing (or at a directory that happens to exist).
ln -fsv "$PWD"/scripts/install/{gh,herdr,opendesign,pi} ~/.local/bin/
cp -fv scripts/install/herdr.png ~/.local/share/icons/

# GNOME hides desktop entries whose Exec binary is not in PATH, so pick a
# terminal emulator that actually exists on this system: Ptyxis (Ubuntu 25.04+),
# gnome-terminal (Ubuntu <= 24.10), or let the desktop environment choose one.
# $HOME is the repo of every path below, so quote the wrapper inside Exec: a
# desktop entry splits Exec on whitespace, and an unquoted /home/my name/...
# would launch "/home/my".
HERDR_BIN="$HOME/.local/bin/herdr"
if command -v ptyxis > /dev/null; then
    EXEC="ptyxis --title=herdr -- \"$HERDR_BIN\""
    TERMINAL=false
    WMCLASS=org.gnome.Ptyxis
elif command -v gnome-terminal > /dev/null; then
    EXEC="gnome-terminal --title=herdr -- \"$HERDR_BIN\""
    TERMINAL=false
    WMCLASS=gnome-terminal-server
else
    EXEC="\"$HERDR_BIN\""
    TERMINAL=true
    WMCLASS=herdr
fi

sed -e "s|@EXEC@|$EXEC|" \
    -e "s|@TERMINAL@|$TERMINAL|" \
    -e "s|@WMCLASS@|$WMCLASS|" \
    -e "s|@HOME@|$HOME|" \
    scripts/install/herdr.desktop > ~/.local/share/applications/herdr.desktop
echo "generated '$HOME/.local/share/applications/herdr.desktop' (Exec=$EXEC)"
update-desktop-database ~/.local/share/applications 2> /dev/null || true
