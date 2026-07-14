#!/bin/bash
# Install the optional Markdown/Terminal renderers used by the herdr-file-viewer
# plugin to pretty-print file contents inside a herdr pane:
#
#   - delta  (git-delta on apt): syntax-highlighted, side-by-side diff/pager
#   - bat    (bat on apt):       syntax-highlighted cat with a git gutter
#   - glow   (snap):             Markdown renderer with TUI
#
# These are OPTIONAL: herdr-file-viewer works without them (it falls back to a
# plain pager), but the renderers make it much nicer. Run via `pixi run install`.
# Each is skipped (no-op) when already present, and each uses sudo only when the
# caller is not already root.
set -o errexit
set -o nounset

SUDO=""
if [ "$(id -u)" != "0" ]; then
  SUDO="sudo"
fi

APT_OK=false
if command -v apt-get > /dev/null; then
  APT_OK=true
fi

SNAP_OK=false
if command -v snap > /dev/null; then
  SNAP_OK=true
fi

# --- delta (apt package: git-delta; binary: delta) -----------------------------------------
if command -v delta > /dev/null; then
  echo "delta is already installed."
elif [ "$APT_OK" = true ]; then
  echo "Installing delta (apt package git-delta)"
  $SUDO apt-get install -y git-delta
  echo "delta installed."
else
  echo "delta not found and apt-get is unavailable; install git-delta with your package manager." >&2
fi

# --- bat (apt package: bat; binary: bat) ---------------------------------------------------
if command -v bat > /dev/null; then
  echo "bat is already installed."
elif [ "$APT_OK" = true ]; then
  echo "Installing bat"
  $SUDO apt-get install -y bat
  echo "bat installed."
else
  echo "bat not found and apt-get is unavailable; install bat with your package manager." >&2
fi

# --- glow (snap package: glow; binary: glow) ----------------------------------------------
if command -v glow > /dev/null; then
  echo "glow is already installed."
elif [ "$SNAP_OK" = true ]; then
  echo "Installing glow (snap)"
  $SUDO snap install glow
  echo "glow installed."
else
  echo "glow not found and snap is unavailable; install glow with your package manager (e.g. snap install glow)." >&2
fi
