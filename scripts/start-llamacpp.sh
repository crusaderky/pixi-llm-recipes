#!/bin/bash
set -o errexit
set -o nounset

# Parse --port (default 8080) and --host-ram (default: off, no cap);
# everything else is passed verbatim to llama-server.
PORT=8080
HOST_RAM=
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
        --host-ram)
            HOST_RAM="$2"
            shift 2
            ;;
        --host-ram=*)
            HOST_RAM="${1#*=}"
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

# --host-ram simulates a host with less RAM than this one actually has.
#
# llama-server mmaps the model weights, so with `load-mode = mmap` the resident
# footprint of a model larger than RAM is whatever the OS keeps in the page
# cache; the rest is faulted back in from disk on demand. There is no knob for
# "give this process only N GiB of page cache" -- RLIMIT_RSS is unenforced and
# RLIMIT_AS caps address space, not residency -- so the cap is applied with the
# one mechanism that does account page cache: a cgroup v2 memory limit, via a
# transient systemd user scope. Both anonymous memory and the mmap'd model
# pages faulted in by the server are charged to that scope, and the kernel
# reclaims (evicts) the mmap'd pages once the cap is reached. Swap is disabled
# for the scope, so the simulated host is one with N of RAM and no swap.
#
# Two caveats, both of which make the cap under- or over-bite rather than
# silently do nothing:
#
#  - Page cache is charged to whichever cgroup *first* faulted a page in, and
#    is not re-charged afterwards. Weights left in the page cache by an earlier
#    run are therefore free to this one. Start from a cold cache
#    (`sudo sysctl -w vm.drop_caches=3`) for a faithful measurement.
#  - mlock'd weights (`load-mode = mlock` in models.ini) and CUDA pinned host
#    buffers are unreclaimable: if they don't fit under the cap, the kernel
#    OOM-kills llama-server instead of paging. Simulate low RAM against
#    `load-mode = mmap`.
LAUNCH_PREFIX=()
if [[ -n "$HOST_RAM" ]]; then
    if ! command -v systemd-run > /dev/null 2>&1; then
        echo "--host-ram needs systemd-run (cgroup v2 memory limits); not available here." >&2
        exit 1
    fi
    # One probe for the three things that can go wrong: no systemd user manager,
    # no memory controller delegated to the user slice, bad size syntax.
    if ! systemd-run --user --scope --collect -q \
        -p "MemoryMax=${HOST_RAM}" -p MemorySwapMax=0 -- true > /dev/null 2>&1; then
        echo "--host-ram=${HOST_RAM} was rejected by systemd. It needs a running systemd" >&2
        echo "user manager with the memory controller delegated to the user slice, and a" >&2
        echo "size systemd understands (bytes, or a K/M/G/T suffix, or a % of total RAM)." >&2
        exit 1
    fi
    LAUNCH_PREFIX=(
        systemd-run --user --scope --collect -q
        --unit "llama-server-${PORT}"
        -p "MemoryMax=${HOST_RAM}" -p MemorySwapMax=0 --
    )
    echo "Simulating a ${HOST_RAM} host: llama-server runs in llama-server-${PORT}.scope"
    echo "(MemoryMax=${HOST_RAM}, swap disabled). Inspect it with:"
    echo "  systemctl --user status llama-server-${PORT}.scope"
fi

# Note: Don't use --log-file; it hides a bunch of information
"${LAUNCH_PREFIX[@]}" llama-server --models-preset models.ini --models-max 1 --port "${PORT}" "${LLAMA_EXTRA_ARGS[@]}" > llama-server.log 2>&1 &
SERVER_PID=$!

echo "Logging to llama-server.log"
echo "Waiting for server to start on port ${PORT}..."
until server_is_up; do
    if ! kill -0 "${SERVER_PID}" 2> /dev/null; then
        echo "llama-server exited before it came up; see llama-server.log" >&2
        if [[ -n "$HOST_RAM" ]]; then
            echo "With --host-ram set it may have been OOM-killed by the cgroup; check" >&2
            echo "  journalctl --user -u llama-server-${PORT}.scope" >&2
        fi
        exit 1
    fi
    sleep 0.1
done
