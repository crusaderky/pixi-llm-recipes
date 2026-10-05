#!/bin/bash
# Stop systemd-oomd from killing the whole login session when a big model is loading.
#
# systemd ships the login session itself as an oomd kill candidate:
# /usr/lib/systemd/system/user@.service.d/10-oomd-user-service-defaults.conf sets
#
#   [Service]
#   ManagedOOMMemoryPressure=kill
#   ManagedOOMMemoryPressureLimit=50%
#
# and per systemd-oomd.service(8) that means: once the memory pressure of the whole
# user@<uid>.service subtree stays above 50% for the configured duration (20-30s)
# *with reclaim activity*, oomd sends SIGKILL to every process in one of the cgroups
# below it. The unit with the property set is not a candidate, but init.scope -- where
# systemd --user runs -- is a leaf, so it is. Killing it takes every unit in the
# session down with it: gnome-shell, the X server (which exits cleanly, exit code 0,
# so it looks like a crash on screen), the terminal, and the model that was loading.
#
# What trips it is a model, not a leak. Strata loads ~30 GB into RAM on top of the
# ~55 GB of GGUF the page cache holds -- page cache is charged to the cgroup that read
# it and stays charged after that process exits -- so a couple of starts are enough to
# hold the session above the limit while the kernel keeps reclaiming. None of that is
# VRAM: no --vram-reserve-mib, quant or context setting avoids it.
#
# This writes a drop-in that takes the session out of oomd's hands:
#
#   [Service]
#   ManagedOOMMemoryPressure=auto
#
# "auto" means systemd-oomd does not monitor or choose that unit (systemd.resource-
# control(5)); the kernel's own OOM killer stays the backstop, and it kills a process
# instead of the session. Undo with:
#
#   sudo rm /etc/systemd/system/user@.service.d/90-oomd-user-session.conf
#   sudo systemctl daemon-reload
#
# No-op where systemd-oomd is not installed. Run via `pixi run install-oomd` (or
# `pixi r install`). Uses sudo when not running as root. Idempotent.
set -o errexit
set -o nounset

DROPIN=/etc/systemd/system/user@.service.d/90-oomd-user-session.conf
CONTENT="# Installed by pixi-llm-recipes (pixi run install / install-oomd).
# Keep the login session out of systemd-oomd's reach: a ~30 GB model in RAM plus the
# page cache of the GGUF it read trips the session's 50% memory-pressure limit, and
# the cgroup oomd kills for that can be init.scope -- which takes the X server and
# the whole desktop with it.
[Service]
ManagedOOMMemoryPressure=auto"

if ! command -v systemctl > /dev/null || [ ! -d /run/systemd/system ]; then
  echo "Not a systemd host; nothing to configure."
  exit 0
fi
if ! systemctl cat systemd-oomd.service > /dev/null 2>&1; then
  echo "systemd-oomd is not installed; nothing to configure."
  exit 0
fi

SUDO=""
if [ "$(id -u)" != "0" ]; then
  SUDO="sudo"
fi

if [ -e "$DROPIN" ] && [ "$(cat "$DROPIN")" = "$CONTENT" ]; then
  echo "Session memory-pressure policy is already configured in $DROPIN."
else
  echo "Installing $DROPIN (the login session stops being an oomd kill candidate)"
  $SUDO mkdir -p "$(dirname "$DROPIN")"
  printf '%s\n' "$CONTENT" | $SUDO tee "$DROPIN" > /dev/null
  $SUDO systemctl daemon-reload
  echo "Installed."
fi

# Verify: a later-sorting drop-in, or a kill policy on an ancestor slice, would still
# arm oomd against the session (an ancestor's `kill` makes descendants candidates
# whatever their own property says).
SESSION="user@$(id -u).service"
EFFECTIVE="$($SUDO systemctl show "$SESSION" -p ManagedOOMMemoryPressure --value)"
ARMED=false
if [ "$EFFECTIVE" = kill ]; then
  echo "WARNING: $SESSION still reports ManagedOOMMemoryPressure=kill." >&2
  echo "         Check the other drop-ins: $SUDO systemctl cat $SESSION" >&2
  ARMED=true
fi
for unit in user.slice "user-$(id -u).slice" -.slice; do
  if [ "$($SUDO systemctl show "$unit" -p ManagedOOMMemoryPressure --value 2> /dev/null || true)" = kill ]; then
    echo "WARNING: $unit is also an oomd kill candidate; a session on 'auto' can still be killed under it." >&2
    ARMED=true
  fi
done
if [ "$ARMED" = true ]; then
  echo "The login session is still reachable by systemd-oomd; see the warnings above." >&2
  exit 1
fi
echo "The login session is no longer a systemd-oomd kill candidate (effective: ${EFFECTIVE:-unknown})."
