#!/bin/bash
# Run Claude Code with full access to the whole host
set -o errexit
set -o nounset

if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  # Align bash's ~ with the home dir that claude (node) uses.
  export HOME="$USERPROFILE"
fi

# Inject conda-packaged Claude Code extensions (hooks, skills, settings)
bash "$(dirname "$0")/inject-claude-extensions.sh"

if [ "$1" == "-" ]; then
  echo "Running in empty temporary directory"
  echo "Use \`pixi run claude-unsafe <directory>\` to move to a specific directory."
  DIR=$(mktemp -d)
  trap "rm -rf $DIR" EXIT
else
  DIR="$(realpath "$1")"
fi


# Decode forwarded args from env var (base64 encoded, null-separated).
# Avoids pixi shell-parser mangling of single-quote characters.
# Falls back to positional args ($2 onward) when _FWD_ARGS is not set,
# for direct `pixi r claude-unsafe -- <args>` invocations that bypass scripts/claude.
FWD_ARGS=()
if [ -n "${_FWD_ARGS:-}" ]; then
  while IFS= read -r -d '' arg; do
    FWD_ARGS+=("$arg")
  done < <(printf '%s' "$_FWD_ARGS" | base64 -d)
  unset _FWD_ARGS
elif [ $# -ge 2 ]; then
  FWD_ARGS=("${@:2}")
fi

CLAUDE_ARGS=("${FWD_ARGS[@]}")

# Resolve the real claude binary before prepending ~/.local/bin to PATH,
# otherwise the bare `claude` below would resolve to that wrapper and re-enter.
CLAUDE_BIN="$(command -v claude)"

# Prepend ~/.local/bin to $PATH so children resolve the naked pi/claude
# wrappers instead of the raw conda binaries, while keeping $CONDA_PREFIX/bin
# available so tools with no wrapper (e.g. rtk, gh) still resolve. Stripping
# $CONDA_PREFIX/bin entirely breaks extensions/CLIs that only live there.
if [ -d "$HOME/.local/bin" ]; then
  PATH="$HOME/.local/bin:$PATH"
fi

# Unset all PIXI_*/CONDA_* and the pixi-activation env vars so they don't leak
# into Claude Code and its children.
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

cd "$DIR"
"$CLAUDE_BIN" "${CLAUDE_ARGS[@]}"
