#!/usr/bin/env bash
# setup.sh — Terminal-Bench 2.0 harness (design docs 02 canonical + 04 pi). Idempotent.
#
# Installs Harbor (the terminal-bench runner) into an isolated venv and clones
# badlogic's pi-terminal-bench adapter (for the doc-04 pi arms). Docker is
# required at RUN time (doc 00 scope guard: this pulls Docker in ONLY here, never
# into a default pixi env). Terminus 2 ships with Harbor — no separate install.
set -o errexit
set -o nounset
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

ADAPTER_REPO="https://github.com/badlogic/pi-terminal-bench"
ADAPTER_SHA="0074c915dc7d8ceeba5f61b19e7b9aa078564fa3"

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x "$UV" ]] || { echo "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }

"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python harbor
echo "harbor $(.venv/bin/harbor --version 2>&1 | tr -d '\n')"

# doc 04: badlogic pi-terminal-bench adapter (PiAgent). Its README documents a
# Harbor patch + Docker upload_dir workaround written against an OLDER Harbor;
# verify/apply against the installed Harbor before the L4/L5 arms (recorded in
# the ledger). Installed editable so `pi_terminal_bench.pi_agent:PiAgent` imports.
if [[ ! -d pi-terminal-bench/.git ]]; then
    git clone "$ADAPTER_REPO" pi-terminal-bench
fi
git -C pi-terminal-bench fetch --depth 1 origin "$ADAPTER_SHA" 2>/dev/null || git -C pi-terminal-bench fetch origin
git -C pi-terminal-bench checkout -q "$ADAPTER_SHA"
"$UV" pip install --python .venv/bin/python -e ./pi-terminal-bench || \
    echo "WARN: adapter install failed (Harbor version drift) — L4/L5 need reconciliation; L1/L2 unaffected."

echo
echo "Docker required at run time. Verify host<->container networking (doc 02 V2):"
echo "  docker run --rm --add-host host.docker.internal:host-gateway curlimages/curl \\"
echo "    curl -s http://host.docker.internal:8080/health"
echo
echo "Terminal-Bench ready. Validate the L2 job config offline (no Docker):"
echo "  .venv/bin/python run.py --arm L2 --dry"
