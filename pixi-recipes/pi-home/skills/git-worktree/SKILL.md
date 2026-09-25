---
name: git-worktree
description: Create persistent Git worktrees in the sandbox-safe Herdr worktree directory. Use when a task needs an isolated checkout, branch, or parallel changes.
compatibility: The sandbox binds each project's ~/.herdr/worktrees directory. Worktrees created elsewhere disappear when pi exits.
---

# Git worktrees

Sandbox uses a fresh `/home` tmpfs. Only the current workdir and explicit bind
mounts survive pi shutdown. **Never create a worktree in `/tmp`, another project,
or any other path.** Worktrees must always live under:

```text
~/.herdr/worktrees/<project>/<worktree name>
```

`<project>` is the project directory name. For a checkout at
`/path/to/<project>`, create a worktree with:

```bash
project="$(basename "$PWD")"
worktree_name="feature-example"
branch_name="feature/example"
git worktree add -b "$branch_name" \
  "$HOME/.herdr/worktrees/$project/$worktree_name"
```

`git worktree add` creates the worktree directory. Use an existing branch with
`git worktree add "$HOME/.herdr/worktrees/$project/$worktree_name" "$branch_name"`.
Do not use a path under the current checkout, `/tmp`, or `$HOME` outside the
bound Herdr worktree directory.

## Starting inside a worktree

If pi starts in `~/.herdr/worktrees/<project>/<worktree name>`, that is already
a valid, persistent worktree. Do not nest another worktree inside it. To create
another worktree, make it a sibling:

```text
~/.herdr/worktrees/<project>/<worktree name 2>
```

Derive `<project>` from the first component below
`$HOME/.herdr/worktrees` (do not use the current worktree directory name), then
run the same `git worktree add` command:

```bash
worktree_root="$HOME/.herdr/worktrees"
relative="${PWD#"$worktree_root"/}"
project="${relative%%/*}"
worktree_name="feature-example-2"
branch_name="feature/example-2"
target="$worktree_root/$project/$worktree_name"
case "$target" in
  "$worktree_root/$project"/*) ;;
  *) echo "invalid worktree target" >&2; exit 1 ;;
esac
git worktree add -b "$branch_name" "$target"
```

Keep worktree names and branch names unique. After creation, `cd` into the new
path to work there.

## Persistence check

Before creating anything, verify target starts with the bound directory:

```bash
case "$target" in
  "$HOME/.herdr/worktrees/$project"/*) ;;
  *) echo "refusing worktree outside Herdr worktree directory" >&2; exit 1 ;;
esac
```

Set `target` to the full worktree path before this check. This prevents an
accidental worktree from being lost when the sandbox exits.
