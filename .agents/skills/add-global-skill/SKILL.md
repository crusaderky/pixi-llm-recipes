---
name: add-global-skill
description: How to add a new pi skill that's available from every workspace. Global skills go in the `pi-skills` conda recipe, not in `~/.agents/skills/` or `~/.pi/agent/skills/`.
---

# Adding a Global Skill

## Rule

To add a skill that should be available from **all workspaces**, put it in the `pi-skills` conda recipe at:

```
pixi-recipes/pi-skills/skills/<skill-name>/SKILL.md
```

Do **not** use `~/.agents/skills/` or `~/.pi/agent/skills/` — those are local to this machine only. Skills in `@pixi-recipes/pi-skills/` are packaged into the conda package and land in `~/.pi/agent/skills/` for every user who installs the `agents` environment.

## Steps

1. Create skill directory:
   ```bash
   mkdir -p pixi-recipes/pi-skills/skills/<skill-name>
   ```

2. Write `SKILL.md` with frontmatter (name + description).

3. Update `AGENTS.md` — add entry in the project tree and file reference table.

4. Rebuild the package:
   ```bash
   pixi lock && pixi install -e agents
   ```

## Structure

```
pixi-recipes/pi-skills/
├── recipe.yaml
├── build.sh
├── build.bat
└── skills/
    └── <skill-name>/
        └── SKILL.md       # Required: frontmatter + instructions
        ├── scripts/        # Optional helper scripts
        └── assets/         # Optional reference files
```

## Discovery

Pi discovers skills at startup from `~/.pi/agent/skills/`. The `pi-skills` build script flat-copies `skills/*` into `$PREFIX/home/.pi/agent/skills/`, so each subdirectory becomes a discoverable skill.

## Existing Global Skills

- `use-gh-cli` — use `gh` CLI instead of web fetch for GitHub operations
