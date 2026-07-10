#!/bin/bash
# Raise the locked-memory limit (RLIMIT_MEMLOCK / `ulimit -l`) so llama-server
# can mlock model weights into RAM.
#
# With `mlock = true` (see models.ini) llama-server locks the model weights into
# resident memory so the kernel can't page them out. The stock Ubuntu default
# for `ulimit -l` is 8192 KiB (8 MiB) — far too small for multi-GiB models — so
# most of the weights fail to lock and mlock is effectively a no-op.
#
# This installs /etc/security/limits.d/99-memlock.conf granting unlimited locked
# memory to all users. PAM applies limits.d only at login, so the change takes
# effect in new login sessions: log out and back in (or reboot) afterwards.
#
# Run via `pixi run install`. Uses sudo when not running as root: interactive
# password prompt locally, passwordless on GitHub Actions. Idempotent.
set -o errexit
set -o nounset

LIMITS=/etc/security/limits.d/99-memlock.conf
CONTENT="# Installed by pixi-llm-recipes (pixi run install / install-memlock).
# Allow llama-server --mlock to lock multi-GiB model weights into RAM.
*     soft    memlock    unlimited
*     hard    memlock    unlimited
root  soft    memlock    unlimited
root  hard    memlock    unlimited"

if [ -e "$LIMITS" ] && [ "$(cat "$LIMITS")" = "$CONTENT" ]; then
  echo "Locked-memory limit is already configured in $LIMITS."
else
  SUDO=""
  if [ "$(id -u)" != "0" ]; then
    SUDO="sudo"
  fi
  echo "Installing locked-memory limit to $LIMITS"
  printf '%s\n' "$CONTENT" | $SUDO tee "$LIMITS" > /dev/null
  echo "Locked-memory limit installed."
fi

# limits.d is applied by PAM at login; the current shell keeps its old limit.
if [ "$(ulimit -l)" != "unlimited" ]; then
  echo
  echo "NOTE: this shell still has 'ulimit -l' = $(ulimit -l) KiB. Log out and"
  echo "back in (or reboot) for the new unlimited locked-memory limit to apply."
fi
