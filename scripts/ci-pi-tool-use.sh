#!/usr/bin/env bash
# Retry a `pi` invocation until the model under test actually calls the bash
# tool, so the tool-use steps of .github/workflows/pi.yml assert on that rather
# than on one coin flip.
#
# The model is LFM2.5-230M (see the CI section of AGENTS.md): a 230 M model
# chosen because it downloads in seconds, whose tool calls are guesses. On
# run 42830109814 the same prompt made it call `ls` on ubuntu-latest and answer
# "Could you please specify the command you'd like me to run?" on
# ubuntu-24.04-arm, with the sandbox, the wrapper, the git guard and rtk all
# working underneath. What these steps prove is that the chain works when the
# model calls a tool; how often the model calls one is sampling, so the
# assertion stays (the final grep is still a hard failure) and the prompt gets
# re-asked.
#
# Usage: scripts/ci-pi-tool-use.sh <command and its args...>
#
# The command writes pi's output to stdout; it is teed to ./out.txt in the
# current directory, which is also where a previous attempt's out.txt is removed
# from. CI_PI_TOOL_ATTEMPTS overrides the attempt count.

set -uo pipefail

MARKER=${CI_PI_TOOL_MARKER:-hello123}
ATTEMPTS=${CI_PI_TOOL_ATTEMPTS:-5}

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <command...>" >&2
    exit 2
fi

for attempt in $(seq 1 "${ATTEMPTS}"); do
    rm -f out.txt
    # No -e and no check here: a pi that fails is just another attempt without
    # the marker, and the message after the loop says so.
    "$@" | tee out.txt
    if grep -q "${MARKER}" out.txt; then
        echo "tool output seen on attempt ${attempt}/${ATTEMPTS}"
        exit 0
    fi
    echo "attempt ${attempt}/${ATTEMPTS}: no ${MARKER} in pi's output — the model did not call the bash tool" >&2
done

echo "giving up after ${ATTEMPTS} attempts; the model never called the bash tool" >&2
exit 1
