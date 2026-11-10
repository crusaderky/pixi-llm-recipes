#!/usr/bin/env bash
set -euo pipefail

export HOME="${PREFIX}/home"
rm -rf "${PREFIX}/home/.pi/agent/npm"
rm -f "${PREFIX}/home/.pi/agent/settings.json"

# PLUGINS is set in recipe.yaml
for plugin in ${PLUGINS}; do
    pi install "npm:${plugin}"
done

# pi-llama-cpp never disconnects the llama-server SSE stream it opens to follow
# a model load, and that stream reconnects forever, so `pi -p` prints its answer
# and then never exits (upstream gsanhueza/pi-llama-cpp#40). Close it on
# session_shutdown. Hard error by design: if a plugin bump makes the patch stop
# applying, the package build fails instead of shipping a plugin that wedges
# every `pi -p` step in .github/workflows/pi.yml.
# The cwd is the directory the recipe files were copied into (same assumption
# pi-home/build.sh makes with `cp -a skills`); the check turns a wrong
# assumption into a message rather than a "node: module not found".
PATCH=patches/pi-llama-cpp-sse-shutdown.cjs
if [ ! -f "${PATCH}" ]; then
    echo "build.sh: ${PATCH} not found relative to $(pwd)" >&2
    exit 1
fi
node "${PATCH}" "${PREFIX}/home/.pi/agent/npm/node_modules/pi-llama-cpp"

npm approve-scripts --allow-scripts-pending

# Install rtk integration for pi
rtk init -g --agent pi --auto-patch
