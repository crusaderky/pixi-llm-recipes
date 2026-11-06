#!/bin/bash
# Print what this designer environment ships and how to run it: the packaged
# OpenDesign daemon and static UI, the Pi it drives, and the v1 support
# boundary. Same spirit as `llama-help` and `strata-help`.
set -o errexit
set -o nounset

if [ -z "${CONDA_PREFIX:-}" ]; then
  echo "opendesign-help.sh: run through the designer pixi environment" >&2
  exit 2
fi
PREFIX="$CONDA_PREFIX"
APP="$PREFIX/opt/opendesign"

DAEMON_VERSION="$(node -e '
const { readFileSync } = require("node:fs");
const pkg = JSON.parse(readFileSync(process.argv[1], "utf8"));
process.stdout.write(pkg.version ?? "unknown");
' "$APP/apps/daemon/package.json")"

cat <<EOF
OpenDesign — local-first AI design workspace (https://opendesigner.io/)

  daemon package   $APP
  daemon version   $DAEMON_VERSION (upstream's own package.json; the release tag is open-design-v<tag>)
  node             $("$PREFIX/bin/node" --version)
  pi               $("$PREFIX/bin/pi" --version 2>/dev/null || echo "not found")

Run it

  pixi r opendesign <workspace>           # foreground, inside a bubblewrap sandbox
  pixi r opendesign <workspace> -- --port 7457
  pixi r opendesign <workspace> -- --no-git     # no GitHub credential at all
  pixi r opendesign <workspace> -- --ro-bind ../reference   # extra input, read-only
  pixi r opendesign -                     # throwaway workspace in a tmpfs
  pixi r opendesign-unsafe <workspace>    # full host access, debugging only

  opendesign                              # same as \`pixi r opendesign\` in the cwd (after \`pixi r install\`)

The UI is served to your own browser on http://127.0.0.1:7456 (loopback only).

What the sandbox gives you

  read-write   the workspace only: projects, .od state, artifacts
  read-only    the designer prefix, the packaged Pi tree, scripts/git-guards,
               ~/.gitconfig, ~/.config/git, ~/.config/gh
  read-write   ~/.pi/agent/{auth,trust,settings,models,caveman}.json and sessions/
               (host Pi login, providers, preferences and resumable sessions)
  hidden       the rest of \$HOME, host /tmp, host /run (ssh-agent and Docker
               sockets), SSH keys and credentials, GPU devices
  network      shared with the host, so model providers, web tools and a local
               llama-server/Strata stay reachable
  state        <workspace>/.od (add it to .gitignore)

The daemon drives the squeezed-in Pi in RPC mode (PI_BIN points at the raw
packaged binary), so it inherits the same non-destructive GitHub policy: reads,
fast-forward pushes, issues/PRs/comments work; force-push, remote branch
deletion and API mutations stay blocked. --no-git is the enforcement of a
credential-free session, not a flag the agent can drop.

Not covered in this environment

  Electron/AppImage packaging, PDF/PPTX/screenshot exports (no headless
  browser), motion/video/browser automation, and third-party marketplace
  plugins. Unsupported paths still run inside the sandbox, and fail only
  because their optional tools are absent.
EOF
