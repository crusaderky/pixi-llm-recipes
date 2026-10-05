#!/bin/bash
set -o errexit
set -o pipefail

# Start Strata (the Qwen3.8-Flash-Next build in the `strata` feature) in the background,
# appending its output to strata.log in the current directory. Everything else it writes
# stays inside $CONDA_PREFIX: the app and the engine in $CONDA_PREFIX/opt/strata, the
# expert pack, the MTP draft layer and the run config in $CONDA_PREFIX/strata-data.
#
# The first start downloads the model shards into the shared Hugging Face cache
# (~/.cache/huggingface/hub, the same blobs `llama-server -hf` uses -- the Coder's are
# already there), takes ~5 GB of MTP tensors out of the Qwen checkpoint and prepares the
# pack. That is a couple of minutes, once; later starts only load the model (~40 s).
#
# Strata listens on 8082 by default: llama-server owns 8080 and the forge layout gives its own
# llama-server backend 8081, so none of the three collides. A port someone else owns is
# refused rather than taken over.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# STRATA_PORT overrides it for both halves: the health check below and setup.py
PORT="${STRATA_PORT:-8082}"
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
    echo "Port ${PORT} is in use by another server (llama-server on 8080, the forge backend on 8081, another Strata)." >&2
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
