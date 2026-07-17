#!/usr/bin/env bash
# setup.sh — IFBench harness (design doc 07). Idempotent.
#
# Creates an ISOLATED venv (doc 05 §Harness isolation), clones the pinned
# allenai/IFBench, installs it + openai, and pre-fetches the nltk resources the
# verifiers need. IFBench executes NO model code, so no exec sandbox is involved.
set -o errexit
set -o nounset
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

# Pinned upstream commit (recorded in the ledger harness.version).
IFBENCH_REPO="https://github.com/allenai/IFBench"
IFBENCH_SHA="1091c4c3de6c1f6ed12c012ed68f11ea450b0117"

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x "$UV" ]] || { echo "uv not found (install: curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }

# 1) Clone + pin.
if [[ ! -d IFBench/.git ]]; then
    git clone "$IFBENCH_REPO" IFBench
fi
git -C IFBench fetch --depth 1 origin "$IFBENCH_SHA" 2>/dev/null || git -C IFBench fetch origin
git -C IFBench checkout -q "$IFBENCH_SHA"
echo "IFBench pinned at $(git -C IFBench rev-parse --short HEAD)"

# 2) Isolated venv (lean — no torch/vllm; generation is via the OpenAI endpoint).
"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -e ./IFBench openai
# syllapy (an IFBench dep) still imports pkg_resources, dropped by setuptools>=81.
"$UV" pip install --python .venv/bin/python "setuptools<81"

# 3) Pre-fetch nltk resources (punkt/punkt_tab/stopwords/tagger) the verifiers use.
.venv/bin/python -c "import sys; sys.path.insert(0, 'IFBench'); import instructions_util; instructions_util.download_nltk_resources()"

echo
echo "IFBench ready. Run (L2 local):"
echo "  export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local BENCH_MODEL=Qwen3.6-35B-A3B"
echo "  .venv/bin/python run_ifbench.py --arm L2"
