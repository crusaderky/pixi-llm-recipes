#!/bin/bash
set -o errexit
set -o pipefail

# Prepare everything Strata needs before it can start, without starting it: the model
# shards into the shared Hugging Face cache (the same blobs `llama-server -hf` uses), the
# expert pack, the MTP draft layer and the run config, all inside $CONDA_PREFIX. Output goes
# to the terminal and into strata.log in the current directory, where start-strata.sh logs.
#
# What it prepares is whatever strata.ini says right now, so this is also the command that
# applies a change to a flag setup.py only reads when preparing (`kv`, `parallel`, `vision`,
# `low-ram`, ...). `pixi run start-strata` runs the same thing on its first start, and again
# whenever that file moves; running it first only makes it visible.
#
# `--` forwards anything else to Strata's own setup.py, e.g.
# `pixi run strata-install -- --calibrate` or `--keep-mtp-inputs`.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$PWD/strata.log"
echo "Logging to ${LOG}"
python "${SCRIPT_DIR}/strata-run.py" --no-start "$@" 2>&1 | tee -a "${LOG}"
