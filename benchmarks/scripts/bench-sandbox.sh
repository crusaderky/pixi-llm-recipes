#!/usr/bin/env bash
# bench-sandbox.sh — untrusted-code execution sandbox for the lightweight
# benchmark panel (docs/benchmarks/05-common-lightweight.md, deliverable D2).
#
# Wraps `bwrap` (bubblewrap) to run model-generated code in an isolated
# namespace with NO network and a single writable area, so grading
# (LiveCodeBench, EvalPlus, CRUXEval-I) can never reach out or mutate the host.
# Reuses the same AppArmor profile as bwrap-pi.sh (no new profile); install it
# once with `pixi run install-apparmor`.
#
# Contract (doc 05 §D2):
#   - read-only root (entire host visible read-only, incl. the harness venv +
#     repo, so the graded command can read inputs by their host path);
#   - /tmp is the ONLY writable area and the working directory — a fresh tmpfs
#     by default, or the --stage dir bind-mounted over /tmp so the harness can
#     stage inputs before and read results back after;
#   - no network (--unshare-all, no --share-net);
#   - optional wall-time / virtual-memory / CPU-time caps;
#   - --die-with-parent; non-zero exit propagates as a test failure.
#
# The writable workspace is /tmp (not /sandbox): /tmp always exists on the host
# so bwrap can mount over it without creating a mountpoint on the read-only
# root. Harnesses use /tmp/<file> as the in-sandbox path.
#
# Usage:
#   bench-sandbox.sh [--stage <dir>] [--time <s>] [--mem <KB>] [--cpu <s>] \
#                    [--tmpfs-size <bytes>] -- <cmd...>
#
#   --stage <dir>      Bind-mount host <dir> read-write over /tmp. The harness
#                      stages snippet + test inputs into <dir> BEFORE calling,
#                      and reads result files back out of <dir> AFTER. Inside
#                      the sandbox the workspace path is /tmp. Mutually
#                      exclusive with the default fresh tmpfs.
#   --time <s>         Wall-clock timeout (seconds); kills the sandboxed cmd
#                      after <s>s (exit 124 on timeout — a non-zero failure).
#   --mem <KB>         Virtual-memory cap (ulimit -v), in kilobytes.
#   --cpu <s>          CPU-time cap (ulimit -t), in seconds.
#   --tmpfs-size <bytes>  Size of the default /tmp tmpfs (default 1 GiB).
#                      Ignored if --stage is given.
#   --                 Separates options from the command to run (optional if
#                      no options precede the command).
#
# Examples:
#   # known-good snippet (acceptance: exit 0)
#   bench-sandbox.sh -- python -c 'print(2+2)'
#
#   # socket snippet (acceptance: non-zero/blocked)
#   bench-sandbox.sh -- python -c 'import socket; socket.create_connection(("1.1.1.1",80),5).close()'
#
#   # staged grading: harness writes inputs to ./stage first
#   mkdir -p ./stage && cp solution.py tests.py ./stage/
#   bench-sandbox.sh --stage "$PWD/stage" --time 60 --mem 2097152 -- \
#     python -m evalplus.evaluate --dataset humaneval --samples /tmp/solution.py
#
# Linux only (bubblewrap). Not ported to Windows — the lightweight panel's
# exec sandbox is a Linux-only deliverable (doc 05).
set -o errexit
set -o nounset

PROG=${0##*/}

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

STAGE=""
TIME=""
MEM=""
CPU=""
TMPFS_SIZE=$((1024 * 1024 * 1024)) # 1 GiB default
SEEN_DASH_DASH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage)
            [[ $# -ge 2 ]] || { echo "$PROG: $1 needs a value" >&2; exit 2; }
            STAGE=$2; shift 2 ;;
        --time)
            [[ $# -ge 2 ]] || { echo "$PROG: $1 needs a value" >&2; exit 2; }
            TIME=$2; shift 2 ;;
        --mem)
            [[ $# -ge 2 ]] || { echo "$PROG: $1 needs a value" >&2; exit 2; }
            MEM=$2; shift 2 ;;
        --cpu)
            [[ $# -ge 2 ]] || { echo "$PROG: $1 needs a value" >&2; exit 2; }
            CPU=$2; shift 2 ;;
        --tmpfs-size)
            [[ $# -ge 2 ]] || { echo "$PROG: $1 needs a value" >&2; exit 2; }
            TMPFS_SIZE=$2; shift 2 ;;
        --help|-h)
            usage ;;
        --)
            shift; SEEN_DASH_DASH=1; break ;;
        *)
            echo "$PROG: unknown option $1 (use -- before the command)" >&2
            exit 2 ;;
    esac
done

# After the loop, "$@" is either the command (if -- seen) or the remaining args.
if [[ $SEEN_DASH_DASH -eq 0 && $# -gt 0 ]]; then
    # No options, no -- : treat all args as the command.
    :
fi
CMD=("$@")

if [[ ${#CMD[@]} -eq 0 ]]; then
    echo "$PROG: no command given (use -- <cmd...>)" >&2
    exit 2
fi

# Resolve bwrap: prefer the pixi env's, fall back to PATH.
BWRAP=""
for c in "${BWRAP:-}" "${PIXI_BWRAP:-}" "${CONDA_PREFIX:-$PWD/.pixi/envs/agents}/bin/bwrap" "$(command -v bwrap 2>/dev/null)"; do
    [[ -x "$c" ]] && BWRAP=$c && break
done
if [[ -z "$BWRAP" ]]; then
    echo "$PROG: bwrap not found (install bubblewrap or set PIXI_BWRAP)" >&2
    exit 2
fi

# /tmp is the writable workspace: a fresh tmpfs by default, or the --stage dir
# bind-mounted over /tmp. Both mount over an existing host path so bwrap does
# not need to create a mountpoint on the read-only root.
SANDBOX_ARGS=()
if [[ -n "$STAGE" ]]; then
    STAGE_ABS=$(cd "$STAGE" && pwd -P)
    SANDBOX_ARGS+=(--bind "$STAGE_ABS" /tmp)
else
    SANDBOX_ARGS+=(--size "$TMPFS_SIZE" --tmpfs /tmp)
fi
SANDBOX_ARGS+=(--chdir /tmp)

# Inner wrapper applies ulimits INSIDE the namespace (so they bind the graded
# process, not bwrap itself), then execs the real command. Env vars are
# inherited by bwrap by default (no --clearenv).
export BENCH_MEM_KB=$MEM
export BENCH_CPU_S=$CPU
INNER='[ -n "$BENCH_MEM_KB" ] && ulimit -v "$BENCH_MEM_KB" 2>/dev/null || true
[ -n "$BENCH_CPU_S" ] && ulimit -t "$BENCH_CPU_S" 2>/dev/null || true
exec "$@"'

# Wall-time timeout wraps the whole bwrap call (external, so a hung sandbox is
# killed regardless of inner state). `timeout` exit 124 = timed out (non-zero).
WRAP=()
if [[ -n "$TIME" ]]; then
    if command -v timeout >/dev/null 2>&1; then
        WRAP=(timeout --signal=TERM --kill-after=5 "$TIME")
    else
        echo "$PROG: --time given but coreutils 'timeout' not found; ignoring" >&2
    fi
fi

# Run: read-only root (host visible ro, incl. venv + repo), /dev + /proc for the
# process, /tmp the only writable area (working dir), NO --share-net.
# --tmpfs /dev/shm: `--dev /dev` mounts a minimal devtmpfs WITHOUT /dev/shm, but
# graded code often uses Python multiprocessing / ProcessPoolExecutor (evalplus,
# livecodebench, cruxeval), which needs POSIX shared memory there. A private
# tmpfs keeps it isolated (no host access).
exec "${WRAP[@]}" "$BWRAP" \
    --ro-bind / / \
    --dev /dev \
    --tmpfs /dev/shm \
    --proc /proc \
    "${SANDBOX_ARGS[@]}" \
    --die-with-parent \
    --unshare-all \
    -- bash -c "$INNER" bash "${CMD[@]}"
