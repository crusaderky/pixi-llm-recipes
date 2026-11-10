# Universal Pi directives

## GitHub: never write unless explicitly told to

**NEVER create, edit, or delete any GitHub post or pull request unless the user has
explicitly asked for exactly that action. No exceptions, and no task that merely implies
it counts as asking.**

- A "post" is any GitHub content an agent can write: an issue, a pull request (open,
  edit, comment on, review, close, or merge it), an issue or PR comment, a release or
  its notes, a discussion reply, or any raw-API equivalent of those.
- Implication is not permission. "Fix CI", "ship it", "get this reviewed" or "push and
  report back" authorizes commits and pushes — not a new PR, not a new comment, not a
  reply on someone else's thread.
- Approval given earlier is not approval now. Every write needs its own explicit request
  in the current conversation. If it is not spelled out, ask, and wait for the answer.
- Reads are always allowed: `gh pr view`, `gh run list`, logs, diffs, blame.
- The sandbox's `git`/`gh` guards block destructive paths (force-push, ref deletion,
  close/merge/lock, release edits, API mutations) but deliberately **allow** creating
  issues, PRs and comments. This rule, not the guard, is what keeps those writes from
  happening unprompted.
