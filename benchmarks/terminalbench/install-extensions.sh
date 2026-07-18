#!/usr/bin/env bash
# install-extensions.sh — doc 04b (L5). Reproduces the repo's pi extension set,
# reduced to the token-economy KEEP pair {pi-caveman, rtk}, INSIDE the pi agent's
# task container. Baked into the pi-terminal-bench adapter's container build so
# the L5 arm runs pi + exactly these two extensions; every other deployed
# extension is DROP (benchmark validity). Pin versions to match
# pixi-recipes/pi-extensions/recipe.yaml and record them in the ledger.
set -o errexit
set -o nounset

# --- pi-caveman: npm plugin (terse-output token economy) ---
PI_CAVEMAN_VER="${PI_CAVEMAN_VER:-latest}"
pi install "npm:pi-caveman@${PI_CAVEMAN_VER}"

# --- rtk: conda-forge `rtk-cli`, NOT npm (doc 03/04 §KEEP). Inside a container
# without conda, install the prebuilt binary (or a pip/wheel equivalent), then
# initialize the pi integration. Adjust the URL/version to the pinned rtk. ---
RTK_VERSION="${RTK_VERSION:-}"
if command -v rtk >/dev/null 2>&1; then
    echo "rtk already on PATH: $(rtk --version 2>&1)"
elif command -v conda >/dev/null 2>&1; then
    conda install -y -c conda-forge "rtk-cli${RTK_VERSION:+=$RTK_VERSION}"
else
    echo "WARN: rtk (rtk-cli) not installable here — provide a prebuilt binary on PATH."
    echo "      rtk is conda-forge, NOT npm, so 'pi install npm:...' does NOT apply."
fi
command -v rtk >/dev/null 2>&1 && rtk init -g --agent pi || true

# Hard hygiene gate (doc 04): only pi-caveman + rtk may be present; the DROP set
# (pi-web-access, pi-subagents, pi-autoresearch, pi-intercom, pi-btw,
# pi-token-speed, pi-usage-extension, rpiv-ask-user-question, pi-llama-cpp) must
# be absent. Verify from the run transcript.
echo "Installed L5 extensions:"; pi list 2>/dev/null || true
