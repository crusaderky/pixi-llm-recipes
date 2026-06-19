---
name: update-global-skill
description: How to update a global pi skill. Global skills live in `pixi-recipes/pi-skills/skills/`, not in `~/.pi/agent/skills/`. The latter is just the installed copy — always edit the source in the recipe.
compatibility: Designed for the pixi-llm-recipes project.
---

# Update a Global Skill

## Rule

Global skills are defined in the `pi-skills` conda recipe. The installed copy
at `~/.pi/agent/skills/<name>/` is ephemeral — it gets overwritten every time
the `pi-skills` package is rebuilt. **Always edit the source in the recipe.**

Source location:
```
pixi-recipes/pi-skills/skills/<skill-name>/SKILL.md
```

## Steps

### 1. Find the skill source

Regardless of a user saying a skill is at `~/.pi/agent/skills/<name>/` or `~/.agents/skills/<name>/`:

- Check if it exists in `pixi-recipes/pi-skills/skills/<name>/` — that is the canonical source.
- If it's not there, it's a workspace-specific skill in `.agents/skills/<name>/` — edit it directly.

DO NOT update `~/.pi/agent/skills`; it is a volatile directory which will be destroyed at the next cache clear.

### 2. Read the current source

```
read pixi-recipes/pi-skills/skills/<name>/SKILL.md
```

### 3. Apply edits

Edit `SKILL.md` in-place in the recipe, not the installed copy.

### 4. (Optional) Rebuild the package

If you want the change to take effect immediately in the current environment:

```bash
pixi install -e agents
```

This rebuilds the `pi-skills` conda package and re-installs it, replacing
`~/.pi/agent/skills/<name>/` with the updated version.

## Related

See `add-global-skill` skill for how global skills are structured,
how they're packaged, and how they're discovered by pi.
