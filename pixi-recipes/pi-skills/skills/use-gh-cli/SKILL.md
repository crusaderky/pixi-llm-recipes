---
name: use-gh-cli
description: Use `gh` CLI for GitHub operations (CI logs, PRs, issues, releases) instead of HTTP web fetch. GitHub API requires authentication; `ollama_web_fetch` returns limited/empty data. The `gh` CLI is pre-authenticated and available in the sandbox.
---

# Use gh CLI for GitHub

## Problem

`ollama_web_fetch` / HTTP GET to `github.com` or `api.github.com` returns empty or limited data — GitHub requires authentication for most API endpoints and even web pages. This means CI logs, PR diffs, issue details, and release assets all fail via plain web fetch.

The `gh` CLI is on `PATH` inside the sandbox, but **only `pixi run pi -- --with-git` (note the `--`) binds auth credentials**. Unsandboxed (`pi-unsafe`) works too.

### First: Check Auth, Stop If Missing

Before any `gh` command, run `gh auth status`. If it fails, **stop immediately** — no workarounds. Tell user:

> `gh` CLI is not authenticated. Restart pi with `pixi run pi <path-to-workspace> -- --with-git` to bind GitHub auth credentials. Re-run with `--with-git` and try again.

Do not fall back to `ollama_web_fetch` or other methods.

## Use `gh` Instead

### CI Logs (most common failure)

**When user pastes a CI log URL** — do NOT use `ollama_web_fetch`. Extract owner/repo, run ID, and optional job ID from the URL, then use `gh`:

| URL                                                          | Pattern                            | `gh` command                          |
| ------------------------------------------------------------ | ---------------------------------- | ------------------------------------- |
| `https://github.com/owner/repo/actions/runs/12345`           | run ID = `12345`                   | `gh run view 12345 --log`             |
| `https://github.com/owner/repo/actions/runs/12345/job/67890` | run ID = `12345`, job ID = `67890` | `gh run view 12345 --log --job 67890` |

Example: user pastes `https://github.com/dask/dask/actions/runs/27794025788/job/82249639708`
→ `gh run view 27794025788 --log --job 82249639708`

For a run-only URL: `https://github.com/dask/dask/actions/runs/27794025788`
→ `gh run view 27794025788 --log`

```bash
# List recent workflow runs for the current repo
gh run list --limit 10

# View failed runs
gh run list --limit 5 --status failure

# View logs for a specific run (interactive viewer)
gh run view <run-id> --log

# Get full log for a specific job
gh run view <run-id> --log --job <job-id>

# Get log as text (pipe to file or grep)
gh run view <run-id> --log > ci.log
```

### PRs and Issues

```bash
gh pr view <number>           # PR details + diff
gh pr view <number> --json    # Full JSON for scripting
gh issue view <number>        # Issue details
gh pr list --state open       # Open PRs
```

### Releases

```bash
gh release view <tag>         # Release details + assets
gh release list --limit 5     # Recent releases
```

### Repo Info

```bash
gh repo view                  # Repo details
gh repo view --json defaultBranch,description,homepageUrl
```

## Notes

- `gh` works in the current directory's repo context. Outside a repo, use `gh <command> --repo <owner>/<repo>`.
- Pipeline: `gh run list --json databaseId --jq '.[0].databaseId'` to extract IDs for scripting.
- For long outputs, redirect to file and read with `read` tool.
