#!/bin/bash
set -o errexit
set -o pipefail

# Prepare everything Strata needs before it can start, without starting it: the model
# shards into the shared Hugging Face cache (the Coder's are already there), the expert
# pack, the MTP draft layer and the run config, all inside $CONDA_PREFIX. Output goes to
# the terminal and into strata.log in the current directory, where start-strata.sh logs.
#
# This is what `pixi run start-strata` runs on its first start; running it first only makes
# that visible. `--` forwards anything else to Strata's own setup.py, e.g.
# `pixi run strata-install -- --family qwen --model IQ3_S`.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$PWD/strata.log"
echo "Logging to ${LOG}"
python "${SCRIPT_DIR}/strata-run.py" --no-start "$@" 2>&1 | tee -a "${LOG}"
