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

rm -rf ~/.pi/agent/npm
if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
  # git-bash can't create symlinks; copy instead
  cp -r "$CONDA_PREFIX/home/.pi/agent/agents" ~/.pi/agent/agents
  cp -r "$CONDA_PREFIX/home/.pi/agent/npm" ~/.pi/agent/npm
  cp -r "$CONDA_PREFIX/home/.pi/agent/skills" ~/.pi/agent/skills
  cp -r "$CONDA_PREFIX/home/.pi/agent/AGENTS.md" ~/.pi/agent/AGENTS.md  
else
  ln -s "$CONDA_PREFIX/home/.pi/agent/agents" ~/.pi/agent/agents
  ln -s "$CONDA_PREFIX/home/.pi/agent/npm" ~/.pi/agent/npm
  ln -s "$CONDA_PREFIX/home/.pi/agent/skills" ~/.pi/agent/skills
  ln -s "$CONDA_PREFIX/home/.pi/agent/AGENTS.md" ~/.pi/agent/AGENTS.md
fi
function cleanup {
  if [[ "$OSTYPE" == msys* || "$OSTYPE" == cygwin* ]]; then
    rsync -avcO --no-perms --no-times ~/.pi/agent/{agents,skills,AGENTS.md} pixi-recipes/pi-skills/
  fi
  rm -rf ~/.pi/agent/{agents,npm,skills,AGENTS.md}
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

cd "$DIR"
pi ${@:2}
