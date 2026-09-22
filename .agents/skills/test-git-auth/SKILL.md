---
name: test-git-auth
description: Verify that git and the gh CLI work (authenticated) under the sandbox's default non-destructive GitHub policy. Leaves zero remote clutter: authenticates with `git push --dry-run`, probes that the policy guard blocks force-push and API mutations, then reads CI via gh.
compatibility: Requires a git remote named "origin" and GitHub setup from `pixi r install` (run it on the host first). Works in the default git-enabled mode. Under `--no-git` every check is expected to FAIL by design — that is the point of the mode.
allowed-tools: Bash
---

Smoke-test that GitHub credentials and the non-destructive policy layer are wired up
correctly inside the session. The push check is `git push --dry-run` on purpose: the
policy blocks deleting remote branches, so a throwaway branch could never be cleaned
up. Dry-run proves authentication and ref resolution with no remote clutter.

**Never abort early** — run every phase, even after a failure, so the report is complete.

## Phase 0 — pre-flight

Collect all in one Bash call:

```bash
echo "git  -> $(command -v git)"; echo "gh   -> $(command -v gh)"  # 1. guard wrappers first on PATH?
git config --global --get user.name; git config --global --get user.email  # 2. identity visible?
git config --get core.hooksPath                               # 3. policy hooks injected?
gh auth status 2>&1                                           # 4. "Logged in to github.com"?
```

Check 1 passes when both resolve into `scripts/git-guards` (the policy wrappers). Check 3
expects `…/scripts/git-guards/hooks` (always injected on Linux; the symlink farm is
skipped on Windows, where the policy is wrapper-only).
Check 4 failure maps to `--no-git` or missing GitHub setup; annotate accordingly and
still attempt the later phases.

## Phase 1 — policy probes (must be blocked)

Both probes are harmless if the policy layer is somehow absent (a dry-run push and a
DELETE against a nonexistent path):

```bash
git push --force --dry-run origin HEAD 2>&1 | head -2   # expect "blocked by the pi non-destructive GitHub policy"
gh api -X DELETE /repos/none/none 2>&1 | head -2        # expect the same from the gh guard
```

Third probe — the policy must not be env-downgradeable. Under `--no-git` both must
STILL be refused (the `/etc/pi-git-policy` marker wins over `$PI_GIT_GUARD`); in the
default mode the results equal probe 1:

```bash
PI_GIT_GUARD=restricted git push --force --dry-run origin HEAD 2>&1 | head -2
PI_GIT_GUARD=restricted gh api -X DELETE /repos/none/none 2>&1 | head -2
```

If a probe reaches the network instead, report the policy layer as MISSING (or, for
the third probe, as ENV-DOWNGRADEABLE).

## Phase 2 — git push authentication

1. `git push --dry-run origin HEAD 2>&1` — expect "Everything up-to-date" or a list of
   ref updates. Failure here is an authentication/remote problem (the point of this skill).
2. Confirm no refs changed: `git status --short`, `git log --oneline -1`.

## Phase 3 — gh

1. `gh repo view --json nameWithOwner -q .nameWithOwner` — also validates auth.
2. `gh run list --limit 5` — show the output.

## Phase 4 — report

```
## git-auth test results

| # | Check                        | Result | Note |
|---|------------------------------|--------|------|
| 1 | guard wrappers on PATH       | ✓ / ✗  | … |
| 2 | git identity                 | ✓ / ✗  | … |
| 3 | policy hooks injected        | ✓ / ✗  | … |
| 4 | gh authenticated             | ✓ / ✗  | … |
| 5 | force-push blocked           | ✓ / ✗  | … |
| 6 | gh api mutation blocked      | ✓ / ✗  | … |
| 7 | env-flip resisted            | ✓ / ✗  | … |
| 8 | git push --dry-run           | ✓ / ✗  | … |
| 9 | remote refs unchanged        | ✓ / ✗  | … |

gh:  <owner>/<repo> — <table from gh run list>
```

If checks 1, 2, 3 or 4 failed, add prominently:

> **Likely cause:** the session was started with `--no-git` (relaunch without it), or the
> one-off GitHub setup has never run — execute `pixi r install` (or `pixi r install-git`)
> on the host and restart the session.

If `SSH_AUTH_SOCK` is set but points under `/tmp/` and is unreachable, note that the
sandbox only auto-binds `/tmp` sockets detected at launch time — the path outside may
differ from the one inside.

Remind the reader of the policy in the report footer: force-push, remote branch/ref
deletion and deleting/modifying GitHub posts are blocked in every mode, with no flag to
re-enable them — such operations belong to the human's own shell.
