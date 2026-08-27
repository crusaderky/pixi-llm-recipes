#!/bin/bash
# Run Claude Code with read/write permissions in pwd and no access anywhere else.
# Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
#
# Usage: bwrap-claude.sh <dir|-> [--with-git] [--bind <dir>] ... [-- claude-args...]
#   Forwarded args are read from _FWD_ARGS env var (base64-encoded, null-separated,
#   set by the scripts/claude wrapper) or, as a fallback, from positional args $2 onward
#   (for direct `pixi r claude -- <args>` invocations).
set -o errexit
set -o nounset

if [ "$1" == "-" ]; then
  echo "Running in empty temporary directory"
  echo "Use \`pixi run -e claude claude <directory>\` to move to a specific directory."
  ARGS="--tmpfs /tmp/claude --chdir /tmp/claude"
else
  DIR="$(realpath "$1")"
  ARGS="--bind $DIR $DIR --chdir $DIR"
fi

mkdir -p ~/.cache/ccache
mkdir -p ~/.cache/claude
mkdir -p ~/.cache/llama-cpp-changelog
mkdir -p ~/.cache/claude-cli-nodejs
mkdir -p ~/.cache/pip
mkdir -p ~/.cache/pre-commit
mkdir -p ~/.cache/rattler
mkdir -p ~/.cache/uv
mkdir -p ~/.config/rtk
mkdir -p ~/.claude
if [ ! -e ~/.claude.json ]; then touch ~/.claude.json; fi

# Decode forwarded args from env var (base64 encoded, null-separated).
# Avoids pixi shell-parser mangling of single-quote characters.
# Falls back to positional args ($2 onward) when _FWD_ARGS is not set,
# for direct `pixi r claude -- <args>` invocations that bypass scripts/claude.
FWD_ARGS=()
if [ -n "${_FWD_ARGS:-}" ]; then
  while IFS= read -r -d '' arg; do
    FWD_ARGS+=("$arg")
  done < <(printf '%s' "$_FWD_ARGS" | base64 -d)
  unset _FWD_ARGS
elif [ $# -ge 2 ]; then
  FWD_ARGS=("${@:2}")
fi

# Parse --bind <dir> pairs and --with-git flags from forwarded args
EXTRA_BINDS=""
WITH_GIT=false
CLAUDE_ARGS=()
i=0
while [ $i -lt ${#FWD_ARGS[@]} ]; do
  arg="${FWD_ARGS[$i]}"
  if [ "$arg" = "--bind" ]; then
    bind_dir="${FWD_ARGS[$((i+1))]}"
    ABS_BIND="$(realpath "$bind_dir")"
    EXTRA_BINDS="$EXTRA_BINDS --bind $ABS_BIND $ABS_BIND"
    i=$((i + 2))
  elif [ "$arg" = "--with-git" ]; then
    WITH_GIT=true
    i=$((i + 1))
  else
    CLAUDE_ARGS+=("$arg")
    i=$((i + 1))
  fi
done

# --with-git: bind SSH keys, git config, and gh CLI auth into the sandbox (read-only,
# except ~/.config/gh which gh may write token refreshes to).
# The SSH agent socket (SSH_AUTH_SOCK) is accessible via the root bind as long as it
# lives under /run/ (typical for gnome-keyring/systemd). If it's under /tmp, bind it too.
_CONDA_PREFIX="$CONDA_PREFIX"
_PIXI_ROOT="$(dirname "$(dirname "$PIXI_EXE")")"  # Typically ~/.pixi

# Inject conda-packaged Claude Code extensions into ~/.claude
bash "$(dirname "$0")/inject-claude-extensions.sh"

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

GIT_BINDS=""
if [ "$WITH_GIT" = true ]; then
  for p in "$HOME/.ssh" "$HOME/.config/git" "$HOME/.git-credentials"; do
    [ -e "$p" ] && GIT_BINDS="$GIT_BINDS --ro-bind $p $p"
  done
  [ -f "$HOME/.gitconfig" ] && GIT_BINDS="$GIT_BINDS --ro-bind $HOME/.gitconfig $HOME/.gitconfig"
  [ -d "$HOME/.config/gh" ] && GIT_BINDS="$GIT_BINDS --bind $HOME/.config/gh $HOME/.config/gh"
  if [ -n "${SSH_AUTH_SOCK:-}" ] && [[ "$SSH_AUTH_SOCK" == /tmp/* ]]; then
    GIT_BINDS="$GIT_BINDS --ro-bind $SSH_AUTH_SOCK $SSH_AUTH_SOCK"
  fi
fi

# Claude Code puts its cross-session-messaging sockets in $XDG_RUNTIME_DIR/cc-socks.
# The read-only root bind leaves it unable to create that directory, so it prints
# "Cross-session messaging is off: its socket directory could not be set up".
# Mount a private tmpfs there instead: the sandbox gets a writable sockets
# directory of its own, without a channel into unsandboxed sessions on the host.
# bwrap cannot mkdir a mountpoint under the read-only root, so the directory has
# to exist on the host first.
CC_SOCKS_TMPFS=""
if [ -d "${XDG_RUNTIME_DIR:-}" ]; then
  mkdir -p "$XDG_RUNTIME_DIR/cc-socks"
  chmod 700 "$XDG_RUNTIME_DIR/cc-socks"
  CC_SOCKS_TMPFS="--tmpfs $XDG_RUNTIME_DIR/cc-socks"
fi

# Unset all PIXI_*/CONDA_* and the pixi-activation env vars so they don't leak
# into the sandboxed Claude Code process.
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

# Note: --ro-bind $_CONDA_PREFIX must be after --bind $1.
# When setting pixi-llm-recipes as the project root for the bind,
# re-bind $CONDA_PREFIX as read-only after it's bound as read-write

exec bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  $CC_SOCKS_TMPFS \
  --bind    "$HOME/.cache/ccache"                 "$HOME/.cache/ccache" \
  --bind    "$HOME/.cache/claude"                 "$HOME/.cache/claude" \
  --bind    "$HOME/.cache/llama-cpp-changelog"    "$HOME/.cache/llama-cpp-changelog" \
  --bind    "$HOME/.cache/claude-cli-nodejs" "$HOME/.cache/claude-cli-nodejs" \
  --bind    "$HOME/.cache/pip"               "$HOME/.cache/pip" \
  --bind    "$HOME/.cache/pre-commit"        "$HOME/.cache/pre-commit" \
  --bind    "$HOME/.cache/rattler"           "$HOME/.cache/rattler" \
  --bind    "$HOME/.cache/uv"                "$HOME/.cache/uv" \
  --bind    "$HOME/.config/rtk"              "$HOME/.config/rtk" \
  --bind    "$HOME/.claude"                  "$HOME/.claude" \
  --bind    "$HOME/.claude.json"             "$HOME/.claude.json" \
  --ro-bind "$_PIXI_ROOT"                    "$_PIXI_ROOT" \
  $EXTRA_BINDS \
  $WORKTREE_BINDS \
  $GIT_BINDS \
  $ARGS \
  --ro-bind "$_CONDA_PREFIX"                 "$_CONDA_PREFIX" \
  --die-with-parent \
  --unshare-all --share-net \
  -- claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]}"

