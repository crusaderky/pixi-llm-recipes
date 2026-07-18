#!/usr/bin/env bash
# setup.sh — SciCode harness (design docs 01 canonical + 03 pi). Idempotent.
#
# Isolated venv (doc 05 §Harness isolation) with the pinned SciCode repo + its
# inspect_ai integration, and the numeric test data (test_data.h5, ~1 GB) the
# scorer needs. LEAN: SciCode itself is light (numpy/scipy/h5py/inspect-ai);
# no torch/vllm. Generation is via the OpenAI endpoint.
set -o errexit
set -o nounset
set -o pipefail

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

SCICODE_REPO="https://github.com/scicode-bench/SciCode"
SCICODE_SHA="e3158ea011d4235245a547460d3688d7ccbf9900"
H5_SHA256="48b0272a88b17dbd29777c217e1b4fb2b019b92e11cc2add847409db9541b890"
H5_GDRIVE_FOLDER="1W5GZW6_bdiDAiipuFMqdUhvUaHIj6-pR"

UV=$(command -v uv || echo "$HOME/.local/bin/uv")
[[ -x "$UV" ]] || { echo "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }

if [[ ! -d SciCode/.git ]]; then
    git clone "$SCICODE_REPO" SciCode
fi
git -C SciCode fetch --depth 1 origin "$SCICODE_SHA" 2>/dev/null || git -C SciCode fetch origin
git -C SciCode checkout -q "$SCICODE_SHA"
echo "SciCode pinned at $(git -C SciCode rev-parse --short HEAD)"

"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -e ./SciCode inspect-ai openai gdown

# test_data.h5 (~1 GB) — required by the scorer (canonical AND pi arms). NOT
# gated: gdown bypasses the GDrive web sign-in. Skipped if already present with
# the right sha256. This is the one heavy download; comment it out to defer.
H5=SciCode/eval/data/test_data.h5
if [[ -f "$H5" ]] && echo "$H5_SHA256  $H5" | sha256sum -c - >/dev/null 2>&1; then
    echo "test_data.h5 already present (sha256 ok)"
else
    echo "Fetching test_data.h5 (~1 GB) via gdown ..."
    .venv/bin/python -m gdown --folder "https://drive.google.com/drive/folders/${H5_GDRIVE_FOLDER}" -O SciCode/eval/data
    echo "$H5_SHA256  $H5" | sha256sum -c - || { echo "sha256 MISMATCH for test_data.h5"; exit 1; }
fi

echo
echo "SciCode ready. Canonical (doc 01, L2 local):"
echo "  export OPENAI_BASE_URL=http://localhost:8080/v1 OPENAI_API_KEY=sk-local BENCH_MODEL=Qwen3.6-35B-A3B"
echo "  .venv/bin/python run_canonical.py --arm L2"
echo "Dummy plumbing smoke (no model): .venv/bin/python run_canonical.py --mode dummy --limit 1"
