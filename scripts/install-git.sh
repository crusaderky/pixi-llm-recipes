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
#   never creates a second token or any other duplicate state.
# - `gh auth setup-git` https credential helper, only when not configured yet.
# - git identity (user.name / user.email), only for the keys still missing,
#   derived from the GitHub profile (noreply address when the profile hides one).
#
# Run via `pixi r install` (or `pixi r install-git`). Idempotent.
set -o errexit
set -o nounset

echo "== GitHub authentication =="
if gh auth status -h github.com > /dev/null 2>&1; then
  echo "Already authenticated to github.com; keeping the existing token."
elif gh auth token -h github.com > /dev/null 2>&1; then
  echo "Stored GitHub token is present but not working; refreshing it (no new token)."
  gh auth refresh -h github.com
else
  echo "No GitHub credentials found; running 'gh auth login' (this creates one token)."
  gh auth login -h github.com
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
else
  NAME="$(gh api user --jq '.name // .login')"
  git config --global user.name "$NAME"
  echo "Set user.name = $NAME"
fi
if git config --global --get user.email > /dev/null 2>&1; then
  echo "user.email already set ($(git config --global --get user.email))."
else
  EMAIL="$(gh api user --jq '.email // empty')"
  if [ -z "$EMAIL" ]; then
    EMAIL="$(gh api user --jq '(.id | tostring) + "+" + .login + "@users.noreply.github.com"')"
  fi
  git config --global user.email "$EMAIL"
  echo "Set user.email = $EMAIL"
fi

echo "GitHub groundwork complete (policy hooks need no install: scripts/git-guards/hooks)."
