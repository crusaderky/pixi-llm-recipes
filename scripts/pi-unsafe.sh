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

rm -rf ~/.pi/agent/{extensions,npm}
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  # git-bash can't create symlinks; copy instead
  cp -r "$CONDA_PREFIX"/home/.pi/agent/{agents,extensions,npm,skills,AGENTS.md,keybindings.json} ~/.pi/agent/
else
  ln -s "$CONDA_PREFIX"/home/.pi/agent/{agents,extensions,npm,skills,AGENTS.md,keybindings.json} ~/.pi/agent/
fi

function cleanup {
  if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
    rsync -avcO --no-perms --no-times ~/.pi/agent/{agents,skills,AGENTS.md,keybindings.json} pixi-recipes/pi-home/
  fi
  rm -rf ~/.pi/agent/{agents,npm,skills,AGENTS.md,keybindings.json}
}
trap cleanup EXIT

bash "$(dirname "$0")/inject-pi-extensions.sh"

# Unset all PIXI_* and CONDA_* environment variables
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD

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

PI_ARGS=("${FWD_ARGS[@]}")
cd "$DIR"
pi "${PI_ARGS[@]}"
