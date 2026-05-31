#!/usr/bin/env bash
set -euo pipefail

export HOME="${PREFIX}/home"
rm -rf "${PREFIX}/home/.pi/agent/npm"
rm -f "${PREFIX}/home/.pi/agent/settings.json"

pi install npm:pi-autoresearch@1.4.0
pi install npm:pi-btw@0.4.0
pi install npm:pi-token-speed@0.2.1
pi install npm:@juicesharp/rpiv-web-tools@1.16.1
pi install npm:@tmustier/pi-usage-extension@0.3.2
