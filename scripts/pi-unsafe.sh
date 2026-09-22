#!/bin/bash
# Run Pi with full access to the whole host
set -o errexit
set -o nounset

if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  # Align bash's ~ with the home dir that pi (node) uses. If HOME is unset,
  # MSYS bash would otherwise default it to <prefix>/Library/home/<user>.
  export HOME="$USERPROFILE"
fi

mkdir -p ~/.pi/agent
rm -rf ~/.pi/agent/{bin,extensions,npm,skills,AGENTS.md,keybindings.json}
rm -f ~/.pi/web-search.json

if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  # git-bash can't create symlinks; copy instead
  # Use backslash path separator for Windows conda prefix
  _WIN_PREFIX="${CONDA_PREFIX//\//\\}"
  _WIN_SRC="${_WIN_PREFIX}/home/.pi/agent"
  for _item in bin extensions npm skills AGENTS.md keybindings.json; do
    # Some items may be absent — copy only what exists.
    if [ -e "${_WIN_SRC}/${_item}" ]; then
      cp -r "${_WIN_SRC}/${_item}" ~/.pi/agent/
    fi
  done
  cp "${_WIN_PREFIX}/home/.pi/web-search.json" ~/.pi/
else
  ln -fs "$CONDA_PREFIX"/home/.pi/agent/{bin,extensions,npm,skills,AGENTS.md,keybindings.json} ~/.pi/agent/
  ln -fs "$CONDA_PREFIX"/home/.pi/web-search.json ~/.pi/
fi

function cleanup {
  # rsync in-session edits back to the recipe for review. rsync is not packaged
  # for Windows, so this is skipped there rather than spamming "command not found".
  if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]] && command -v rsync >/dev/null 2>&1; then
    for _item in bin extensions skills AGENTS.md keybindings.json; do
      # extensions/ may be absent on Windows (see copy step above).
      if [ -e ~/.pi/agent/"$_item" ]; then
        rsync -avcO --no-perms --no-times ~/.pi/agent/"$_item" pixi-recipes/pi-home/
      fi
    done
  fi
  rm -rf ~/.pi/agent/{bin,extensions,npm,skills,AGENTS.md,keybindings.json} ~/.pi/web-search.json
}
trap cleanup EXIT

bash "$(dirname "$0")/inject-pi-extensions.sh"

# Resolve the real pi binary before prepending ~/.local/bin to PATH,
# otherwise the bare `pi` below would resolve to that wrapper and re-enter.
PI_BIN="$(command -v pi)"

# Prepend ~/.local/bin to $PATH so children resolve the naked pi wrapper
# instead of the raw conda binary, while keeping $CONDA_PREFIX/bin
# available so tools with no wrapper (e.g. rtk, gh) still resolve. Stripping
# $CONDA_PREFIX/bin entirely breaks the rtk extension ("rtk binary not found").
if [ -d "$HOME/.local/bin" ]; then
  PATH="$HOME/.local/bin:$PATH"
fi
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

if [ "$1" == "-" ]; then
  echo "Running in empty temporary directory"
  echo "Use \`pixi run pi <directory>\` to move to a specific directory."
  DIR=$(mktemp -d)
  trap "cleanup && rm -rf $DIR" EXIT
else
  DIR="$1"
fi


# Decode forwarded args from env var (base64 encoded, null-separated).
# Avoids pixi shell-parser mangling of single-quote characters.
# Falls back to positional args ($2 onward) when _FWD_ARGS is not set,
# for direct `pixi r pi-unsafe -- <args>` invocations that bypass scripts/pi.
FWD_ARGS=()
if [ -n "${_FWD_ARGS:-}" ]; then
  while IFS= read -r -d '' arg; do
    FWD_ARGS+=("$arg")
  done < <(printf '%s' "$_FWD_ARGS" | base64 -d)
  unset _FWD_ARGS
elif [ $# -ge 2 ]; then
  FWD_ARGS=("${@:2}")
fi

# Consume --no-git; every other forwarded arg goes to pi verbatim.
NO_GIT=false
PI_ARGS=()
for arg in "${FWD_ARGS[@]}"; do
  if [ "$arg" = "--no-git" ]; then
    NO_GIT=true
  else
    PI_ARGS+=("$arg")
  fi
done

# GitHub policy layer — same policy as bwrap-pi.sh, best-effort without the
# sandbox (no read-only mounts exist here to carry the unforgeable marker, so
# $PI_GIT_GUARD is the enforcement channel and a child that resolves git/gh
# outside PATH, e.g. cmd.exe on Windows, sidesteps the wrappers):
# non-destructive by default, everything blocked with --no-git.
_GUARD="$(cd "$(dirname "$0")/git-guards" && pwd)"
PATH="$_GUARD:$PATH"
if [ "$NO_GIT" = true ]; then
  export PI_GIT_GUARD=blocked
  export GIT_ALLOW_PROTOCOL=file
  export GIT_TERMINAL_PROMPT=0
  export GH_CONFIG_DIR="${TMPDIR:-/tmp}/pi-gh-empty"
  mkdir -p "$GH_CONFIG_DIR"
  unset SSH_AUTH_SOCK GH_TOKEN GITHUB_TOKEN
else
  export PI_GIT_GUARD=restricted
  # The hooks/ farm is symlinks; a git-for-windows checkout materialises them as
  # text stubs, so hook injection is Linux-only (Windows is best-effort anyway).
  if [[ "$OSTYPE" != msys* && "$OSTYPE" != cygwin* ]] && [ -d "$_GUARD/hooks" ]; then
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0=core.hooksPath
    export GIT_CONFIG_VALUE_0="$_GUARD/hooks"
  fi
fi
cd "$DIR"
"$PI_BIN" "${PI_ARGS[@]}"
