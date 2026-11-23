#!/bin/bash
set -o errexit
set -o pipefail

# Start Strata in the background, appending its output to strata.log in the current
# directory. The first argument names one profile from strata.ini (a section or an alias);
# only that model starts. The app, engine and run configs stay in $CONDA_PREFIX/opt/strata;
# the expert pack, MTP draft layer and preparation records live in ~/.cache/strata-data, so
# they survive a recreated env.
#
# What it runs with is that profile of strata.ini; `pixi run strata-help <profile>` prints
# every parameter Strata takes and what that profile says right now.
#
# The first start of a profile downloads the model shards into the shared Hugging Face cache
# (~/.cache/huggingface/hub, the same blobs `llama-server -hf` uses), takes ~5 GB of MTP
# tensors out of the Qwen checkpoint and prepares the pack. That is a couple of minutes, once;
# later starts only load the model (~40 s) -- unless strata.ini changed something setup.py
# only reads when preparing, which re-prepares once (see scripts/strata-run.py).
#
# Strata listens on the port strata.ini says (`port`): llama-server owns 9931, so these
# do not collide. A port someone else owns is refused rather than taken over.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=""
PROFILE="${STRATA_PROFILE:-}"
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
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --profile=*)
            PROFILE="${1#*=}"
            shift
            ;;
        *)
            if [[ -z "${PROFILE}" && "$1" != --* ]]; then
                PROFILE="$1"
            else
                ARGS+=("$1")
            fi
            shift
            ;;
    esac
done
export STRATA_PROFILE="${PROFILE}"

# --port here, then STRATA_PORT, then the profile -- the same order scripts/strata-run.py
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
    echo "Port ${PORT} is in use by another server (llama-server, another Strata)." >&2
    echo "Stop it first (pixi run stop-llamacpp), then start Strata again." >&2
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
