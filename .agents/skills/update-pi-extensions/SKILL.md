---
name: update-pi-extensions
description: Check the latest version of every npm package pinned in the PLUGINS list of pixi-recipes/pi-extensions/recipe.yaml and update the file in-place with any versions that are out of date. Use when asked to "update pi-extensions", "bump pi plugin versions", or "refresh pi-extensions versions".
compatibility: Requires network access to registry.npmjs.org. Designed for the pixi-llm-recipes project.
allowed-tools: WebFetch Read Edit
---

## Steps

1. Read `pixi-recipes/pi-extensions/recipe.yaml`. The pins live in
   `build.script.env.PLUGINS`: a whitespace-separated list of `<package>@<version>`.
   Split each on the **last** `@`, so scoped names survive
   (`@juicesharp/rpiv-advisor@1.18.2` → package `@juicesharp/rpiv-advisor`, version
   `1.18.2`). The recipe's own `package.version` is unrelated — leave it alone.

2. For each package, `GET https://registry.npmjs.org/<package>/latest` and read
   `.version`. On any error, warn and leave that pin untouched.

3. Print a summary table (package / current / latest / update?). If nothing is
   out of date, say so and stop.

4. Edit `recipe.yaml` in place, replacing only the version portion of each stale entry.
   Preserve comments, blank lines, indentation and ordering exactly.

5. Report the bumps, the up-to-date packages, and any whose lookup failed.
