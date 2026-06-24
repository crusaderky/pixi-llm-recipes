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

cd "$DIR"
claude "${@:2}"
