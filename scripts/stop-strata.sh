#!/bin/bash
set -o errexit
set -o pipefail

# Stop Strata. SIGTERM is what the server expects: it answers the engine with QUIT and
# shuts it down (the pid file start-strata.sh writes holds the server's own pid, since
# setup.py execs into it). A server started from another conda env -- or before the pid
# file existed -- is still found by its command line.

PIDFILE="${CONDA_PREFIX}/strata.pid"
# the app lives at <prefix>/opt/strata/, whichever prefix started it
PATTERN='opt/strata/(serve/server\.py|engine/strata)'

found=0
if [[ -f "${PIDFILE}" ]]; then
    pid="$(cat "${PIDFILE}")"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2> /dev/null; then
        echo "Sending SIGTERM to Strata (pid ${pid})..."
        kill "${pid}" || true
        found=1
    fi
fi

if pkill -f "${PATTERN}"; then
    found=1
fi

if [[ "${found}" -eq 0 ]]; then
    echo "Strata is not running"
    rm -f "${PIDFILE}"
    exit 0
fi

i=0
while [[ $i -lt 100 ]] && pgrep -f "${PATTERN}" > /dev/null; do
    sleep 0.1
    i=$((i + 1))
done

if pgrep -f "${PATTERN}" > /dev/null; then
    echo "Sending SIGKILL"
    pkill -9 -f "${PATTERN}" || true
fi

rm -f "${PIDFILE}"
echo "Strata stopped"
