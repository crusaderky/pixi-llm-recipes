#!/usr/bin/env bash
# setup.sh — CRUXEval harness (design doc 09). Idempotent.
#
# Isolated venv (doc 05 §Harness isolation) + the pinned facebookresearch/cruxeval
# clone (its prompts.py + data/cruxeval.jsonl are reused; generation is ours via
# the OpenAI endpoint). LEAN: only numpy/tabulate/openai — no torch/vllm.
set -o errexit
set -o nounset
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

CRUX_REPO="https://github.com/facebookresearch/cruxeval"
CRUX_SHA="190faf16d175b5847b0af05d937872b1fb395942"

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x "$UV" ]] || { echo "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }

if [[ ! -d CRUXEval/.git ]]; then
    git clone "$CRUX_REPO" CRUXEval
fi
git -C CRUXEval fetch --depth 1 origin "$CRUX_SHA" 2>/dev/null || git -C CRUXEval fetch origin
git -C CRUXEval checkout -q "$CRUX_SHA"
echo "CRUXEval pinned at $(git -C CRUXEval rev-parse --short HEAD)"

"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python numpy tabulate openai

echo
echo "CRUXEval ready. Run (L2 local):"
echo "  export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local BENCH_MODEL=Qwen3.6-35B-A3B"
echo "  .venv/bin/python run_cruxeval.py --arm L2 --task output_prediction,input_prediction"
echo "Offline smoke: .venv/bin/python run_cruxeval.py --selftest"
