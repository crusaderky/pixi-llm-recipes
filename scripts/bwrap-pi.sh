#!/bin/bash
# Run Pi with read/write permissions in pwd and no access anywhere else
# Needs an AppArmor profile at /etc/apparmor.d/bwrap; install it with
# `pixi run install-apparmor` (see scripts/install-apparmor.sh).
#
# Usage: bwrap-pi.sh <dir|-> [--with-git] [--bind <dir>] ... [-- pi-args...]
#   --with-git  Bind ~/.ssh, ~/.gitconfig, ~/.config/git, ~/.git-credentials,
#               and ~/.config/gh (rw) so git push and gh commands work.
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

# Parse --bind <dir> pairs and --with-git flag from remaining args
EXTRA_BINDS=""
WITH_GIT=false
PI_ARGS=()
i=2
while [ $i -le $# ]; do
  eval "arg=\${$i}"
  if [ "$arg" = "--bind" ]; then
    eval "bind_dir=\${$((i+1))}"
    ABS_BIND="$(realpath "$bind_dir")"
    EXTRA_BINDS="$EXTRA_BINDS --bind $ABS_BIND $ABS_BIND"
    i=$((i + 2))
  elif [ "$arg" = "--with-git" ]; then
    WITH_GIT=true
    i=$((i + 1))
  else
    PI_ARGS+=("$arg")
    i=$((i + 1))
  fi
done

# --with-git: bind SSH keys, git config, and gh CLI auth into the sandbox (read-only,
# except ~/.config/gh which gh may write token refreshes to).
# The SSH agent socket (SSH_AUTH_SOCK) is accessible via the root bind as long as it
# lives under /run/ (typical for gnome-keyring/systemd). If it's under /tmp, bind it too.
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

_PIXI_ROOT="$(dirname "$(dirname "$PIXI_EXE")")"  # Typically ~/.pixi
_CONDA_PREFIX="$CONDA_PREFIX"
MODELS_JSON="$PWD/models.$PIXI_ENVIRONMENT_NAME.json"

mkdir -p ~/.cache/ccache
mkdir -p ~/.cache/pip
mkdir -p ~/.cache/pre-commit
mkdir -p ~/.cache/rattler
mkdir -p ~/.cache/uv
mkdir -p ~/.pi/agent/sessions
mkdir -p ~/.config/rpiv-web-tools
mkdir -p ~/.config/rtk

for f in auth trust settings; do
  if [ ! -f ~/.pi/agent/$f.json ]; then
    echo "{}" > ~/.pi/agent/$f.json
  fi
done

bash "$(dirname "$0")/inject-pi-extensions.sh"

function cleanup {
  rsync -avcO --no-perms --no-times $_CONDA_PREFIX/home/.pi/agent/{agents,skills,AGENTS.md} pixi-recipes/pi-skills/
}
trap cleanup EXIT

# Unset all PIXI_* and CONDA_* environment variables
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD

bwrap \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --tmpfs /home \
  --tmpfs /root \
  --ro-bind "$_CONDA_PREFIX"              "$_CONDA_PREFIX" \
  --bind "$HOME/.cache/ccache"            "$HOME/.cache/ccache" \
  --bind "$HOME/.cache/pip"               "$HOME/.cache/pip" \
  --bind "$HOME/.cache/pre-commit"        "$HOME/.cache/pre-commit" \
  --bind "$HOME/.cache/rattler"           "$HOME/.cache/rattler" \
  --bind "$HOME/.cache/uv"                "$HOME/.cache/uv" \
  --bind "$HOME/.config/rpiv-web-tools"   "$HOME/.config/rpiv-web-tools" \
  --bind "$HOME/.config/rtk"              "$HOME/.config/rtk" \
  --bind "$_CONDA_PREFIX/home/.pi"        "$HOME/.pi" \
  --bind "$HOME/.pi/agent/auth.json"      "$HOME/.pi/agent/auth.json" \
  --bind "$HOME/.pi/agent/trust.json"     "$HOME/.pi/agent/trust.json" \
  --bind "$HOME/.pi/agent/settings.json"  "$HOME/.pi/agent/settings.json" \
  --bind "$HOME/.pi/agent/sessions"       "$HOME/.pi/agent/sessions" \
  --ro-bind "$_PIXI_ROOT"                 "$_PIXI_ROOT" \
  $EXTRA_BINDS \
  $GIT_BINDS \
  $ARGS \
  --die-with-parent \
  --unshare-all --share-net \
  -- pi "${PI_ARGS[@]}"
