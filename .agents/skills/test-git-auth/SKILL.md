---
name: test-git-auth
description: Verify that git and the gh CLI work (authenticated) under the sandbox's default non-destructive GitHub policy. Leaves zero remote clutter, authenticates with `git push --dry-run`, probes that the policy guard blocks force-push and API mutations, then reads CI via gh.
compatibility: Requires a git remote named "origin" and GitHub setup from `pixi r install` (run it on the host first). Works in the default git-enabled mode. Under `--no-git` the authenticated checks are expected to FAIL by design (no credential is bound) — that is the point of the mode.
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
echo "hooksPath -> ${GIT_CONFIG_VALUE_0:-<unset>}"            # 3. policy hooks injected?
gh auth status 2>&1                                           # 4. "Logged in to github.com"?
```

Check 1 passes when both resolve into `scripts/git-guards` (the policy wrappers). Check
2 must not read `alias.*` keys via `git config`: the guard blocks them. Check 3 expects
`…/scripts/git-guards/hooks` (the policy hooks are injected through the `GIT_CONFIG_*`
environment — the env var is the authoritative check).
Check 4 failure maps to `--no-git` or missing GitHub setup; annotate accordingly and
still attempt the later phases.

## Phase 1 — policy probes (must be blocked)

Both probes are harmless if the policy layer is somehow absent (a dry-run push and a
DELETE against a nonexistent path):

```bash
git push --force --dry-run origin HEAD 2>&1 | head -2   # expect "blocked by the pi non-destructive GitHub policy"
gh api -X DELETE /repos/none/none 2>&1 | head -2        # expect the same from the gh guard
git config alias.probe "push --force" 2>&1 | head -1   # expect the same (alias writes are blocked)
git send-pack --dry-run 2>&1 | head -1                 # expect the same (plumbing push is blocked)
git-send-pack --dry-run 2>&1 | head -1                 # expect the same (dashed binary's stub)
```

Then the env-downgrade probes. In the default mode the wrapper re-injects
`core.hooksPath` on every invocation, so a caller-supplied `GIT_CONFIG_*` cannot drop the
hook and the push must return the same refusal. Under `--no-git` both must STILL be
refused (the guard is `blocked`), though the mode's real enforcement is that no GitHub
credential is bound at all:

```bash
GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null \
  git push --force --dry-run origin HEAD 2>&1 | head -2
PI_GIT_GUARD=restricted gh api -X DELETE /repos/none/none 2>&1 | head -2
```

If a probe reaches the network instead, report the policy layer as MISSING (or, for the
override probe, as ENV-DOWNGRADEABLE).

## Phase 1b — local regression harness (no network, no remote clutter)

The probes above only exercise argv. These cover the routes that once bypassed the guard:
every one must be refused. Runs entirely against a throwaway bare repo in `/tmp`:

```bash
T=$(mktemp -d)
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git init -q --bare "$T/r.git" && git -C "$T/r.git" symbolic-ref HEAD refs/heads/main
git init -q "$T/seed" && (cd "$T/seed"; echo A > f; git add f; git commit -qm A
  git push -q "$T/r.git" HEAD:refs/heads/main)
git -C "$T/r.git" branch side main     # non-current branch, safe to target

# a) force hidden in remote.<name>.push, remote tip never fetched: the pre-push hook
#    must fail closed (no fetch above, on purpose).
git init -q "$T/evil" && (cd "$T/evil"; echo E > e; git add e; git commit -qm E
  git remote add origin "$T/r.git"
  git config remote.origin.push '+refs/heads/main:refs/heads/main'
  git push 2>&1) | tail -1

# b) the same force with the caller rewriting the hook environment: the wrapper
#    re-injects core.hooksPath, so this must be refused too.
(cd "$T/evil"; GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath \
  GIT_CONFIG_VALUE_0=/dev/null git push 2>&1) | tail -1

# c) deletion hidden in remote.<name>.push
git clone -q "$T/r.git" "$T/del" && (cd "$T/del"
  git config remote.origin.push ':refs/heads/side'; git push 2>&1) | tail -1

# d) gh must ignore a caller-supplied config dir (alias expansion bypasses the verb
#    denylist). Expect "unknown command" and no ALIAS-RAN output.
mkdir -p "$T/ghcfg"
printf 'aliases:\n  probe: "!echo ALIAS-RAN"\n' > "$T/ghcfg/config.yml"
GH_CONFIG_DIR="$T/ghcfg" gh probe 2>&1 | tail -1

echo "refs: main=$(git -C "$T/r.git" rev-parse --short main) side=$(git -C "$T/r.git" rev-parse --short side)"
rm -rf "$T"
```

Pass = every step refused and both refs unchanged. Any `+ … (forced update)`,
`- [deleted]`, or `ALIAS-RAN` means the corresponding guard is broken.

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
| 10 | hidden refspec force blocked | ✓ / ✗  | hook fails closed, Phase 1b(a) |
| 11 | hook env override blocked    | ✓ / ✗  | wrapper re-injects, Phase 1b(b) |
| 12 | hidden refspec delete blocked| ✓ / ✗  | Phase 1b(c) |
| 13 | gh config-dir alias ignored  | ✓ / ✗  | Phase 1b(d) |

gh:  <owner>/<repo> — <table from gh run list>
```

If checks 1, 2, 3 or 4 failed, add prominently:

> **Likely cause:** the session was started with `--no-git` (relaunch without it), or the
> one-off GitHub setup has never run — execute `pixi r install` (or `pixi r install-git`)
> on the host and restart the session.

Remind the reader of the policy in the report footer: in the default mode force-push,
remote branch/ref deletion and deleting/modifying GitHub posts are blocked, with no flag
to re-enable them — such operations belong to the human's own shell (or a `--no-git`
session, which has no GitHub credential to do them with).
