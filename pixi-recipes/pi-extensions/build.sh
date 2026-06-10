#!/usr/bin/env bash
set -euo pipefail

export HOME="${PREFIX}/home"
rm -rf "${PREFIX}/home/.pi/agent/npm"
rm -f "${PREFIX}/home/.pi/agent/settings.json"

pi install npm:pi-autoresearch@1.6.0
pi install npm:pi-btw@0.4.1
pi install npm:pi-llama-cpp@0.6.0
pi install npm:pi-ollama-cloud@0.6.0
pi install npm:pi-token-speed@0.3.1
pi install npm:@juicesharp/rpiv-advisor@1.19.1
pi install npm:@juicesharp/rpiv-ask-user-question@1.19.1
# pi install npm:@juicesharp/rpiv-web-tools@1.19.1  # Redundant with pi-ollama-cloud
pi install npm:@tmustier/pi-usage-extension@0.3.2
