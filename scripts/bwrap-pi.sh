#!/bin/bash
# Run Pi with read/write permissions in pwd and no access anywhere else
# Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
#
# NVIDIA GPUs are passed through when the host has the driver loaded
# (/dev/nvidia* are dev-bound through the fresh --dev /dev), so CUDA builds
# and llama.cpp GPU runs work from inside the sandbox.
#
# Usage: bwrap-pi.sh <dir|-> [--no-git] [--subagents] [--bind <dir>] ... [-- pi-args...]
#   --subagents loads the otherwise-filtered pi-subagents package for this run.
#   Git/GitHub access is ON by default under a non-destructive policy: fetch/pull,
#   fast-forward pushes and gh reads/creation work; force-push, remote branch/ref
#   deletion and deleting/modifying GitHub posts are blocked.
#   --no-git binds no GitHub credential at all — that is its enforcement — and
#   switches git's network transport and gh off as a UX layer.
#   GitHub authentication is https + the gh token only: ssh keys/sockets are
#   never bound in (an agent/key is an unscopeable full-write credential), and
#   the host's /run is hidden behind a tmpfs (it holds live ssh-agent sockets
#   and the docker sockets — root-equivalent escapes; AF_UNIX connect() works
#   across read-only mounts, so hiding them requires the tmpfs). Only
#   /run/systemd/resolve is re-exposed afterwards: /etc/resolv.conf is a symlink
#   into it on systemd hosts, and its sockets carry no privilege.
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

# Parse --bind <dir> pairs, --no-git, and --subagents from forwarded args.
EXTRA_BINDS=""
NO_GIT=false
WITH_SUBAGENTS=false
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
  elif [ "$arg" = "--subagents" ]; then
    WITH_SUBAGENTS=true
    i=$((i + 1))
  else
    PI_ARGS+=("$arg")
    i=$((i + 1))
  fi
done

# Git/GitHub credentials: bound read-only, and https-only. ~/.gitconfig and
# ~/.config/git carry identity and behaviour; ~/.config/gh carries the gh
# token (read-only — gh does not need to write it: run `gh auth refresh` from
# your own shell when it expires, and it also keeps the agent from adding gh
# aliases, which the policy wrapper cannot see through). Nothing else: no
# ~/.ssh, no ~/.git-credentials, no ssh-agent socket — ssh keys are unscopeable
# full-write credentials, so the sandbox pushes over https through the
# `gh auth git-credential` helper instead. --no-git drops all of it.
GIT_BINDS=""
if [ "$NO_GIT" = false ]; then
  for p in "$HOME/.config/git" "$HOME/.config/gh"; do
    [ -e "$p" ] && GIT_BINDS="$GIT_BINDS --ro-bind $p $p"
  done
  [ -f "$HOME/.gitconfig" ] && GIT_BINDS="$GIT_BINDS --ro-bind $HOME/.gitconfig $HOME/.gitconfig"
  # The gh token must be in plain hosts.yml storage: a keyring-stored one
  # (gh auth login --secure-storage) is invisible here, because the /run
  # tmpfs below takes the keyring's D-Bus socket with it, and every push
  # inside the session fails with "could not read Username". Warn now
  # instead of failing later; `pixi r install-git` migrates the token.
  if [ -f "$HOME/.config/gh/hosts.yml" ] \
     && grep -q 'user:' "$HOME/.config/gh/hosts.yml" \
     && ! grep -q 'oauth_token' "$HOME/.config/gh/hosts.yml"; then
    echo "WARNING: gh token is in the system keyring, which this sandbox cannot" >&2
    echo "read (/run is hidden) — pushes inside the session will fail. Run" >&2
    echo "'pixi r install-git' on the host: it re-stores the token in plain" >&2
    echo "hosts.yml storage."
  fi
fi

# GitHub policy layer (scripts/git-guards: the git/gh PATH wrappers plus the
# hooks/ symlink farm over hook-dispatch). Present in every mode; there is no
# flag that re-enables destructive activity.
#   default:   non-destructive policy — git/gh work, but force-push, remote
#              ref/branch deletion and deleting/modifying GitHub posts are
#              blocked (guard wrappers on PATH + the pre-push policy hook).
#   --no-git:  no GitHub credential is bound (above) — that is the enforcement:
#              without a token or key there is nothing to write with. The env
#              layers below only add UX (git's network transport off, gh pointed
#              at an empty config); they are not load-bearing.
# $GUARD_BIN is bound read-only at the bottom of the bwrap invocation — after
# the workdir bind, so it stays read-only even when the workspace is this repo.
GUARD_BIN="$(cd "$(dirname "$0")/git-guards" && pwd)"
HOOKS_DIR="$GUARD_BIN/hooks"
# DNS: /etc/resolv.conf is a symlink into /run/systemd/resolve on systemd
# hosts, so the /run tmpfs below would break name resolution. Re-expose that
# one directory (resolver sockets only, no privilege).
RESOLV_BIND=""
[ -d /run/systemd/resolve ] && RESOLV_BIND="--ro-bind /run/systemd/resolve /run/systemd/resolve"

POLICY_ARGS="--setenv PATH $GUARD_BIN:$PATH"
if [ "$NO_GIT" = true ]; then
  POLICY_ARGS="$POLICY_ARGS --setenv PI_GIT_GUARD blocked"
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_ALLOW_PROTOCOL file"
  POLICY_ARGS="$POLICY_ARGS --setenv GIT_TERMINAL_PROMPT 0"
  POLICY_ARGS="$POLICY_ARGS --setenv GH_CONFIG_DIR /tmp/pi-gh-empty"
  # No credential carrier may survive into this mode: environment tokens, the
  # ssh/askpass hooks, and GIT_CONFIG_* (a host shell can carry an
  # http.<url>.extraheader Authorization there).
  POLICY_ARGS="$POLICY_ARGS --unsetenv SSH_AUTH_SOCK"
  POLICY_ARGS="$POLICY_ARGS --unsetenv GH_TOKEN --unsetenv GITHUB_TOKEN"
  POLICY_ARGS="$POLICY_ARGS --unsetenv GH_ENTERPRISE_TOKEN --unsetenv GITHUB_ENTERPRISE_TOKEN"
  POLICY_ARGS="$POLICY_ARGS --unsetenv GIT_CONFIG_COUNT --unsetenv GIT_CONFIG_KEY_0 --unsetenv GIT_CONFIG_VALUE_0"
  POLICY_ARGS="$POLICY_ARGS --unsetenv GIT_ASKPASS --unsetenv SSH_ASKPASS --unsetenv GIT_SSH_COMMAND"
else
  POLICY_ARGS="$POLICY_ARGS --setenv PI_GIT_GUARD restricted"
  # core.hooksPath for this session only (like a -c flag): the host's own git
  # config is never touched. hook-dispatch carries the pre-push policy and
  # delegates every other hook name to the repository's own hooks. The git
  # wrapper re-injects these on every invocation, so the agent cannot drop them
  # by rewriting its environment.
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
HERDR_WORKTREE_BINDS=""
if [ "$1" != "-" ]; then
  if GD="$(git -C "$DIR" rev-parse --git-dir 2>/dev/null)" \
     && GC="$(git -C "$DIR" rev-parse --git-common-dir 2>/dev/null)"; then
    GD_ABS="$(cd "$DIR" && cd "$GD" && pwd)"
    GC_ABS="$(cd "$DIR" && cd "$GC" && pwd)"
    if [ "$GD_ABS" != "$GC_ABS" ]; then
      WORKTREE_BINDS="--bind $GC_ABS $GC_ABS"
    fi
  fi

  # Herdr worktrees must survive this sandbox. Only expose the project-specific
  # worktree directory when DIR is the root of an actual Git checkout. Keep this
  # bind when DIR is already a worktree too: the current-worktree bind alone
  # would not expose persistent siblings. Derive the project name from its path
  # instead of treating the worktree name as a new project; the target path is
  # unchanged.
  if GIT_ROOT="$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)" \
     && [ "$GIT_ROOT" = "$DIR" ]; then
    HERDR_WORKTREES="$HOME/.herdr/worktrees"
    case "$DIR" in
      "$HERDR_WORKTREES"/*/*)
        HERDR_PROJECT="${DIR#"$HERDR_WORKTREES"/}"
        HERDR_PROJECT="${HERDR_PROJECT%%/*}"
        ;;
      *)
        HERDR_PROJECT="$(basename "$DIR")"
        ;;
    esac
    HERDR_WORKTREE_DIR="$HERDR_WORKTREES/$HERDR_PROJECT"
    mkdir -p "$HERDR_WORKTREE_DIR"
    HERDR_WORKTREE_BINDS="--bind $HERDR_WORKTREE_DIR $HERDR_WORKTREE_DIR"
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

SUBAGENT_ARGS=()
if [ "$WITH_SUBAGENTS" = true ]; then
  SUBAGENT_DIR="$_CONDA_PREFIX/home/.pi/agent/npm/node_modules/pi-subagents"
  for resource in index.js skills prompts; do
    if [ ! -e "$SUBAGENT_DIR/$resource" ]; then
      echo "pi-subagents resource not found: $SUBAGENT_DIR/$resource" >&2
      exit 1
    fi
  done
  SUBAGENT_ARGS=(
    --extension "$SUBAGENT_DIR/index.js"
    --skill "$SUBAGENT_DIR/skills"
    --prompt-template "$SUBAGENT_DIR/prompts"
  )
fi

function cleanup {
  rsync -avcO --no-perms --no-times "$_CONDA_PREFIX"/home/.pi/agent/{skills,AGENTS.md,keybindings.json} pixi-recipes/pi-home/
}
trap cleanup EXIT

# Unset all PIXI_*/CONDA_* and the pixi-activation env vars
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

# pi-intercom setup note:
# ~/.pi/agent/intercom is a fresh tmpfs per sandbox: the pi-intercom broker, its unix
# socket, and all of its runtime state stay private to this sandbox. Sessions inside the
# same sandbox (pi and all its pi-subagents children) can message each other;
# independent sandboxes or unsandboxed pi instances on the host cannot be reached.
# Parallel sandboxes don't conflict: each has its own broker on its own private socket.
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
  --tmpfs /run \
  $RESOLV_BIND \
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
  --tmpfs "$HOME/.pi/agent/intercom" \
  --ro-bind "$_PIXI_ROOT"                 "$_PIXI_ROOT" \
  $EXTRA_BINDS \
  $HERDR_WORKTREE_BINDS \
  $WORKTREE_BINDS \
  $GIT_BINDS \
  $CUDA_BINDS \
  $POLICY_ARGS \
  $ARGS \
  --ro-bind "$GUARD_BIN"                  "$GUARD_BIN" \
  --ro-bind "$_CONDA_PREFIX"              "$_CONDA_PREFIX" \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${SUBAGENT_ARGS[@]}" "${PI_ARGS[@]}"
