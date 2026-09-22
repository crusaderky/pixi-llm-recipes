#!/bin/bash
# Set up GitHub groundwork for agent sessions.
#
# The non-destructive policy layer needs no install: it lives in scripts/git-guards/
# (the `git`/`gh` PATH wrappers plus a committed `hooks/` symlink farm over
# hook-dispatch whose pre-push blocks force-push and remote ref deletion), and the
# pi launchers inject it per-session via core.hooksPath.
#
# Groundwork (done once, kept on rerun):
# - gh authentication: `gh auth login` runs ONLY when no token exists at all; a
#   stored-but-broken token gets `gh auth refresh` instead. Rerunning this script
#   never creates a second token or any other duplicate state. On CI (CI or
#   GITHUB_ACTIONS set) both interactive steps are skipped — the device flow has
#   no browser there — and the job is expected to export GH_TOKEN instead.
# - `gh auth setup-git` https credential helper, only when not configured yet.
# - git identity (user.name / user.email), only for the keys still missing,
#   derived from the GitHub profile (noreply address when the profile hides one);
#   keys stay unset when the profile is unreachable (a CI job with no token).
#
# Run via `pixi r install` (or `pixi r install-git`). Idempotent.
set -o errexit
set -o nounset

# gh flipped its storage default: `gh auth login` now stores the token in the
# system credential store (keyring) unless `--insecure-storage` is passed, and
# a keyring token is invisible inside the bwrap sandbox, whose /run tmpfs hides
# the keyring's D-Bus socket. Probe for the flag so the script also works with
# older gh versions, where plain hosts.yml was already the default.
GH_LOGIN_FLAGS=()
if gh help auth login 2> /dev/null | grep -q -- --insecure-storage; then
  GH_LOGIN_FLAGS=(--insecure-storage)
fi

# Interactivity is decided by the CI environment, not by a tty: the device flow
# cannot work without a browser either way, and a human running `pixi r install`
# from a script benefits from the same explicit skip. GitHub Actions sets
# both CI=true and GITHUB_ACTIONS=true; other CI vendors set CI too.
ON_CI=false
if [ "${CI:-false}" = true ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
  ON_CI=true
fi

echo "== GitHub authentication =="
if gh auth status -h github.com > /dev/null 2>&1; then
  echo "Already authenticated to github.com; keeping the existing token."
elif [ "$ON_CI" = true ]; then
  echo "Running on CI: skipping interactive GitHub authentication (the device flow"
  echo "needs a browser). Export GH_TOKEN in the workflow to authenticate."
elif gh auth token -h github.com > /dev/null 2>&1; then
  echo "Stored GitHub token is present but not working; refreshing it (no new token)."
  gh auth refresh -h github.com
else
  echo "No GitHub credentials found; running 'gh auth login' (this creates one token)."
  gh auth login -h github.com "${GH_LOGIN_FLAGS[@]}"
fi

# The token must live in plain hosts.yml storage: gh may keep it in the system
# credential store (the default since the storage flip, or gh auth login
# --secure-storage on older versions), and the bwrap sandbox hides the host's
# /run — the keyring's D-Bus socket goes with it — so sessions see an account
# with no token and every push fails with "could not read Username". Re-store
# the working token in plain storage: no new token, no browser.
HOSTS_FILE="${GH_CONFIG_DIR:-$HOME/.config/gh}/hosts.yml"
if [ -f "$HOSTS_FILE" ] && grep -q 'user:' "$HOSTS_FILE" 2> /dev/null \
   && ! grep -q 'oauth_token' "$HOSTS_FILE" 2> /dev/null; then
  echo "Token lives in the system keyring, which the bwrap sandbox cannot read"
  echo "(/run is hidden); re-storing it in plain hosts.yml storage."
  GH_TOKEN_VALUE="$(gh auth token -h github.com)"
  GH_USER="$(gh api user --jq .login)"
  gh auth logout -h github.com -u "$GH_USER"
  printf '%s\n' "$GH_TOKEN_VALUE" | gh auth login "${GH_LOGIN_FLAGS[@]}" --with-token
  echo "Token re-stored in $HOSTS_FILE."
fi

echo "== git https credential helper =="
if [ "$(git config --global --get credential.https://github.com.helper)" = "!gh auth git-credential" ]; then
  echo "gh credential helper already configured."
else
  gh auth setup-git
  echo "Configured 'gh auth git-credential' as the https credential helper."
fi

echo "== git identity =="
if git config --global --get user.name > /dev/null 2>&1; then
  echo "user.name already set ($(git config --global --get user.name))."
elif gh api user > /dev/null 2>&1; then
  NAME="$(gh api user --jq '.name // .login')"
  git config --global user.name "$NAME"
  echo "Set user.name = $NAME"
else
  echo "GitHub profile unreachable (no usable token); leaving user.name unset."
fi
if git config --global --get user.email > /dev/null 2>&1; then
  echo "user.email already set ($(git config --global --get user.email))."
elif gh api user > /dev/null 2>&1; then
  EMAIL="$(gh api user --jq '.email // empty')"
  if [ -z "$EMAIL" ]; then
    EMAIL="$(gh api user --jq '(.id | tostring) + "+" + .login + "@users.noreply.github.com"')"
  fi
  git config --global user.email "$EMAIL"
  echo "Set user.email = $EMAIL"
else
  echo "GitHub profile unreachable (no usable token); leaving user.email unset."
fi

echo "GitHub groundwork complete (policy hooks need no install: scripts/git-guards/hooks)."
