#!/bin/bash
# Install the AppArmor profile required by bwrap-pi.sh into /etc/apparmor.d/bwrap.
#
# Ubuntu 23.10+ restricts unprivileged user namespaces: bwrap needs an AppArmor
# profile granting `userns` to run without root. This script is a no-op on
# systems without that restriction.
#
# Ubuntu ships a stock /etc/apparmor.d/bwrap covering /usr/bin/bwrap only.
# This script overwrites it with the same stock profile plus a `bwrap-local`
# profile covering the bwrap installed in this project's pixi environments.
# Note: dpkg will report the file as locally modified on apparmor upgrades.
#
# Run via `pixi run install-apparmor`. Uses sudo when not running as root:
# interactive password prompt locally, passwordless on GitHub Actions.
set -o errexit
set -o nounset

RESTRICT=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
if [ ! -e "$RESTRICT" ] || [ "$(cat "$RESTRICT")" != "1" ]; then
  echo "Unprivileged user namespaces are not restricted; no AppArmor profile needed."
  exit 0
fi

# AppArmor attaches profiles by canonical executable path
REPO="$(realpath "$(dirname "$0")/..")"
PROFILE=/etc/apparmor.d/bwrap

CONTENT="abi <abi/4.0>,
include <tunables/global>

profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
profile bwrap-local $REPO/.pixi/envs/*/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}"

if [ -e "$PROFILE" ] && [ "$(cat "$PROFILE")" = "$CONTENT" ]; then
  echo "AppArmor profile $PROFILE is already up to date."
  exit 0
fi

SUDO=""
if [ "$(id -u)" != "0" ]; then
  SUDO="sudo"
fi

echo "Installing AppArmor profile to $PROFILE"
printf '%s\n' "$CONTENT" | $SUDO tee "$PROFILE" > /dev/null

if command -v systemctl > /dev/null && systemctl is-active --quiet apparmor; then
  $SUDO systemctl reload apparmor
else
  $SUDO apparmor_parser -r "$PROFILE"
fi
echo "AppArmor profile loaded."
