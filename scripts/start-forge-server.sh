#!/bin/bash
set -o errexit
set -o nounset

# Start forge-proxy on port 8080, forwarding to the llama-server that the
# start-server dependency task started on port 8081.
# Forge applies guardrails (response validation, rescue parsing, retry loops)
# transparently — clients talk to 8080 as if it were a smarter model.
PORT=8080
BACKEND_PORT=8081

# Test if something is listening on the port; any HTTP response (even an
# error) means the server is up. Don't use nc, which doesn't exist on Windows.
server_is_up() {
    curl -s -o /dev/null "http://localhost:${PORT}/health"
}

if server_is_up; then
    echo "forge-proxy is already running on port ${PORT}."
    exit 0
fi

python -m forge.proxy --backend-url "http://localhost:${BACKEND_PORT}" --port "${PORT}" > forge-proxy.log 2>&1 &

echo "Logging to forge-proxy.log"
echo "Waiting for forge-proxy to start on port ${PORT}..."
for _ in $(seq 1 100); do
    if server_is_up; then
        echo "forge-proxy is up on port ${PORT} → llama-server on port ${BACKEND_PORT}."
        exit 0
    fi
    sleep 0.1
done

echo "forge-proxy failed to start; see forge-proxy.log" >&2
exit 1
