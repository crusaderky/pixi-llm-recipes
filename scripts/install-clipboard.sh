#!/bin/bash
# Install wl-clipboard so herdr's copy-on-select can write the system clipboard.
#
# herdr is a TUI; to set the clipboard it shells out to wl-copy (Wayland) or
# xclip/xsel (X11), falling back to an OSC 52 escape sequence when none is
# found. VTE-based terminals (Ptyxis, GNOME Terminal) silently drop OSC 52, so
# on a stock Wayland desktop herdr's "copied to clipboard" toast lies — nothing
# reaches the clipboard — until wl-clipboard is installed.
#
# Run via `pixi run install`. Uses sudo only when needed: no-op when wl-copy is
# already present, and no sudo when already root.
set -o errexit
set -o nounset

if command -v wl-copy > /dev/null; then
  echo "wl-clipboard is already installed."
  exit 0
fi

if ! command -v apt-get > /dev/null; then
  echo "wl-copy not found and apt-get is unavailable; install wl-clipboard with your package manager." >&2
  exit 0
fi

SUDO=""
if [ "$(id -u)" != "0" ]; then
  SUDO="sudo"
fi

echo "Installing wl-clipboard"
$SUDO apt-get install -y wl-clipboard
echo "wl-clipboard installed."
