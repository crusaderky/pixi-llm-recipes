#!/bin/bash
set -o errexit
set -o pipefail

# Start Strata (the model strata.ini names) in the background, appending its output to
# strata.log in the current directory. Everything else it writes stays inside $CONDA_PREFIX:
# the app and the engine in $CONDA_PREFIX/opt/strata, the expert pack, the MTP draft layer
# and the run config in $CONDA_PREFIX/strata-data.
#
# What it runs with is strata.ini in this repo's root; `pixi run strata-help` prints every
# parameter Strata takes and what that file says right now.
#
# The first start downloads the model shards into the shared Hugging Face cache
# (~/.cache/huggingface/hub, the same blobs `llama-server -hf` uses), takes ~5 GB of MTP
# tensors out of the Qwen checkpoint and prepares the pack. That is a couple of minutes, once;
# later starts only load the model (~40 s) -- unless strata.ini changed something setup.py
# only reads when preparing, which re-prepares once (see scripts/strata-run.py).
#
# Strata listens on the port strata.ini says (`port`): llama-server owns 8080 and the forge
# layout gives its own llama-server backend 8081, so none of the three collides. A port
# someone else owns is refused rather than taken over.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        --port=*)
            PORT="${1#*=}"
            shift
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

# --port here, then STRATA_PORT, then strata.ini -- the same order scripts/strata-run.py
# applies. The wrapper is asked rather than a number written here, so the health check below
# polls the port Strata is about to listen on.
if [[ -z "${PORT}" ]]; then
    PORT="${STRATA_PORT:-$(python "${SCRIPT_DIR}/strata-run.py" --print port 2> /dev/null || true)}"
fi
if [[ -z "${PORT}" ]]; then
    echo "No port to start on: set one in strata.ini (port = ...) or pass --port <n>." >&2
    exit 1
fi

LOG="$PWD/strata.log"
PIDFILE="${CONDA_PREFIX}/strata.pid"

# Any HTTP response means something is listening; only Strata's own /health carries the
# "loaded" flag llama-server's does not.
server_is_up() {
    curl -s -o /dev/null "http://localhost:${PORT}/health"
}

if server_is_up; then
    if curl -s "http://localhost:${PORT}/health" | grep -q '"loaded"'; then
        echo "Strata is already running on port ${PORT}."
        exit 0
    fi
    echo "Port ${PORT} is in use by another server (llama-server, the forge backend, another Strata)." >&2
    echo "Stop it first (pixi run stop-server), then start Strata again." >&2
    exit 1
fi

echo "Logging to ${LOG}"
nohup python "${SCRIPT_DIR}/strata-run.py" --port "${PORT}" "${ARGS[@]}" >> "${LOG}" 2>&1 &
SERVER_PID=$!
echo "${SERVER_PID}" > "${PIDFILE}"

echo "Waiting for Strata on port ${PORT}..."
until server_is_up; do
    if ! kill -0 "${SERVER_PID}" 2> /dev/null; then
        echo "Strata exited before it came up; see ${LOG}" >&2
        exit 1
    fi
    sleep 1
done

echo "Strata is ready: http://127.0.0.1:${PORT}/  (OpenAI API: http://127.0.0.1:${PORT}/v1)"
