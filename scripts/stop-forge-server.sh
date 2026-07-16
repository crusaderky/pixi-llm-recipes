#!/bin/bash
set -o errexit
set -o pipefail

if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
    # Windows (git-bash): no pkill/pgrep, and forge-proxy is a python.exe, so
    # taskkill by image name would kill unrelated python processes; match the
    # command line instead.
    if powershell -NoProfile -Command '$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "forge.proxy" }; if (-not $p) { exit 1 }; $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }'; then
        echo "Killed all forge-proxy processes."
    else
        echo "forge-proxy is not running"
    fi
    exit 0
fi

if pkill -f forge.proxy; then
    echo "Sending SIGTERM to all forge-proxy processes..."
else
    echo "forge-proxy is not running"
    exit 0
fi

i=0
while [ $i -lt 50 ] && pgrep -f forge.proxy >/dev/null; do
    sleep 0.1
    i=$((i+1))
done

if [ $i -eq 50 ]; then
    echo "Sending SIGKILL"
    pkill -9 -f forge.proxy || true
fi
