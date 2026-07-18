#!/usr/bin/env bash
# setup.sh — LiveCodeBench harness (design doc 06). Idempotent.
#
# LiveCodeBench is NOT on PyPI (doc 06's `pip install livecodebench` was
# representative); it is the LiveCodeBench/LiveCodeBench repo. We import its
# canonical dataset/prompt/extraction/grading from source (sys.path) and drive
# generation ourselves via the OpenAI endpoint. LEAN: the pinned repo lists
# torch+vllm (for LOCAL inference, which we don't use) and a stale pyext (its
# import is commented out) — we install NEITHER. datasets is pinned <4 because
# LCB's dataset uses a loading script that datasets>=4 dropped.
set -o errexit
set -o nounset
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

LCB_REPO="https://github.com/LiveCodeBench/LiveCodeBench"
LCB_SHA="28fef95ea8c9f7a547c8329f2cd3d32b92c1fa24"

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x "$UV" ]] || { echo "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }

if [[ ! -d LiveCodeBench/.git ]]; then
    git clone "$LCB_REPO" LiveCodeBench
fi
git -C LiveCodeBench fetch --depth 1 origin "$LCB_SHA" 2>/dev/null || git -C LiveCodeBench fetch origin
git -C LiveCodeBench checkout -q "$LCB_SHA"
echo "LiveCodeBench pinned at $(git -C LiveCodeBench rev-parse --short HEAD)"

"$UV" venv --python 3.12 .venv
# Light runtime deps only — NO torch/vllm/pyext. datasets<4 for the loading script.
"$UV" pip install --python .venv/bin/python \
    "datasets>=3.2,<4" openai pebble numpy \
    anthropic cohere google-genai "mistralai==0.4.2" together annotated-types

echo
echo "LiveCodeBench ready. Run (L2 local):"
echo "  export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local BENCH_MODEL=Qwen3.6-35B-A3B"
echo "  .venv/bin/python run_livecodebench.py --arm L2"
echo "Offline grading smoke: .venv/bin/python run_livecodebench.py --smoke-grade"
