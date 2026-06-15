#!/usr/bin/env bash
set -o xtrace -o nounset -o pipefail -o errexit

tgz=$(npm pack --ignore-scripts)
npm install -ddd \
    --global \
    ${SRC_DIR}/${tgz}

pnpm install --ignore-scripts
pnpm-licenses generate-disclaimer --prod --output-file=third-party-licenses.txt
