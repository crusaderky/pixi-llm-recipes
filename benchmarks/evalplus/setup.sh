#!/usr/bin/env bash
# setup.sh — EvalPlus harness (design doc 08). Idempotent.
#
# Isolated venv (doc 05 §Harness isolation) with the pinned evalplus package.
# LEAN: the [vllm] extra is NOT installed — we generate via the OpenAI endpoint
# and grade locally through benchmarks/scripts/bench-sandbox.sh (doc 05 D2).
set -o errexit
set -o nounset
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

EVALPLUS_VERSION="0.3.1"   # recorded in the ledger harness.version

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x "$UV" ]] || { echo "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }

"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python "evalplus==${EVALPLUS_VERSION}" openai

echo
echo "EvalPlus ${EVALPLUS_VERSION} ready. Run (L2 local):"
echo "  export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local BENCH_MODEL=Qwen3.6-35B-A3B"
echo "  .venv/bin/python run_evalplus.py --arm L2 --datasets humaneval,mbpp"
echo "Offline grading smoke (no model): .venv/bin/python run_evalplus.py --smoke-grade"
