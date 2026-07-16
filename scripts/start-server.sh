#!/bin/bash
set -o errexit
set -o nounset

# Parse --port flag (default 8080); everything else is passed verbatim to llama-server.
PORT=8080
LLAMA_EXTRA_ARGS=()
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
            LLAMA_EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# Test if something is listening on the port; any HTTP response (even an
# error) means the server is up. Don't use nc, which doesn't exist on Windows.
server_is_up() {
    curl -s -o /dev/null "http://localhost:${PORT}/health"
}

if server_is_up; then
    echo "llama-server is already running on port ${PORT}."
    exit 0
fi

# Note: Don't use --log-file; it hides a bunch of information
llama-server --models-preset models.ini --models-max 1 --port "${PORT}" "${LLAMA_EXTRA_ARGS[@]}" > llama-server.log 2>&1 &

echo "Logging to llama-server.log"
echo "Waiting for server to start on port ${PORT}..."
until server_is_up; do
    sleep 0.1
done
