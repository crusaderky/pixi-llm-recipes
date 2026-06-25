#!/bin/bash
# Launch `herdr` with `~/.local/bin` *ahead* of the active conda prefix on PATH.
#
# Why: the `herdr` task runs in the `agents` env, so pixi prepends
# `$CONDA_PREFIX/bin` to PATH. That dir contains the raw `pi` and `claude`
# binaries (from pi-coding-agent / @anthropic-ai/claude-code), which then
# shadow the `~/.local/bin/pi` and `~/.local/bin/claude` wrappers installed by
# `pixi r install`. Children spawned inside herdr inherit this PATH, so `pi`
# inside a herdr pane would skip the bubblewrap sandbox and the `--with-git` /
# `--bind` / `--no-sandbox` argument handling that lives in those wrappers.
#
# Reorder so `~/.local/bin` wins, but resolve the real herdr binary *before*
# the reorder, otherwise `exec herdr` would re-enter the `~/.local/bin/herdr`
# symlink (→ scripts/herdr → `pixi r herdr` → this script) forever.
set -o errexit
set -o nounset

HERDR_BIN="$(command -v herdr)"

# Unset all CONDA_* environment variables and remove $CONDA_PREFIX/bin from
# $PATH, so `pi`/`claude` spawned inside herdr resolve the ~/.local/bin
# wrappers (sandbox + arg handling) instead of the raw conda binaries.
if [ -n "${CONDA_PREFIX:-}" ]; then
  _strip=":$CONDA_PREFIX/bin:"
  _path=":$PATH:"
  _path="${_path//"$_strip"/:}"
  _path="${_path#:}"
  PATH="${_path%:}"
  unset _strip _path
fi
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^CONDA_[^=]+')
unset INIT_CWD

exec "$HERDR_BIN" "$@"