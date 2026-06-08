#!/bin/bash
set -o errexit
set -o pipefail

if pkill -f llama-server; then
    echo "Sending SIGTERM to all llama-server processes..."
else
    echo "llama-server is not running"
    exit 0
fi

i=0
while [ $i -lt 50 ] && pgrep -f llama-server >/dev/null; do
    sleep 0.1
    i=$((i+1))
done

if [ $i -eq 50 ]; then
    echo "Sending SIGKILL"
    pkill -9 -f llama-server || true
fi
