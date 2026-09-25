---
name: autoreview
description: Review a user-selected GitHub pull request or local branch, fix findings in new AUTOREVIEW commits, run tests, push safely, and monitor CI. Use when the user explicitly asks for autoreview, optionally with a PR URL or number, branch, base ref, or review scope.
compatibility: Requires a GitHub repository, authenticated gh CLI, origin remote, local unit-test command, and permission to push target branch. Designed for pi's non-destructive GitHub policy; editing an existing PR body may be blocked by that policy.
---

# Autoreview

Review and improve code; do not merely report findings. This workflow writes commits,
pushes them, may create a draft PR, and waits for CI. Follow every checkpoint exactly.

Read and follow `use-gh-cli` before using `gh`. Read and follow `git-worktree` before
creating a worktree. Run `gh auth status` before first `gh` command; stop if it fails.
Use `gh` for GitHub reads. Never post a review, review comment, issue comment, or other
comment on an existing PR.

Record original working directory so it can be restored during cleanup.

## 1. Select target and base

Accept an explicit PR, branch, base ref, or scope from the user.

### Pull request supplied

Read complete PR context before changing anything:

- title, body, author, base and head refs, repository, and current state;
- issue comments, reviews, inline review comments, and requested changes;
- linked issues or PRs mentioned in that context;
- failing or pending checks when they already exist.

Follow explicit links to issues. Open each linked issue, then recursively follow its
explicit issue links until no new issue remains. Track visited issue numbers or URLs to
avoid cycles. Separate required behavior from suggestions and stale discussion.

The PR should have a matching local branch. A local branch may be created from the exact
`origin/<head branch>` when that remote branch exists. If neither matching local nor
remote branch exists, stop and ask; never guess a similarly named branch. If the PR head
belongs to a fork rather than `origin`, stop because pushing `origin/<head>` will not
update that PR.

### No pull request supplied

Use current branch. If it is `main`, `master`, or `staging`, stop and ask what change
should be reviewed. Otherwise assume current branch is target.

Choose review base in this order:

1. explicit base from user;
2. nonstandard PR base, when one exists and clearly owns this branch;
3. `staging`, when current branch descends from local or remote `staging`;
4. repository's `main` or `master`;
5. stop and ask when no base is unambiguous.

In a `main`/`master` plus `staging` workflow, prefer `staging` when it is an ancestor.
Use `origin/staging` when it exists; do not review a stale local staging ref. This is
intentional: `staging...HEAD` contains only feature-branch changes and excludes
staging-only commits. Never review or report `main...staging` as part of this work.

Fetch current remote refs before finalizing base and branch. Record base ref, target
branch, local branch ref, remote branch ref, and merge base. Define `TARGET_REF` as exact
ref whose commits will be reviewed:

- use local branch when it contains remote branch;
- use `origin/<target branch>` when remote branch contains local branch;
- use local branch when no remote counterpart exists;
- stop if local and remote branches have diverged.

## 2. Checkpoint — verify commit ownership

**STOP before creating a worktree or writing anything if this check fails.**

List every commit in `BASE..TARGET_REF` with author name and email. Compare it with local
Git identity from `git config user.name` and `git config user.email`. Continue only when
every reviewed commit belongs to same local user. Treat matching normalized email as
identity; do not fail only because display name changed.

If any author differs, stop and ask:

> Commits to review are authored by `<name> <email>`, but local Git identity is
> `<name> <email>`. Do you want me to write to this branch?

Do not modify authors, commits, or branches while waiting for an answer. Do not treat
co-author trailers as commit authors unless they are also commit authors.

## 3. Create isolated worktree

Use `git-worktree`. Never force-add a branch already checked out elsewhere.

- If target branch is already in a worktree, reuse it only when clean.
- Otherwise create worktree under
  `~/.herdr/worktrees/<project>/autoreview-<sanitized-branch>`.
- Add existing local branch to new worktree, or create it from exact
  `origin/<target branch>` when local branch is absent.
- Never use `git worktree add --force`.
- If branch is behind its remote, update it with fast-forward only. If it has diverged,
  stopped, dirty, or uncertain state, stop and ask.
- Work only from new or reused worktree after this point.

After any fast-forward update, rerun ownership check against final worktree `HEAD`. Its
reviewed commit set must still pass before writing anything.

## 4. Review only target delta

Use merge-base diff for code and one-sided log for commits:

```bash
git log --oneline --decorate "BASE..HEAD"
git diff --stat "BASE...HEAD"
git diff --check "BASE...HEAD"
git diff "BASE...HEAD"
```

Read changed files and enough surrounding code, tests, configuration, and call sites to
understand behavior. Do not review base-only commits. Do not broaden scope to unrelated
repository cleanup.

Check at least:

- **Correctness:** does change solve original issue and satisfy linked context?
- **Regressions:** does it break contracts, edge cases, error paths, concurrency,
  persistence, compatibility, or existing behavior?
- **Maintainability:** clarity, cohesion, duplication, naming, dead code, and unnecessary
  complexity?
- **Tests:** are behavior and failure modes covered; are tests deterministic; can weak or
  redundant tests be compacted or pruned?
- **Performance:** meaningful hot paths, allocations, I/O, algorithms, or regressions?
- **Security and operations:** trust boundaries, input validation, secrets, migrations,
  observability, build/release behavior, and rollback safety as applicable.

Add project-specific checks when repository instructions or changed code justify them.

Run or add benchmarks **only when meaningful performance impact is plausible**. Explain
which change creates that concern. When uncertain, rerun an existing benchmark. Do not
run or add benchmarks for changes with no plausible performance impact.

## 5. Fix findings in new commits

For each actionable finding, implement smallest complete fix. Add or adjust tests when
behavior warrants it. If finding is ambiguous, unsafe to fix automatically, or unrelated
to target delta, stop and report it instead of guessing.

Create new commits; never amend, rebase, squash, reset, or rewrite existing history. Keep
one logical finding per commit unless fixes cannot be separated meaningfully.

Every new commit title must use exactly:

```text
AUTOREVIEW <nitpick|minor|major|critical> - <short title>
```

Commit body must explain issue and why it matters. It may also explain fix. Example:

```text
AUTOREVIEW minor - reject empty cache keys

An empty key bypassed normalization and could select an unrelated cache entry.
Validate it before lookup so malformed callers fail explicitly.
```

Use severity consistently:

- `critical`: likely security breach, data loss, or severe release/build failure;
- `major`: substantive correctness bug, regression, or broken public contract;
- `minor`: bounded bug, important missing test, or maintainability problem with real cost;
- `nitpick`: non-functional clarity or consistency improvement.

Do not inflate severity. Map every new commit to its finding in final report.

## 6. Test before push

Read repository instructions and discover intended test commands. After creating fix
commits:

1. Run focused tests for changed behavior.
2. Run full relevant unit-test suite.
3. Run required lint, formatting, type-check, build, or other repository checks.
4. Run performance benchmarks only under Section 4's condition.
5. Ensure `git status --short` is clean.

If testing reveals another fix, create another `AUTOREVIEW` commit and rerun affected and
full checks. Do not push while relevant tests fail. Do not hide a pre-existing or
environment failure; report evidence and ask how to proceed. If repository has no unit
suite, say so and ask whether available checks are sufficient before pushing.

Push only after checks pass:

```bash
git push origin "HEAD:refs/heads/<target-branch>"
```

Push must be fast-forward. Never use `--force`, `--force-with-lease`, delete a remote ref,
or route around policy. If push is rejected or remote advances, stop and ask; do not
rewrite history automatically.

## 7. Handle pull request

### Existing PR

After push, wait for CI to finish. Use `gh pr checks <number> --watch` or equivalent
read-only `gh` commands. Inspect failed job logs before changing code.

For CI failures caused by reviewed branch:

1. identify cause;
2. create new `AUTOREVIEW` commit with required title and explanatory body;
3. rerun local checks;
4. push fast-forward;
5. wait for new CI run.

Continue follow-up commits and pushes until all CI checks reported for PR are green. If
PR has no checks, report that fact rather than claiming CI is green. Stop and report for
infrastructure failure, flaky test requiring product decision, permissions failure, or
any action blocked by policy. Never use `gh run rerun` to evade this workflow.

### PR body wording

Only edit PR body when it **explicitly states** that PR is AI-generated. Do not infer this
from commit messages, comments, author, or prior edits. Do not edit any PR comment that
lacks explicit AI-generated wording.

For eligible body, make concise human-readable rewrite. Preserve issue links, important
context, behavior, testing claims, and AI disclosure. Remove obvious status such as
“CI is all green” when GitHub already shows it. If existing wording is already concise,
do not edit.

Use `gh pr edit <number> --body-file <file>`. Under sandbox policy this edit may be
blocked. If blocked, do not work around guard; report that user must apply body edit and
continue code/CI work.

### No PR

After successful local checks and push, **STOP and ask user whether to open new draft
PR**. Do not infer approval.

If user approves:

- create PR with `gh pr create --draft`;
- target selected review base;
- give clear, accurate title and concise body;
- begin body with prominent statement such as
  `AI-generated draft PR: prepared by an automated review agent.`;
- do not claim human authorship or manual verification.

If user declines, continue to cleanup.

## 8. Cleanup

Do not remove worktree while waiting for user's draft-PR decision. After final requested
action:

1. return to original working directory;
2. verify worktree has no uncommitted or untracked files;
3. remove worktree with `git worktree remove <path>`;
4. keep local branch; never delete it;
5. verify branch still exists and report final state.

If worktree is dirty or removal fails, do not discard files. Report exact blocker.

## 9. Report

Include:

- PR URL/number or target branch;
- selected base and merge base;
- commits reviewed;
- findings by severity and matching `AUTOREVIEW` commit for each;
- tests and checks run, with results;
- benchmark decision and reason;
- push result and final remote SHA;
- CI result, or exact blocker;
- PR body edit result: updated, already concise, ineligible, or policy-blocked;
- draft PR decision and URL, if applicable;
- worktree removal result and confirmation that local branch was kept.

Never say complete while tests, required CI, ownership confirmation, or user decision is
still pending.
