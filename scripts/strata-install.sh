#!/bin/bash
set -o errexit
set -o pipefail

# Prepare everything Strata needs before it can start, without starting it: the model
# shards into the shared Hugging Face cache (the same blobs `llama-server -hf` uses), the
# expert pack, the MTP draft layer and the run config, all inside $CONDA_PREFIX. Output goes
# to the terminal and into strata.log in the current directory, where start-strata.sh logs.
#
# The first argument names one profile from strata.ini (a section or an alias); it prepares
# only that model. Several profiles share this env, each with its own run config and
# preparation record, so installing another one leaves the earlier models alone.
#
# What it prepares is whatever that profile says right now, so this is also the command that
# applies a change to a flag setup.py only reads when preparing (`kv`, `parallel`, `vision`,
# `low-ram`, ...). `pixi run start-strata <profile>` runs the same thing on its first start,
# and again whenever that profile moves; running it first only makes it visible.
#
# `--` forwards anything else to Strata's own setup.py, e.g.
# `pixi run strata-install QFN -- --calibrate` or `--keep-mtp-inputs`.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$PWD/strata.log"

PROFILE="${STRATA_PROFILE:-}"
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --profile=*)
            PROFILE="${1#*=}"
            shift
            ;;
        *)
            if [[ -z "${PROFILE}" && "$1" != --* ]]; then
                PROFILE="$1"
            else
                ARGS+=("$1")
            fi
            shift
            ;;
    esac
done
export STRATA_PROFILE="${PROFILE}"

echo "Logging to ${LOG}"
python "${SCRIPT_DIR}/strata-run.py" --no-start "${ARGS[@]}" 2>&1 | tee -a "${LOG}"
