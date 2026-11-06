#!/usr/bin/env bash
# Smoke-test the sandboxed OpenDesign launcher: the whole daemon inside one
# bubblewrap namespace, serving the static UI to this (host) network, with the
# workspace as its only persistent write target. Run on a host with the
# AppArmor profile installed (`pixi r install-apparmor`), as:
#   pixi run -e designer bash scripts/smoke-opendesign-sandbox.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PREFIX="${CONDA_PREFIX:?run through pixi run -e designer so CONDA_PREFIX is set}"
APP="${PREFIX}/opt/opendesign"
PORT="${OD_SMOKE_SANDBOX_PORT:-7466}"
BASE_URL="http://127.0.0.1:${PORT}"
LAUNCHER="scripts/opendesign-bwrap.sh"

[ -x "${PREFIX}/bin/bwrap" ] || { echo "bubblewrap is missing from the designer env" >&2; exit 1; }
[ -f "${APP}/apps/web/out/index.html" ] || { echo "OpenDesign is not installed in ${PREFIX}" >&2; exit 1; }
command -v ss >/dev/null || { echo "smoke test requires ss from iproute2" >&2; exit 1; }
if [ -n "$(ss -ltnH "( sport = :${PORT} )")" ]; then
    echo "TCP port ${PORT} is already in use" >&2
    exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/opendesign-sandbox-smoke.XXXXXX")"
WS="${TMP}/workspace"
mkdir -p "$WS"

pid=""
cleanup() {
    status=$?
    trap - EXIT
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        kill -TERM "${pid}" 2>/dev/null || true
        wait "${pid}" 2>/dev/null || true
    fi
    if [ "${status}" -ne 0 ] && [ -f "${TMP}/launcher.log" ]; then
        echo "sandboxed launcher log (${TMP}/launcher.log):" >&2
        cat "${TMP}/launcher.log" >&2
    fi
    rm -rf "${TMP}"
    exit "${status}"
}
trap cleanup EXIT

# 1. Start the sandboxed daemon. --no-git keeps this host's GitHub credentials
# out of the namespace entirely, so the mount assertions below are deterministic
# whatever the CI runner has configured.
bash "$LAUNCHER" "$WS" --no-git --port "$PORT" >"${TMP}/launcher.log" 2>&1 &
pid=$!

export OD_SMOKE_BASE_URL="$BASE_URL"
"${PREFIX}/bin/node" --input-type=module <<'JS'
const base = process.env.OD_SMOKE_BASE_URL;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function get(path) {
  const response = await fetch(`${base}${path}`, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response;
}

let health;
for (let attempt = 0; attempt < 240; attempt++) {
  try {
    health = await (await get("/api/health")).json();
    break;
  } catch (error) {
    if (attempt === 239) throw error;
    await pause(250);
  }
}
if (health?.ok !== true) throw new Error(`unexpected health response: ${JSON.stringify(health)}`);

const html = await (await get("/")).text();
if (!html.includes("<title>OpenDesign</title>")) throw new Error("static UI title is missing");

const skills = await (await get("/api/skills")).json();
const entries = skills.skills;
if (!Array.isArray(entries) || entries.length === 0) throw new Error("/api/skills: empty skills");
console.log(`sandboxed daemon answered: health ok, UI title ok, ${entries.length} skills`);
JS

# 2. The workspace is the only persistent write target: OpenDesign's SQLite
# database and per-workspace state land in <workspace>/.od on the host.
for path in "$WS/.od/app.sqlite" "$WS/.od/config" "$WS/.od/agent-home"; do
    [ -e "$path" ] || { echo "expected workspace state is missing: $path" >&2; exit 1; }
done

# 3. The daemon listens on the host loopback only.
listeners="$(ss -ltnH "( sport = :${PORT} )")"
[ -n "$listeners" ] || { echo "nothing is listening on port ${PORT}" >&2; exit 1; }
printf 'Listening socket:\n%s\n' "$listeners"
# Column 4 is the local address; the peer's 0.0.0.0:* wildcard is normal.
if grep -Evqx "127\.0\.0\.1:${PORT}" <<<"$(awk '{print $4}' <<<"$listeners")"; then
    echo "daemon is not listening exclusively on 127.0.0.1:${PORT}" >&2
    exit 1
fi

# 4. The namespace contract, read out of the running daemon's mount table.
# ss reports the listener's pid inside the pid namespace, which the host shares.
daemon_pid="$(ss -ltnpH "( sport = :${PORT} )" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
if [ -z "$daemon_pid" ]; then
    echo "could not read the daemon pid from ss -p (needs the same user)" >&2
    exit 1
fi
mounts="/proc/${daemon_pid}/mountinfo"
[ -r "$mounts" ] || { echo "cannot read ${mounts}" >&2; exit 1; }

# mountinfo fields: id parent major:minor root mount-point options [optional...]
# - fstype source super-options. Optional fields shift the separator, so both
# helpers scan for it instead of assuming a column.
fail() { echo "sandbox assertion failed: $*" >&2; exit 1; }
mount_field() { # $1 = mount point, $2 = "options" | "fstype"
    awk -v mp="$1" -v want="$2" '
        $5 != mp { next }
        want == "options" { print $6; exit }
        { for (i = 7; i <= NF; i++) if ($i == "-") { print $(i + 1); exit } }
    ' "$mounts" | tail -1
}

# /run and /home are fresh tmpfs mounts: no host agent, Docker or home state.
[ "$(mount_field /run fstype)" = "tmpfs" ] || fail "host /run is not hidden"
[ "$(mount_field /home fstype)" = "tmpfs" ] || fail "host /home is not hidden"
# The workspace is writable, the designer prefix and the Git guards are not.
case ",$(mount_field "$WS" options)," in
    *,rw,*) ;;
    *) fail "workspace ${WS} is not mounted read-write" ;;
esac
case ",$(mount_field "$PREFIX" options)," in
    *,ro,*) ;;
    *) fail "designer prefix ${PREFIX} is not mounted read-only" ;;
esac
GUARDS="$(cd scripts/git-guards && pwd)"
case ",$(mount_field "$GUARDS" options)," in
    *,ro,*) ;;
    *) fail "Git guards ${GUARDS} are not mounted read-only" ;;
esac
# The packaged Pi tree is the one writable path inside the prefix: Pi writes
# ~/.pi/agent/npm and models-store.json there on every model probe.
case ",$(mount_field "$HOME/.pi" options)," in
    *,rw,*) ;;
    *) fail "the packaged Pi tree $HOME/.pi is not mounted read-write" ;;
esac
# Nothing from the host's $HOME is exposed except the Pi config exceptions and
# (default mode only) the Git/GitHub config. --no-git binds no credential at all.
if [ -e "/proc/${daemon_pid}/root$HOME/.config/gh" ]; then
    fail "--no-git still exposes $HOME/.config/gh"
fi
if [ -e "/proc/${daemon_pid}/root$HOME/.ssh" ]; then
    fail "host ~/.ssh is visible inside the namespace"
fi
# The daemon's own environment: raw packaged Pi, workspace state, no sandbox mode.
envs="/proc/${daemon_pid}/environ"
tr '\0' '\n' <"$envs" >"${TMP}/daemon.env"
grep -qx "PI_BIN=${PREFIX}/bin/pi" "${TMP}/daemon.env" || fail "PI_BIN does not name the packaged pi"
grep -qx "PI_GIT_GUARD=blocked" "${TMP}/daemon.env" || fail "PI_GIT_GUARD is not set to blocked"
grep -qx "OD_DATA_DIR=${WS}/.od" "${TMP}/daemon.env" || fail "OD_DATA_DIR is not the workspace .od"
grep -qx "OD_BIND_HOST=127.0.0.1" "${TMP}/daemon.env" || fail "OD_BIND_HOST is not loopback"
if grep -q '^OD_SANDBOX_MODE=' "${TMP}/daemon.env"; then
    fail "OD_SANDBOX_MODE must stay unset (bubblewrap is the isolation)"
fi

# 5. Ctrl-C: the launcher owns the whole tree, and the port comes back.
kill -INT "$pid"
wait "$pid" || true
pid=""
for _ in $(seq 1 40); do
    [ -z "$(ss -ltnH "( sport = :${PORT} )")" ] && break
    sleep 0.25
done
if [ -n "$(ss -ltnH "( sport = :${PORT} )")" ]; then
    echo "port ${PORT} is still occupied after SIGINT" >&2
    exit 1
fi

echo "Sandboxed OpenDesign smoke test passed; daemon stopped cleanly."
