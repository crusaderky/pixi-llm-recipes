#!/usr/bin/env bash
# Bound a CI step that runs pi, and print post-mortem diagnostics when it hangs.
#
# pi in print mode can finish its turn, write the answer, and then never exit:
# an extension (or pi itself) keeps the Node event loop alive, so the process
# sleeps at 0% CPU with the answer already on stdout. Upstream reports are
# earendil-works/pi#2677, #2584, #3886, #5944, #7734 — the last one names a
# spawned subagent as the trigger, and every one of them ends the same way for
# CI: the step burns the runner's full 6 h limit before Actions cancels it.
#
# This wrapper therefore refuses to wait forever. It runs the command with a
# wall-clock deadline, and when the deadline passes it dumps what the process
# tree was doing (so the log says what was blocking, not just "cancelled") and
# kills it.
#
# Usage: ci-run-pi.sh <deadline-seconds> <command> [args...]
# Exit: the command's own status, or 124 when the deadline fired.

set -o nounset

# No `t` column and no `timeout(1)`: neither is guaranteed on the three
# platforms this project targets (Git Bash ships neither unless m2-coreutils is
# in the env), while a sleep/kill loop only needs bash.
DEADLINE="${1:?usage: ci-run-pi.sh <deadline-seconds> <command ...>}"
shift

dump_diagnostics() {
    local pid="$1"

    echo "ci-run-pi: process tree at deadline (truncated to 200 columns):" >&2
    # `wchan` names the kernel function each process is blocked in; `stat`
    # distinguishes a true wait (S) from a spin (R). Deliberately no -u/-x:
    # the ps builds on the three platforms differ, and these columns are what
    # identifies a wedged process. Truncated because a runner's own init line
    # (bwrap, docker-exec) is megabytes of bind mounts.
    ps -eo pid,ppid,stat,etimes,wchan:24,comm,args --no-headers 2>&1 | head -60 | cut -c -200 >&2

    if [ -r "/proc/${pid}/status" ]; then
        echo "ci-run-pi: /proc/${pid}/status (State/Threads only):" >&2
        grep -E '^(State|Threads|PPid):' "/proc/${pid}/status" >&2
        echo "ci-run-pi: /proc/${pid}/task/*/wchan:" >&2
        for t in /proc/"${pid}"/task/*; do
            printf '  %s %s %s\n' "${t##*/}" "$(cat "${t}/comm" 2>/dev/null)" \
                "$(cat "${t}/wchan" 2>/dev/null)" >&2
        done
        echo "ci-run-pi: /proc/${pid}/fd:" >&2
        # ls, not find: the fd listing is only interesting for what each descriptor
        # *points at* (socket, pipe, file), which ls prints and find does not. The
        # names are the descriptor numbers, so SC2012's filename warning cannot bite.
        # shellcheck disable=SC2012
        ls -l "/proc/${pid}/fd" 2>&1 | head -40 >&2
    fi

    if command -v ss > /dev/null 2>&1; then
        # Only the connected ones: a listener tells you nothing about what a
        # wedged client is waiting for, and the listening set on a runner is
        # long enough to bury the interesting line.
        echo "ci-run-pi: connected sockets:" >&2
        ss -tanp 2>&1 | grep -Ev '^(State|LISTEN)' | head -30 >&2
    fi
}

"$@" &
CHILD=$!

elapsed=0
while kill -0 "$CHILD" 2>/dev/null; do
    if [ "$elapsed" -ge "$DEADLINE" ]; then
        echo "ci-run-pi: '$*' was still running after ${DEADLINE}s; it never exited on its own." >&2
        dump_diagnostics "$CHILD"
        # TERM first: an extension that merely forgot to unref a handle still
        # dies on it, and pi's own signal handler gets to flush the session.
        kill -TERM "$CHILD" 2>/dev/null
        sleep 10
        if kill -0 "$CHILD" 2>/dev/null; then
            echo "ci-run-pi: still alive after SIGTERM; sending SIGKILL." >&2
            kill -KILL "$CHILD" 2>/dev/null
            sleep 2
        fi
        exit 124
    fi
    sleep 5
    elapsed=$((elapsed + 5))
done

wait "$CHILD"
# The deadline path above exits 124 itself, so anything reaching here ended on
# its own — including a signal from whoever cancelled the job.
exit "$?"
