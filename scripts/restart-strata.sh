#!/bin/bash
set -o errexit
set -o pipefail

# Stop the running Strata server and start the named model again. A model name is required;
# start-strata.sh validates it (case-insensitively against strata.ini's sections and aliases)
# before this script touches the old server, so a missing or misspelled name is a no-op.
#
# `--check-profile` runs that validation and the argument parsing without starting anything.
# The arguments are then passed unchanged to both passes of start-strata.sh.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

bash "${SCRIPT_DIR}/start-strata.sh" --check-profile "$@"
bash "${SCRIPT_DIR}/stop-strata.sh"
exec bash "${SCRIPT_DIR}/start-strata.sh" "$@"
