#!/bin/bash
# Run Pi with read/write permissions in pwd and no access anywhere else
# Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
#
# NVIDIA GPUs are passed through when the host has the driver loaded
# (/dev/nvidia* are dev-bound through the fresh --dev /dev), so CUDA builds
# and llama.cpp GPU runs work from inside the sandbox.
#
# Usage: bwrap-pi.sh <dir|-> [--no-git] [--bind <dir>] ... [-- pi-args...]
#   Git/GitHub access is ON by default under a non-destructive policy: fast-forward
#   pushes, fetch/pull and gh reads/creation work; force-push, remote branch/ref
#   deletion and deleting/modifying GitHub posts are blocked.
#   --no-git blocks GitHub access entirely.
#   Forwarded args are read from _FWD_ARGS env var (base64-encoded, null-separated,
#   set by the scripts/pi wrapper) or, as a fallback, from positional args $2 onward
#   (for direct `pixi r pi -- <args>` invocations).
set -o errexit
set -o nounset

if [ "$1" == "-" ]; then
  echo "Running in empty temporary directory"
  echo "Use \`pixi run pi <directory>\` to move to a specific directory."
  ARGS="--tmpfs /tmp/pi --chdir /tmp/pi"
else
  DIR="$(realpath "$1")"
  ARGS="--bind $DIR $DIR --chdir $DIR"
fi


# Decode forwarded args from env var (base64 encoded, null-separated).
# Avoids pixi shell-parser mangling of single-quote characters.
# Falls back to positional args ($2 onward) when _FWD_ARGS is not set,
# for direct `pixi r pi -- <args>` invocations that bypass scripts/pi.
FWD_ARGS=()
if [ -n "${_FWD_ARGS:-}" ]; then
  while IFS= read -r -d '' arg; do
    FWD_ARGS+=("$arg")
  done < <(printf '%s' "$_FWD_ARGS" | base64 -d)
  unset _FWD_ARGS
elif [ $# -ge 2 ]; then
  FWD_ARGS=("${@:2}")
fi

# Parse --bind <dir> pairs and --no-git flags from forwarded args
EXTRA_BINDS=""
NO_GIT=false
PI_ARGS=()
i=0
while [ $i -lt ${#FWD_ARGS[@]} ]; do
  arg="${FWD_ARGS[$i]}"
  if [ "$arg" = "--bind" ]; then
    bind_dir="${FWD_ARGS[$((i+1))]}"
    ABS_BIND="$(realpath "$bind_dir")"
    EXTRA_BINDS="$EXTRA_BINDS --bind $ABS_BIND $ABS_BIND"
    i=$((i + 2))
  elif [ "$arg" = "--no-git" ]; then
    NO_GIT=true
    i=$((i + 1))
  else
    PI_ARGS+=("$arg")
    i=$((i + 1))
  fi
done

# Git/GitHub credentials: bound by default (the non-destructive policy below is
# what keeps the access safe). Read-only, except ~/.config/gh which gh may write
# token refreshes to. --no-git drops all of it.
# The SSH agent socket (SSH_AUTH_SOCK) is accessible via the root bind as long as it
# lives under /run/ (typical for gnome-keyring/systemd). If it's under /tmp, bind it too.
GIT_BINDS=""
if [ "$NO_GIT" = false ]; then
  for p in "$HOME/.ssh" "$HOME/.config/git" "$HOME/.git-credentials"; do
    [ -e "$p" ] && GIT_BINDS="$GIT_BINDS --ro-bind $p $p"
  done
  [ -f "$HOME/.gitconfig" ] && GIT_BINDS="$GIT_BINDS --ro-bind $HOME/.gitconfig $HOME/.gitconfig"
  [ -d "$HOME/.config/gh" ] && GIT_BINDS="$GIT_BINDS --bind $HOME/.config/gh $HOME/.config/gh"
  if [ -n "${SSH_AUTH_SOCK:-}" ] && [[ "$SSH_AUTH_SOCK" == /tmp/* ]]; then
    GIT_BINDS="$GIT_BINDS --ro-bind $SSH_AUTH_SOCK $SSH_AUTH_SOCK"
  fi
fi

# GitHub policy layer (scripts/git-guards: the git/gh PATH wrappers plus the
# hooks/ symlink farm over hook-dispatch). Present in every mode; there is no
# flag that re-enables destructive activity.
#   default:   non-destructive policy — git/gh work, but force-push, remote
#              ref/branch deletion and deleting/modifying GitHub posts are
#              blocked (guard wrappers on PATH + the pre-push policy hook).
#   --no-git:  all GitHub access blocked — guard stubs, no credentials bound
#              (above), no git network transport, gh pointed at an empty config,
#              ssh-agent sockets hidden, and an unforgeable marker
#              (/etc/pi-git-policy) that keeps the wrappers blocked even if the
#              agent flips $PI_GIT_GUARD.
# $GUARD_BIN is bound read-only at the bottom of the bwrap invocation — after
# the workdir bind, so it stays read-only even when the workspace is this repo.
GUARD_BIN="$(cd "$(dirname "$0")/git-guards" && pwd)"
HOOKS_DIR="$GUARD_BIN/hooks"
POLICY_ARGS="--setenv PATH $GUARD_BIN:$PATH"
if [ "$NO_GIT" = true ]; then
  POLICY_ARGS="$POLICY_ARGS --setenv PI_GIT_GUARD blocked"
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_ALLOW_PROTOCOL file"
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_TERMINAL_PROMPT 0"
  POLICY_ARGS="$POLICY_ARGS --setenv GH_CONFIG_DIR /tmp/pi-gh-empty"
  POLICY_ARGS="$POLICY_ARGS --unsetenv SSH_AUTH_SOCK --unsetenv GH_TOKEN --unsetenv GITHUB_TOKEN"
  # Enforcement is mount-based, not env-based: the marker wins over $PI_GIT_GUARD,
  # so flipping the variable inside the sandbox cannot downgrade the policy.
  POLICY_ARGS="$POLICY_ARGS --ro-bind $GUARD_BIN/marker-blocked /etc/pi-git-policy"
  # Unsetting SSH_AUTH_SOCK is cosmetic: the agent socket lives under /run and is
  # reachable through the read-only root bind (AF_UNIX connect works on read-only
  # mounts). Hide the usual socket homes and the socket itself.
  _RT="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  for _d in "$_RT/keyring" "$_RT/ssh-unix-local" /run/ssh-unix-local; do
    [ -d "$_d" ] && POLICY_ARGS="$POLICY_ARGS --tmpfs $_d"
  done
  [ -n "${SSH_AUTH_SOCK:-}" ] && POLICY_ARGS="$POLICY_ARGS --ro-bind /dev/null $SSH_AUTH_SOCK"
else
  POLICY_ARGS="$POLICY_ARGS --setenv PI_GIT_GUARD restricted"
  # core.hooksPath for this session only (like a -c flag): the host's own git
  # config is never touched. hook-dispatch carries the pre-push policy and
  # delegates every other hook name to the repository's own hooks.
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_CONFIG_COUNT 1"
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_CONFIG_KEY_0 core.hooksPath"
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_CONFIG_VALUE_0 $HOOKS_DIR"
fi

# Expose NVIDIA CUDA devices when the host driver is loaded. --dev /dev
# starts a fresh /dev, so every /dev/nvidia* node must be dev-bound through.
# /dev/nvidia-uvm is created on demand by the setuid nvidia-modprobe helper;
# without it CUDA initialisation fails even with the other nodes present.
# No-op on hosts without an NVIDIA driver.
CUDA_BINDS=""
if [ -e /dev/nvidiactl ]; then
  command -v nvidia-modprobe > /dev/null 2>&1 && nvidia-modprobe -u -c=0 || true
  for d in /dev/nvidia*; do
    [ -c "$d" ] || [ -b "$d" ] || continue
    CUDA_BINDS="$CUDA_BINDS --dev-bind $d $d"
  done
  # Driver metadata, used by nvidia-smi for its version line; harmless if absent.
  [ -d /proc/driver/nvidia ] && CUDA_BINDS="$CUDA_BINDS --ro-bind /proc/driver/nvidia /proc/driver/nvidia"
fi

_PIXI_ROOT="$(dirname "$(dirname "$PIXI_EXE")")"  # Typically ~/.pixi
_CONDA_PREFIX="$CONDA_PREFIX"

# If the working directory is a git worktree, bind the main repository's .git
# directory read-write so git can read shared objects and update worktree admin
# files (refs, locks) without exposing the main worktree's checked-out files.
WORKTREE_BINDS=""
if [ "$1" != "-" ]; then
  if GD="$(git -C "$DIR" rev-parse --git-dir 2>/dev/null)" \
     && GC="$(git -C "$DIR" rev-parse --git-common-dir 2>/dev/null)"; then
    GD_ABS="$(cd "$DIR" && cd "$GD" && pwd)"
    GC_ABS="$(cd "$DIR" && cd "$GC" && pwd)"
    if [ "$GD_ABS" != "$GC_ABS" ]; then
      WORKTREE_BINDS="--bind $GC_ABS $GC_ABS"
    fi
  fi
fi

mkdir -p ~/.cache/ccache
mkdir -p ~/.cache/llama-cpp-changelog
mkdir -p ~/.cache/pip
mkdir -p ~/.cache/pre-commit
mkdir -p ~/.cache/rattler
mkdir -p ~/.cache/uv
mkdir -p ~/.pi/agent/sessions
mkdir -p ~/.config/rtk

for f in auth trust settings; do
  if [ ! -f ~/.pi/agent/$f.json ]; then
    echo "{}" > ~/.pi/agent/$f.json
  fi
done
if [ ! -f ~/.pi/agent/models.json ]; then
  echo '{"providers": {}}' > ~/.pi/agent/models.json
fi

bash "$(dirname "$0")/inject-pi-extensions.sh"

function cleanup {
  rsync -avcO --no-perms --no-times "$_CONDA_PREFIX"/home/.pi/agent/{skills,AGENTS.md,keybindings.json} pixi-recipes/pi-home/
}
trap cleanup EXIT

# Unset all PIXI_*/CONDA_* and the pixi-activation env vars
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

# Note: --ro-bind $_CONDA_PREFIX must be after --bind $1.
# When setting pixi-llm-recipes as the project root for the bind,
# re-bind $CONDA_PREFIX as read-only after it's bound as read-write.
# Same for $GUARD_BIN: after $ARGS, so the policy files stay read-only even
# when the workspace is this repo (which would bind them read-write).

bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --bind "$HOME/.cache/ccache"            "$HOME/.cache/ccache" \
  --bind "$HOME/.cache/llama-cpp-changelog"    "$HOME/.cache/llama-cpp-changelog" \
  --bind "$HOME/.cache/pip"               "$HOME/.cache/pip" \
  --bind "$HOME/.cache/pre-commit"        "$HOME/.cache/pre-commit" \
  --bind "$HOME/.cache/rattler"           "$HOME/.cache/rattler" \
  --bind "$HOME/.cache/uv"                "$HOME/.cache/uv" \
  --bind "$HOME/.config/rtk"              "$HOME/.config/rtk" \
  --bind "$_CONDA_PREFIX/home/.pi"        "$HOME/.pi" \
  --bind "$HOME/.pi/agent/auth.json"      "$HOME/.pi/agent/auth.json" \
  --bind "$HOME/.pi/agent/trust.json"     "$HOME/.pi/agent/trust.json" \
  --bind "$HOME/.pi/agent/settings.json"  "$HOME/.pi/agent/settings.json" \
  --bind "$HOME/.pi/agent/models.json"    "$HOME/.pi/agent/models.json" \
  --bind "$HOME/.pi/agent/sessions"       "$HOME/.pi/agent/sessions" \
  --ro-bind "$_PIXI_ROOT"                 "$_PIXI_ROOT" \
  $EXTRA_BINDS \
  $WORKTREE_BINDS \
  $GIT_BINDS \
  $CUDA_BINDS \
  $POLICY_ARGS \
  $ARGS \
  --ro-bind "$GUARD_BIN"                  "$GUARD_BIN" \
  --ro-bind "$_CONDA_PREFIX"              "$_CONDA_PREFIX" \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${PI_ARGS[@]}"
