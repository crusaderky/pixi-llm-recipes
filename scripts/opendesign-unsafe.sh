#!/bin/bash
# Run OpenDesign's local daemon with full access to the whole host
# (development/debugging only; on Windows this is the only path, because
# bubblewrap does not exist there).
#
# There is no sandbox here: no bubblewrap namespace, no filesystem confinement,
# no GitHub policy, and the host's /run, SSH agents, Docker sockets and GPU
# devices are all reachable. --no-git is refused rather than silently ignored,
# because with full host access no PATH/env guard can hold and starting an
# unrestricted session after the caller asked for a block is worse than not
# starting at all.
#
# Usage: opendesign-unsafe.sh <dir|-> [--bind <dir>] [--ro-bind <dir>]
#                             [--port <n>] [-- od-args...]
#   --bind / --ro-bind are accepted for argument compatibility with the
#   sandboxed launcher and ignored with a warning: nothing is confined, so
#   "bound" is meaningless. --port and forwarded args work as they do there.
set -o errexit
set -o nounset

if [[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]]; then
  # Align bash's ~ with the home dir that node/pi use.
  export HOME="$USERPROFILE"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${CONDA_PREFIX:-}" ]; then
  echo "opendesign-unsafe.sh: run through the designer pixi environment" >&2
  echo "(CONDA_PREFIX is unset; use \`pixi r opendesign-unsafe <dir>\`)" >&2
  exit 2
fi
PREFIX="$CONDA_PREFIX"
APP="$PREFIX/opt/opendesign"
NODE="$PREFIX/bin/node"
OD_MJS="$APP/apps/daemon/bin/od.mjs"
PI_BIN="$PREFIX/bin/pi"
DEFAULT_PORT=7456

if [ "${1:-}" = "-" ]; then
  DIR="$(mktemp -d "${TMPDIR:-/tmp}/opendesign.XXXXXX")"
  trap 'rm -rf "$DIR"' EXIT
  echo "Running in an empty temporary directory: $DIR"
else
  if [ -z "${1:-}" ]; then
    echo "opendesign-unsafe.sh: need a workspace directory (or -)" >&2
    exit 2
  fi
  DIR="$(realpath "$1")"
fi

FWD_ARGS=()
if [ -n "${_FWD_ARGS:-}" ]; then
  while IFS= read -r -d '' arg; do
    FWD_ARGS+=("$arg")
  done < <(printf '%s' "$_FWD_ARGS" | base64 -d)
  unset _FWD_ARGS
elif [ $# -ge 2 ]; then
  FWD_ARGS=("${@:2}")
fi

PORT="$DEFAULT_PORT"
OD_ARGS=()
i=0
while [ $i -lt ${#FWD_ARGS[@]} ]; do
  arg="${FWD_ARGS[$i]}"
  case "$arg" in
    --bind | --ro-bind)
      if [ $((i + 1)) -ge ${#FWD_ARGS[@]} ]; then
        echo "opendesign-unsafe.sh: $arg needs a directory" >&2
        exit 2
      fi
      echo "WARNING: $arg ${FWD_ARGS[$((i + 1))]} ignored: this mode has full host access" >&2
      i=$((i + 2))
      ;;
    --)
      # pixi's argument separator, if this script is called directly.
      i=$((i + 1))
      ;;
    --no-git)
      echo "opendesign-unsafe: --no-git cannot be honoured without the sandbox." >&2
      echo "This mode has full host access and the host's GitHub credentials;" >&2
      echo "use the sandboxed \`pixi r opendesign -- --no-git\` instead." >&2
      exit 2
      ;;
    --port)
      if [ $((i + 1)) -ge ${#FWD_ARGS[@]} ]; then
        echo "opendesign-unsafe.sh: --port needs a number" >&2
        exit 2
      fi
      PORT="${FWD_ARGS[$((i + 1))]}"
      i=$((i + 2))
      ;;
    --port=*)
      PORT="${arg#--port=}"
      i=$((i + 1))
      ;;
    --host | --host=*)
      echo "opendesign-unsafe.sh: --host is not supported; the daemon always binds 127.0.0.1" >&2
      exit 2
      ;;
    *)
      OD_ARGS+=("$arg")
      i=$((i + 1))
      ;;
  esac
done
case "$PORT" in
  '' | *[!0-9]*)
    echo "opendesign-unsafe.sh: --port must be a number, got '$PORT'" >&2
    exit 2
    ;;
esac

# Per-workspace state, the same layout the sandboxed launcher uses.
OD_STATE="$DIR/.od"
mkdir -p "$OD_STATE/installation" "$OD_STATE/agent-home" \
  "$OD_STATE/cache" "$OD_STATE/config" "$OD_STATE/data" "$OD_STATE/state" "$OD_STATE/tmp"

# Sanitize inherited OpenDesign launch settings before applying ours.
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(OD_|OPEN_DESIGN)[^=]+')

# Same Pi setup as the sandboxed launcher (and as pi-unsafe.sh, whose job of
# linking the packaged Pi tree into ~/.pi is not needed here: the host already
# has whatever the normal Pi launcher left behind).
mkdir -p "$HOME/.pi/agent/sessions"
bash "$SCRIPT_DIR/inject-pi-extensions.sh"

echo "======================================================================"
echo "WARNING: OpenDesign is running WITHOUT a sandbox."
echo "  No bubblewrap namespace, no filesystem or Git policy, no --no-git:"
echo "  every agent and shell command has full access to this host, including"
echo "  ~/.ssh, ~/.config/gh, /run and the Docker sockets."
echo "======================================================================"
echo "OpenDesign on http://127.0.0.1:$PORT (Ctrl-C to stop)"
echo "  workspace  $DIR"
echo "  state      $OD_STATE"

while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

cd "$DIR"
export OD_BIN="$OD_MJS"
export OD_NODE_BIN="$NODE"
export OD_INSTALLATION_DIR="$OD_STATE/installation"
export OD_RESOURCE_ROOT="$APP/resources"
export OD_DATA_DIR="$OD_STATE"
export OD_AGENT_HOME="$OD_STATE/agent-home"
export OD_BIND_HOST=127.0.0.1
export OD_PORT="$PORT"
export NODE_ENV=production
export PI_BIN="$PI_BIN"
export PI_CODING_AGENT_DIR="$HOME/.pi/agent"
export XDG_CACHE_HOME="$OD_STATE/cache"
export XDG_CONFIG_HOME="$OD_STATE/config"
export XDG_DATA_HOME="$OD_STATE/data"
export XDG_STATE_HOME="$OD_STATE/state"
export TMPDIR="$OD_STATE/tmp"
unset OD_SANDBOX_MODE

exec "$NODE" "$OD_MJS" daemon start --serve-web --no-open --port "$PORT" "${OD_ARGS[@]}"
