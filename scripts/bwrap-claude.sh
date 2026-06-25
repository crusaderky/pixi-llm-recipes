#!/bin/bash
# Run Claude Code with read/write permissions in pwd and no access anywhere else.
# Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
#
# Usage: bwrap-claude.sh <dir|-> [--with-git] [--with-herdr] [--bind <dir>] ... [-- claude-args...]
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

# Parse --bind <dir> pairs and --with-git / --with-herdr flags from forwarded args
EXTRA_BINDS=""
WITH_GIT=false
WITH_HERDR=false
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
  elif [ "$arg" = "--with-herdr" ]; then
    WITH_HERDR=true
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

# --with-herdr: bind ~/.config/herdr (which holds herdr.sock) into the sandbox so the
# agent can drive the herdr instance it runs inside. SECURITY: herdr.sock is a
# full-control socket and herdr runs OUTSIDE the sandbox, so an agent with access can
# spawn unsandboxed host-side shells via the socket — a full sandbox escape. Only pass
# this when you trust the agent with full host access.
HERDR_BINDS=""
if [ "$WITH_HERDR" = true ]; then
  mkdir -p ~/.config/herdr
  HERDR_BINDS="--bind $HOME/.config/herdr $HOME/.config/herdr"
fi

# Unset all PIXI_*/CONDA_* and the pixi-activation env vars so they don't leak
# into the sandboxed Claude Code process.
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

exec bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --bind    "$HOME/.cache/ccache"                 "$HOME/.cache/ccache" \
  --bind    "$HOME/.cache/claude"                 "$HOME/.cache/claude" \
  --bind    "$HOME/.cache/llama-cpp-changelog"    "$HOME/.cache/llama-cpp-changelog" \
  --bind    "$HOME/.cache/claude-cli-nodejs" "$HOME/.cache/claude-cli-nodejs" \
  --bind    "$HOME/.cache/pip"               "$HOME/.cache/pip" \
  --bind    "$HOME/.cache/pre-commit"        "$HOME/.cache/pre-commit" \
  --bind    "$HOME/.cache/rattler"           "$HOME/.cache/rattler" \
  --bind    "$HOME/.cache/uv"                "$HOME/.cache/uv" \
  --ro-bind "$_CONDA_PREFIX"                 "$_CONDA_PREFIX" \
  --bind    "$HOME/.claude"                  "$HOME/.claude" \
  --bind    "$HOME/.claude.json"             "$HOME/.claude.json" \
  --ro-bind "$_PIXI_ROOT"                    "$_PIXI_ROOT" \
  $EXTRA_BINDS \
  $WORKTREE_BINDS \
  $GIT_BINDS \
  $HERDR_BINDS \
  $ARGS \
  --die-with-parent \
  --unshare-all --share-net \
  -- claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]}"

