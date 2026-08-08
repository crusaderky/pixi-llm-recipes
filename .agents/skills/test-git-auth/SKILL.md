---
name: test-git-auth
description: Verify that git push and the gh CLI work (authenticated). Creates a throwaway branch with an empty commit, pushes it, verifies the push, then deletes the branch locally and remotely. Also fetches recent CI run logs via gh to confirm gh auth.
compatibility: Requires a git remote named "origin" and an authenticated gh CLI. Works inside bwrap-claude.sh --with-git or bwrap-pi.sh --with-git.
allowed-tools: Bash
---

Smoke-test that SSH/HTTPS credentials and `gh` are forwarded into the sandbox. Run it
right after launching a sandboxed agent with `--with-git`, before starting real work.

**Never abort early** — run every phase, even after a failure, so the report is complete.

## Phase 0 — pre-flight

Collect all six in one Bash call:

```bash
ls ~/.ssh 2>&1                                     # 1. ~/.ssh bound?
ls ~/.ssh/id_* ~/.ssh/*.pem 2>&1 | grep -v '\.pub' # 2. at least one private key?
echo "SSH_AUTH_SOCK=${SSH_AUTH_SOCK:-<unset>}"; ssh-add -l 2>&1 || true  # 3. agent reachable?
git config --global --list 2>&1 | head -5          # 4. user.name / user.email visible?
which gh && gh --version 2>&1                      # 5. gh on PATH?
gh auth status 2>&1                                # 6. "Logged in to github.com"?
```

Check 3 passes if the socket is reachable, even with no identities loaded. Checks 1, 4
and 6 map directly to missing `--with-git` binds; annotate those failures with
`→ did you forget --with-git?`. Attempt the later phases regardless — a pre-flight
failure may be a false alarm.

## Phase 1 — git push

1. Save the current branch (`git branch --show-current`, or `git rev-parse HEAD` if detached).
2. `BRANCH=test-git-auth-$(date +%s)`
3. `git checkout -b "$BRANCH"`
4. `git commit --allow-empty -m "chore: test-git-auth smoke test — delete me"`
5. `git push origin "$BRANCH"` — on failure, record the error and continue to cleanup.
6. `git ls-remote origin "refs/heads/$BRANCH"` — non-empty means the push landed.

## Phase 2 — cleanup (always)

7. `git checkout <original>` 8. `git branch -D "$BRANCH"` 9. `git push origin --delete "$BRANCH"`
   (skip silently if step 5 failed) 10. re-run `git ls-remote` and confirm it is empty.

## Phase 3 — gh

11. `gh repo view --json nameWithOwner -q .nameWithOwner` — also validates auth.
12. `gh run list --limit 5` — show the output.

## Phase 4 — report

```
## git-auth test results

| # | Check             | Result | Note |
|---|-------------------|--------|------|
| 1 | ~/.ssh visible    | ✓ / ✗  | … |
| 2 | SSH private key   | ✓ / ✗  | … |
| 3 | SSH agent socket  | ✓ / ✗  | … |
| 4 | git global config | ✓ / ✗  | … |
| 5 | gh on PATH        | ✓ / ✗  | … |
| 6 | gh authenticated  | ✓ / ✗  | … |

git push:  <branch> — pushed ✓/✗ — remote deleted ✓/skipped
gh:        <owner>/<repo> — <table from gh run list>
```

If checks 1, 4 or 6 failed, add prominently:

> **Likely cause:** the sandbox was started without `--with-git`. Re-launch with
> `claude --with-git` / `pi --with-git` (or `pixi run <task> <workspace> -- --with-git`).

If `SSH_AUTH_SOCK` is set but points under `/tmp/` and is unreachable, note that the
sandbox only auto-binds `/tmp` sockets detected at launch time — the path outside may
differ from the one inside.
