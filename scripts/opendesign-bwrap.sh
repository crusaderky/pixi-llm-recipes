#!/bin/bash
# Run OpenDesign's local daemon — and every agent and shell it spawns — inside
# one bubblewrap namespace, with read/write access to the workspace and nowhere
# else. Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
#
# Usage: opendesign-bwrap.sh <dir|-> [--no-git] [--bind <dir>] [--ro-bind <dir>]
#                            [--port <n>] [-- od-args...]
#   --bind <dir>   exposes the directory read-write; --ro-bind <dir> the same
#                  directory read-only. Both take a host path and are applied as
#                  both the source and the destination, so the sandbox path
#                  matches the host path. As in bwrap-pi.sh, the workspace bind
#                  is applied last and wins inside itself — a read-only bind
#                  pointing at or inside the workspace comes out writable, and
#                  that is warned about.
#   --port <n>     daemon port, default 7456. The daemon always binds
#                  127.0.0.1: there is deliberately no LAN option, and an
#                  argument that tries to change the host (`--host`) is refused.
#   --no-git       bind no GitHub/Git credential at all, exactly as the Pi
#                  launcher does; see the block below.
#   Everything else after `--` (or after the workspace when _FWD_ARGS is absent)
#   is appended to `daemon start --serve-web --no-open`. Forwarded args are read
#   from the _FWD_ARGS env var (base64-encoded, null-separated, set by
#   scripts/install/opendesign) or, as a fallback, from positional args $2
#   onward, for direct `pixi r opendesign -- <args>` invocations.
set -o errexit
set -o nounset

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "${CONDA_PREFIX:-}" ]; then
  echo "opendesign-bwrap.sh: run through the design pixi environment" >&2
  echo "(CONDA_PREFIX is unset; use \`pixi r opendesign <dir>\` or the wrapper)" >&2
  exit 2
fi
PREFIX="$CONDA_PREFIX"
APP="$PREFIX/opt/opendesign"
NODE="$PREFIX/bin/node"
OD_MJS="$APP/apps/daemon/bin/od.mjs"
PI_BIN="$PREFIX/bin/pi"
DEFAULT_PORT=7456

for required in "$NODE" "$OD_MJS" "$PI_BIN" "$APP/apps/web/out/index.html"; do
  if [ ! -e "$required" ]; then
    echo "opendesign-bwrap.sh: missing $required" >&2
    echo "The design environment is incomplete; run \`pixi install -e design\`." >&2
    exit 2
  fi
done

# One positional workspace: a real directory (bound read-write) or `-` for a
# throwaway tmpfs inside the namespace, like bwrap-pi.sh.
WK_ARGS=()
WORKSPACE=""
PERSISTENT=false
if [ "${1:-}" = "-" ]; then
  echo "Running in an empty temporary directory (nothing is persisted)"
  WORKSPACE="/tmp/opendesign-workspace"
  WK_ARGS=(--tmpfs "$WORKSPACE" --chdir "$WORKSPACE")
else
  if [ -z "${1:-}" ]; then
    echo "opendesign-bwrap.sh: need a workspace directory (or -)" >&2
    exit 2
  fi
  if [ ! -d "$1" ]; then
    echo "opendesign-bwrap.sh: no such workspace directory: $1" >&2
    exit 2
  fi
  WORKSPACE="$(realpath "$1")"
  PERSISTENT=true
  WK_ARGS=(--bind "$WORKSPACE" "$WORKSPACE" --chdir "$WORKSPACE")
fi

# Decode forwarded args from env var (base64 encoded, null-separated).
# Avoids pixi shell-parser mangling of single-quote characters.
FWD_ARGS=()
if [ -n "${_FWD_ARGS:-}" ]; then
  while IFS= read -r -d '' arg; do
    FWD_ARGS+=("$arg")
  done < <(printf '%s' "$_FWD_ARGS" | base64 -d)
  unset _FWD_ARGS
elif [ $# -ge 2 ]; then
  FWD_ARGS=("${@:2}")
fi

EXTRA_BINDS=()
EXTRA_RO_BINDS=()
NO_GIT=false
PORT="$DEFAULT_PORT"
OD_ARGS=()
i=0
while [ $i -lt ${#FWD_ARGS[@]} ]; do
  arg="${FWD_ARGS[$i]}"
  case "$arg" in
    --bind | --ro-bind)
      if [ $((i + 1)) -ge ${#FWD_ARGS[@]} ]; then
        echo "opendesign-bwrap.sh: $arg needs a directory" >&2
        exit 2
      fi
      ABS_BIND="$(realpath "${FWD_ARGS[$((i + 1))]}" 2>/dev/null)" || {
        echo "opendesign-bwrap.sh: $arg: no such path: ${FWD_ARGS[$((i + 1))]}" >&2
        exit 2
      }
      if [ "$arg" = "--ro-bind" ]; then
        EXTRA_RO_BINDS+=(--ro-bind "$ABS_BIND" "$ABS_BIND")
        # The workspace bind is applied after these and wins inside itself, so a
        # read-only request pointing at the workspace or something inside it
        # comes out read-write. Warn rather than let that pass silently.
        if [ -n "${WORKSPACE:-}" ]; then
          case "$ABS_BIND/" in
            "$WORKSPACE"/*)
              echo "WARNING: --ro-bind $ABS_BIND is the workspace or inside it; the workspace bind" >&2
              echo "makes it read-write again. --ro-bind is for paths outside the workspace." >&2
              ;;
          esac
        fi
      else
        EXTRA_BINDS+=(--bind "$ABS_BIND" "$ABS_BIND")
      fi
      i=$((i + 2))
      ;;
    --no-git)
      NO_GIT=true
      i=$((i + 1))
      ;;
    --)
      # pixi's argument separator, if this script is called directly.
      i=$((i + 1))
      ;;
    --port)
      if [ $((i + 1)) -ge ${#FWD_ARGS[@]} ]; then
        echo "opendesign-bwrap.sh: --port needs a number" >&2
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
      # The daemon is loopback-only by design: the UI is served to the host
      # browser over the shared network namespace, and nothing else.
      echo "opendesign-bwrap.sh: --host is not supported; the daemon always binds 127.0.0.1" >&2
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
    echo "opendesign-bwrap.sh: --port must be a number, got '$PORT'" >&2
    exit 2
    ;;
esac

# Per-workspace state, all of it inside the workspace so it is covered by the
# one writable bind: OpenDesign's SQLite database, projects and generated files
# (OD_DATA_DIR), and the XDG/HOME-shaped scratch space its agents expect.
OD_STATE="$WORKSPACE/.od"
DATA_DIR="$OD_STATE"
INSTALL_DIR="$OD_STATE/installation"
AGENT_HOME="$OD_STATE/agent-home"
# Create the state directories on the host, inside the workspace, so nothing
# here writes outside it. With `-` the workspace lives in a tmpfs inside the
# namespace and there is nothing to prepare: the daemon creates OD_DATA_DIR and
# OD_INSTALLATION_DIR on its own.
if [ "$PERSISTENT" = true ]; then
  mkdir -p "$DATA_DIR" "$INSTALL_DIR" "$AGENT_HOME" \
    "$OD_STATE/cache" "$OD_STATE/config" "$OD_STATE/data" "$OD_STATE/state" "$OD_STATE/tmp"
fi

# Sanitize inherited OpenDesign launch settings before applying the explicit
# values below: a host shell (or a parent OpenDesign) may carry OD_DATA_DIR,
# OD_SANDBOX_MODE, an auth-disable switch or a bind root that would otherwise
# leak into this run. OD_SANDBOX_MODE stays unset on purpose — bubblewrap is
# the isolation here, and upstream's mode would relocate HOME and break Pi's
# config, gh's config lookup and the bind layout above.
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(OD_|OPEN_DESIGN)[^=]+')

# Pi setup, before entering bwrap: the same seeding bwrap-pi.sh does, so the
# daemon's Pi sees a package set and a config identical to the Pi launcher's.
# inject-strata-model.sh is deliberately NOT run: it needs Python, and the
# normal Pi launcher already keeps the Strata provider entry in models.json.
mkdir -p "$HOME/.pi/agent/sessions"
for f in auth trust settings; do
  if [ ! -f "$HOME/.pi/agent/$f.json" ]; then
    echo "{}" >"$HOME/.pi/agent/$f.json"
  fi
done
if [ ! -f "$HOME/.pi/agent/caveman.json" ]; then
  echo '{"defaultLevel": "lite", "showStatus": true}' >"$HOME/.pi/agent/caveman.json"
fi
if [ ! -f "$HOME/.pi/agent/models.json" ]; then
  echo '{"providers": {}}' >"$HOME/.pi/agent/models.json"
fi
bash "$SCRIPT_DIR/inject-pi-extensions.sh"

# Git/GitHub credentials: bound read-only, and https-only — the same contract as
# bwrap-pi.sh. ~/.gitconfig and ~/.config/git carry identity and behaviour;
# ~/.config/gh carries the gh token. Nothing else: no ~/.ssh, no
# ~/.git-credentials, no ssh-agent socket, and the host's /run (live agent and
# docker sockets) is hidden behind a tmpfs. --no-git drops all of it, which is
# its enforcement.
GIT_BINDS=()
if [ "$NO_GIT" = false ]; then
  for p in "$HOME/.config/git" "$HOME/.config/gh"; do
    [ -e "$p" ] && GIT_BINDS+=(--ro-bind "$p" "$p")
  done
  [ -f "$HOME/.gitconfig" ] && GIT_BINDS+=(--ro-bind "$HOME/.gitconfig" "$HOME/.gitconfig")
fi

# GitHub policy layer (scripts/git-guards: the git/gh PATH wrappers plus the
# hooks/ symlink farm over hook-dispatch), exactly as in the Pi sandbox. The
# directory is bound read-only *after* the workspace bind below, so the policy
# files stay read-only even when this repository is the workspace.
GUARD_BIN="$SCRIPT_DIR/git-guards"
HOOKS_DIR="$GUARD_BIN/hooks"

# DNS: /etc/resolv.conf is a symlink into /run/systemd/resolve on systemd hosts,
# so the /run tmpfs would break name resolution. Re-expose that one directory.
RESOLV_BIND=()
[ -d /run/systemd/resolve ] && RESOLV_BIND=(--ro-bind /run/systemd/resolve /run/systemd/resolve)

# PATH inside the namespace: the guard wrappers first, then the design prefix
# (node, pi, and anything else the environment provides), then the minimal
# system paths. $PREFIX/bin/pi is the raw packaged binary, which is exactly what
# PI_BIN must name — pointing it at ~/.local/bin/pi or scripts/bwrap-pi.sh would
# either re-enter a launcher or attempt a nested namespace.
SANDBOX_PATH="$GUARD_BIN:$PREFIX/bin:/usr/local/bin:/usr/bin:/bin"

POLICY_ARGS=(--setenv PATH "$SANDBOX_PATH")
if [ "$NO_GIT" = true ]; then
  POLICY_ARGS+=(--setenv PI_GIT_GUARD blocked)
  POLICY_ARGS+=(--setenv GIT_ALLOW_PROTOCOL file)
  POLICY_ARGS+=(--setenv GIT_TERMINAL_PROMPT 0)
  POLICY_ARGS+=(--setenv GH_CONFIG_DIR "$OD_STATE/gh-empty")
  # No credential carrier may survive into this mode: environment tokens, the
  # ssh/askpass hooks, and GIT_CONFIG_* (a host shell can carry an
  # http.<url>.extraheader Authorization there).
  POLICY_ARGS+=(--unsetenv SSH_AUTH_SOCK)
  POLICY_ARGS+=(--unsetenv GH_TOKEN --unsetenv GITHUB_TOKEN)
  POLICY_ARGS+=(--unsetenv GH_ENTERPRISE_TOKEN --unsetenv GITHUB_ENTERPRISE_TOKEN)
  POLICY_ARGS+=(--unsetenv GIT_CONFIG_COUNT --unsetenv GIT_CONFIG_KEY_0 --unsetenv GIT_CONFIG_VALUE_0)
  POLICY_ARGS+=(--unsetenv GIT_ASKPASS --unsetenv SSH_ASKPASS --unsetenv GIT_SSH_COMMAND)
else
  POLICY_ARGS+=(--setenv PI_GIT_GUARD restricted)
  # core.hooksPath for this session only (like a -c flag): the host's own git
  # config is never touched. The git wrapper re-injects these on every
  # invocation, so a spawned shell cannot drop them by rewriting its env.
  POLICY_ARGS+=(--setenv GIT_CONFIG_COUNT 1)
  POLICY_ARGS+=(--setenv GIT_CONFIG_KEY_0 core.hooksPath)
  POLICY_ARGS+=(--setenv GIT_CONFIG_VALUE_0 "$HOOKS_DIR")
fi

# The daemon's own environment. Every path here is either inside the workspace
# (writable) or inside the read-only design prefix.
OD_ENV=(
  --setenv OD_BIN "$OD_MJS"
  --setenv OD_NODE_BIN "$NODE"
  --setenv OD_INSTALLATION_DIR "$INSTALL_DIR"
  --setenv OD_RESOURCE_ROOT "$APP/resources"
  --setenv OD_DATA_DIR "$DATA_DIR"
  --setenv OD_AGENT_HOME "$AGENT_HOME"
  --setenv OD_BIND_HOST 127.0.0.1
  --setenv OD_PORT "$PORT"
  --setenv NODE_ENV production
  --setenv PI_BIN "$PI_BIN"
  --setenv PI_CODING_AGENT_DIR "$HOME/.pi/agent"
  --setenv XDG_CACHE_HOME "$OD_STATE/cache"
  --setenv XDG_CONFIG_HOME "$OD_STATE/config"
  --setenv XDG_DATA_HOME "$OD_STATE/data"
  --setenv XDG_STATE_HOME "$OD_STATE/state"
  --setenv TMPDIR "$OD_STATE/tmp"
)
# OD_SANDBOX_MODE is unset above and never set here.

# If the workspace is a git worktree, bind the main repository's .git directory
# read-write so git can read shared objects and update worktree admin files
# without exposing the main checkout — the same rule bwrap-pi.sh applies.
WORKTREE_BINDS=()
if [ -n "$WORKSPACE" ] && [ -d "$WORKSPACE" ] \
  && GD="$(git -C "$WORKSPACE" rev-parse --git-dir 2>/dev/null)" \
  && GC="$(git -C "$WORKSPACE" rev-parse --git-common-dir 2>/dev/null)"; then
  GD_ABS="$(cd "$WORKSPACE" && cd "$GD" && pwd)"
  GC_ABS="$(cd "$WORKSPACE" && cd "$GC" && pwd)"
  if [ "$GD_ABS" != "$GC_ABS" ]; then
    WORKTREE_BINDS=(--bind "$GC_ABS" "$GC_ABS")
  fi
fi

# Unset all PIXI_*/CONDA_* and the pixi-activation env vars (the values the
# namespace needs are passed explicitly through --setenv above).
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

echo "OpenDesign on http://127.0.0.1:$PORT (bubblewrap sandbox, Ctrl-C to stop)"
echo "  workspace  $WORKSPACE"
echo "  state      $OD_STATE"

# Mount order is load-bearing, exactly as in bwrap-pi.sh:
#   * read-only extra binds come before $WK_ARGS, so the workspace wins inside
#     itself;
#   * the guards and the whole design prefix are re-bound read-only *after* the
#     workspace, so neither can be modified even when this repository is the
#     workspace;
#   * the packaged Pi tree is bound over the prefix afterwards and read-write,
#     for the same reason bwrap-pi.sh binds it that way: Pi writes at runtime —
#     ~/.pi/agent/npm and models-store.json go through it on every
#     `pi --list-models` — and a read-only tree makes those writes fail. What
#     must survive an environment reinstall is bound from the host on top of it:
#     auth, trust, settings, models, caveman and sessions;
#   * ~/.pi/agent/intercom is a fresh tmpfs, so the pi-intercom broker and its
#     socket stay private to this namespace.
exec bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --tmpfs /run \
  "${RESOLV_BIND[@]}" \
  "${EXTRA_BINDS[@]}" \
  "${EXTRA_RO_BINDS[@]}" \
  "${WORKTREE_BINDS[@]}" \
  "${GIT_BINDS[@]}" \
  "${POLICY_ARGS[@]}" \
  "${OD_ENV[@]}" \
  "${WK_ARGS[@]}" \
  --ro-bind "$GUARD_BIN" "$GUARD_BIN" \
  --ro-bind "$PREFIX" "$PREFIX" \
  --bind "$PREFIX/home/.pi" "$HOME/.pi" \
  --bind "$HOME/.pi/agent/auth.json" "$HOME/.pi/agent/auth.json" \
  --bind "$HOME/.pi/agent/trust.json" "$HOME/.pi/agent/trust.json" \
  --bind "$HOME/.pi/agent/settings.json" "$HOME/.pi/agent/settings.json" \
  --bind "$HOME/.pi/agent/models.json" "$HOME/.pi/agent/models.json" \
  --bind "$HOME/.pi/agent/caveman.json" "$HOME/.pi/agent/caveman.json" \
  --bind "$HOME/.pi/agent/sessions" "$HOME/.pi/agent/sessions" \
  --tmpfs "$HOME/.pi/agent/intercom" \
  --die-with-parent \
  --unshare-all --share-net \
  -- "$NODE" "$OD_MJS" daemon start --serve-web --no-open --port "$PORT" "${OD_ARGS[@]}"
