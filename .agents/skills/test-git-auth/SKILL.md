---
name: test-git-auth
description: Verify that git push and the gh CLI work (authenticated). Creates a throwaway branch with an empty commit, pushes it, verifies the push, then deletes the branch locally and remotely. Also fetches recent CI run logs via gh to confirm gh auth.
compatibility: Requires git remote named "origin" and gh CLI installed and authenticated. Works inside bwrap-claude.sh --with-git or bwrap-pi.sh --with-git.
allowed-tools: Bash
---

## Purpose

Smoke-test that SSH/HTTPS credentials and the `gh` CLI are properly forwarded into the sandbox. Run this after launching the sandboxed agent with `--with-git` to confirm everything works before starting real work.

## Steps

### Phase 0 — pre-flight checks

Run each check with a single Bash call and collect results before doing anything else. Never abort early — complete all checks so the report is complete.

1. **SSH directory** — check visibility:
   ```bash
   ls ~/.ssh 2>&1
   ```
   - Pass: directory exists and contains at least one file.
   - Fail: `No such file or directory` → `~/.ssh` was not bound into the sandbox.

2. **SSH keys** — check at least one private key is present:
   ```bash
   ls ~/.ssh/id_* ~/.ssh/*.pem 2>&1 | grep -v '.pub'
   ```
   - Pass: one or more key files found.
   - Fail: no private keys → keys exist on the host but none of the standard names were matched, or `~/.ssh` is missing.

3. **SSH agent socket** — check `SSH_AUTH_SOCK` and connectivity:
   ```bash
   echo "SSH_AUTH_SOCK=${SSH_AUTH_SOCK:-<unset>}" && [ -n "${SSH_AUTH_SOCK:-}" ] && ssh-add -l 2>&1 || true
   ```
   - Pass: `SSH_AUTH_SOCK` is set and `ssh-add -l` lists at least one identity (or says "The agent has no identities" — socket reachable but empty is still OK).
   - Fail: variable unset, or socket path not accessible, or `ssh-add` errors with "Could not open a connection".

4. **git global config** — check visibility:
   ```bash
   git config --global --list 2>&1 | head -5
   ```
   - Pass: prints at least `user.name` or `user.email`.
   - Fail: `fatal: unable to read config` or empty output → `~/.gitconfig` / `~/.config/git/config` not bound.

5. **gh CLI binary** — check it is on PATH:
   ```bash
   which gh && gh --version 2>&1
   ```
   - Pass: path printed and version shown.
   - Fail: `not found` → `gh` not installed on the host or its location is not accessible inside the sandbox.

6. **gh auth token** — check authentication state:
   ```bash
   gh auth status 2>&1
   ```
   - Pass: output contains `Logged in to github.com`.
   - Fail: `not logged into` or `No token` → `~/.config/gh` was not bound (missing `--with-git`) or the host is not authenticated.

**After all six checks**, produce a pre-flight table (see Phase 4 report format) and decide whether to continue:
- If checks 1–4 all pass → proceed with git push test.
- If checks 5–6 pass → proceed with gh test.
- If any check fails → still attempt the corresponding test (the error may be a false alarm), but highlight the failure in the report with a `→ did you forget --with-git?` hint for any check that maps directly to a missing bind.

### Phase 1 — git push

1. **Record the starting branch:**
   ```bash
   git branch --show-current
   ```
   Save the output as `ORIGINAL_BRANCH`. If the repo is in detached HEAD state, save the SHA instead (`git rev-parse HEAD`).

2. **Choose a unique branch name** of the form `test-git-auth-<epoch-seconds>`:
   ```bash
   date +%s
   ```
   Combine to get e.g. `test-git-auth-1718123456`.

3. **Create and switch to the new branch:**
   ```bash
   git checkout -b test-git-auth-<timestamp>
   ```

4. **Create an empty commit** (no working-tree changes needed):
   ```bash
   git commit --allow-empty -m "chore: test-git-auth smoke test — delete me"
   ```

5. **Push the branch to origin:**
   ```bash
   git push origin test-git-auth-<timestamp>
   ```
   If this fails, report the error and proceed to cleanup — do not abort.

6. **Confirm the push succeeded** by checking the remote tracking ref:
   ```bash
   git ls-remote origin refs/heads/test-git-auth-<timestamp>
   ```
   A non-empty result means the branch is live on the remote.

### Phase 2 — cleanup

7. **Switch back to the original branch** (or detach to the original SHA):
   ```bash
   git checkout <ORIGINAL_BRANCH>
   ```

8. **Delete the local branch:**
   ```bash
   git branch -D test-git-auth-<timestamp>
   ```

9. **Delete the remote branch:**
   ```bash
   git push origin --delete test-git-auth-<timestamp>
   ```
   If the push in step 5 failed the remote branch does not exist; skip this step silently.

10. **Confirm remote deletion:**
    ```bash
    git ls-remote origin refs/heads/test-git-auth-<timestamp>
    ```
    The output should be empty.

### Phase 3 — gh CLI

11. **Determine the repository slug** from the remote URL:
    ```bash
    gh repo view --json nameWithOwner -q .nameWithOwner
    ```
    This also implicitly validates that `gh` is authenticated; if it fails, report the error and stop.

12. **List the five most recent CI workflow runs** (read-only):
    ```bash
    gh run list --limit 5
    ```
    Show the output to the user — workflow name, status, branch, and elapsed time.

### Phase 4 — report

Summarise the outcome:

```
## git-auth test results

### Pre-flight checks
| # | Check              | Result | Note |
|---|--------------------|--------|------|
| 1 | ~/.ssh visible     | ✓ / ✗  | <detail or "→ did you forget --with-git?"> |
| 2 | SSH private key    | ✓ / ✗  | <filenames found or "none found"> |
| 3 | SSH agent socket   | ✓ / ✗  | <SSH_AUTH_SOCK value or "unset / unreachable"> |
| 4 | git global config  | ✓ / ✗  | <user.name value or "→ did you forget --with-git?"> |
| 5 | gh binary on PATH  | ✓ / ✗  | <version or "not found"> |
| 6 | gh authenticated   | ✓ / ✗  | <account or "→ did you forget --with-git?"> |

### git push
- Branch created:  test-git-auth-<timestamp>
- Push:            ✓ succeeded  /  ✗ failed: <error>
- Remote deleted:  ✓ confirmed  /  ✗ skipped (push failed)

### gh CLI
- Repo:            <owner>/<repo>
- Recent CI runs:
  <table from gh run list>
```

Checks 1, 4, and 6 map directly to missing `--with-git` binds. If any of them fail, add a prominent note:

> **Likely cause:** the sandbox was started without `--with-git`. Re-launch with:
> ```
> pixi run -e claude claude --with-git
> # or
> pixi run -e pi pi <dir> --with-git
> ```

For check 3, if `SSH_AUTH_SOCK` is set but the socket is under `/tmp/` and unreachable, note that `bwrap-claude.sh --with-git` only auto-binds sockets under `/tmp/` when detected at launch time — the socket path visible outside may differ from the one inside the sandbox.
