#!/bin/bash
set -o errexit
set -o nounset

# Test if something is listening on the port; any HTTP response (even an
# error) means the server is up. Don't use nc, which doesn't exist on Windows.
server_is_up() {
    curl -s -o /dev/null http://localhost:8080/health
}

if server_is_up; then
    echo "llama-server is already running on port 8080."
    exit 0
fi

# Note: Don't use --log-file; it hides a bunch of information
llama-server --models-preset models.ini > llama-server.log 2>&1 &

echo "Logging to llama-server.log"
echo "Waiting for server to start..."
until server_is_up; do
    sleep 0.1
done
