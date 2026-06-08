#!/bin/bash
if nc -z localhost 8080; then
    echo "llama-server is already running on port 8080."
    exit 0
fi

# Note: Don't use --log-file; it hides a bunch of information
llama-server --models-preset models.ini > llama-server.log 2>&1 &

echo "Logging to llama-server.log"
echo "Waiting for server to start..."
until nc -z localhost 8080; do
    sleep 0.1
done
