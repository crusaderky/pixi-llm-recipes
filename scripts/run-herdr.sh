#!/bin/bash
# Launch `herdr` with `~/.local/bin` *ahead* of the active conda prefix on PATH.
#
# Why: the `herdr` task runs in the `agents` env, so pixi prepends
# `$CONDA_PREFIX/bin` to PATH. That dir contains the raw `pi` and `claude`
# binaries (from pi-coding-agent / @anthropic-ai/claude-code), which then
# shadow the `~/.local/bin/pi` and `~/.local/bin/claude` wrappers installed by
# `pixi r install`. Children spawned inside herdr inherit this PATH, so `pi`
# inside a herdr pane would skip the bubblewrap sandbox and the `--with-git` /
# `--bind` / `--no-sandbox` argument handling that lives in
# those wrappers.
#
# Reorder so `~/.local/bin` wins, but resolve the real herdr binary *before*
# the reorder, otherwise `exec herdr` would re-enter the `~/.local/bin/herdr`
# symlink (→ scripts/herdr → `pixi r herdr` → this script) forever.
set -o errexit
set -o nounset

# Register the conda-packaged herdr-file-viewer plugin (if installed in this
# env) so herdr discovers it on launch. Runs before $CONDA_PREFIX is stripped.
if [ -n "${CONDA_PREFIX:-}" ] && [ -x "${CONDA_PREFIX}/bin/herdr" ]; then
    bash "$(dirname "${BASH_SOURCE[0]}")/inject-herdr-file-viewer.sh" || true
fi

HERDR_BIN="$(command -v herdr)"

# Remove $CONDA_PREFIX/bin from $PATH so `pi`/`claude` spawned inside herdr
# resolve the ~/.local/bin wrappers (sandbox + arg handling) instead of the raw
# conda binaries.
if [ -n "${CONDA_PREFIX:-}" ]; then
  _strip=":$CONDA_PREFIX/bin:"
  _path=":$PATH:"
  _path="${_path//"$_strip"/:}"
  _path="${_path#:}"
  PATH="${_path%:}"
  unset _strip _path
fi
# Unset all PIXI_*/CONDA_* and the pixi-activation env vars so herdr panes (which
# inherit this environment) don't leak the pixi env into shells run inside them.
while IFS= read -r var; do
  unset "$var"
done < <(env | grep -oE '^(PIXI_|CONDA_)[^=]+')
unset INIT_CWD XML_CATALOG_FILES GSETTINGS_SCHEMA_DIR

# Launch herdr from $HOME so panes it spawns start in ~ rather than in the repo
# root (the cwd of `pixi r herdr`). herdr uses its own launch cwd as the default
# working directory for new terminals (exported as HERDR_STARTUP_CWD).
cd "$HOME"

exec "$HERDR_BIN" "$@"