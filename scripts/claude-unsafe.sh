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

# Resolve the real claude binary before stripping PATH, otherwise the bare
# `claude` below would resolve to the ~/.local/bin wrapper and re-enter this
# script.
CLAUDE_BIN="$(command -v claude)"

# Remove $CONDA_PREFIX/bin from $PATH so children resolve the ~/.local/bin
# wrappers instead of the raw conda binaries.
if [ -n "${CONDA_PREFIX:-}" ]; then
  _strip=":$CONDA_PREFIX/bin:"
  _path=":$PATH:"
  _path="${_path//"$_strip"/:}"
  _path="${_path#:}"
  PATH="${_path%:}"
  unset _strip _path
fi

cd "$DIR"
"$CLAUDE_BIN" "${CLAUDE_ARGS[@]}"
