---
name: use-gh-cli
description: Use `gh` CLI for GitHub operations (CI logs, PRs, issues, releases) instead of HTTP web fetch. GitHub API requires authentication; `web_fetch` returns limited/empty data. The `gh` CLI is authenticated and available in agent sessions under a non-destructive policy.
---

# Use gh CLI for GitHub

`web_fetch` against `github.com` / `api.github.com` returns empty or truncated data —
GitHub requires auth for most endpoints and even for many web pages. CI logs, PR diffs,
issue details and release assets all fail that way. Use `gh` instead; never fall back to
`web_fetch` for GitHub.

**Before any `gh` command, run `gh auth status`.** If it fails, stop immediately — no
workarounds — and tell the user:

> `gh` is not authenticated. Either this session was started with `--no-git` (relaunch
> without it), or the one-off GitHub setup has never run: execute `pixi r install` (or
> `pixi r install-git`) on the host and restart the session.

## Non-destructive policy

`gh` runs under a guard that allows reads and _creating_ content, and blocks GitHub-side
destruction — with no flag to re-enable it:

- **Allowed**: `gh */view`, `list`, `checks`, `run` logs, `gh api` GET and `gh api graphql`
  queries (inline field values), and creating issues, PRs, comments and releases.
- **Blocked**: deleting or editing posts (including `comment --edit-last/--delete-last`),
  `close`/`reopen`/`lock`/`merge`/`ready`/`pin`/`unpin` and other state changes,
  state-changing reviews (`pr review --approve/--request-changes`), `run cancel`/`rerun`,
  `workflow run`, `release upload --clobber`, repo and admin mutations (secrets,
  variables, deploy keys, settings), `gh auth` state changes, and every raw `gh api`
  mutation (including file-backed bodies: `--input`, `-F key=@file`).

If a command is blocked, do not route around it (no `gh api` tricks, no scripts): report
the block and suggest the user run that one operation from their own shell.

Note: the guard only exists in the sandboxed `pi` sessions. `pi-unsafe` and your own
shell have no GitHub restrictions.

## CI logs

Given a CI URL, extract the run ID and optional job ID rather than fetching the page:

| URL                                                          | `gh` command                          |
| ------------------------------------------------------------ | ------------------------------------- |
| `https://github.com/owner/repo/actions/runs/12345`           | `gh run view 12345 --log`             |
| `https://github.com/owner/repo/actions/runs/12345/job/67890` | `gh run view 12345 --log --job 67890` |

```bash
gh run list --limit 10                    # recent runs
gh run list --limit 5 --status failure    # failures only
gh run view <run-id> --log > ci.log       # redirect long logs, then read the file
```

## Everything else

```bash
gh pr view <number>              # details + diff;  --json for scripting
gh issue view <number>
gh pr list --state open
gh release view <tag>            # details + assets
gh release list --limit 5
gh repo view --json defaultBranch,description,homepageUrl
```

## Notes

- `gh` uses the current directory's repo context; outside a repo pass `--repo <owner>/<repo>`.
- Extract IDs for scripting with `--json`/`--jq`, e.g. `gh run list --json databaseId --jq '.[0].databaseId'`.
