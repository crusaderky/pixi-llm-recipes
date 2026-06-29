---
name: update-pi-extensions
description: Check the latest version of every npm package pinned in the PLUGINS list of pixi-recipes/pi-extensions/recipe.yaml and update the file in-place with any versions that are out of date. Use when asked to "update pi-extensions", "bump pi plugin versions", or "refresh pi-extensions versions".
compatibility: Requires network access to registry.npmjs.org. Designed for the pixi-llm-recipes project.
allowed-tools: WebFetch Read Edit
---

## Steps

### 1. Extract pinned packages from recipe.yaml

Read `pixi-recipes/pi-extensions/recipe.yaml`. The plugin pins live in the
`build.script.env.PLUGINS` value: a space/newline-separated list of
`<package>@<version>` entries.

Also note: the recipe package version is fixed (1) and
separate from the plugin versions.

For each entry, parse:

- **package name** — the part before the last `@` (e.g. `pi-autoresearch`, `@juicesharp/rpiv-advisor`)
- **current version** — the part after the last `@` (e.g. `1.5.0`)

Store them as a list of `(package, current_version)` tuples in the order they appear.

> **Note**: Scoped packages like `@juicesharp/rpiv-advisor` have the scope as part of the package name. The version is the last `@<version>` segment.

### 2. Fetch latest versions from npm registry

For each package, query the npm registry:

```
GET https://registry.npmjs.org/<package>/latest
```

Extract `.version` from the JSON response. If the request fails (404 or other error), skip that package and print a warning — do **not** modify its entry in recipe.yaml.

### 3. Compare and identify updates needed

For each package:

- If `latest_version` == `current_version`: no change needed.
- If `latest_version` != `current_version`: mark this package as **needs update**.

Print a summary table:

```
Package                          Current    Latest   Update?
──────────────────────────────── ───────── ──────── ───────
pi-autoresearch                  1.5.0     1.6.0    YES
@juicesharp/rpiv-advisor         1.18.2    1.18.2   NO
```

If **no packages need updating**, print "All packages are already at their latest version. Nothing to do." and stop.

### 4. Update recipe.yaml

For every package marked **YES**, perform an in-place edit on `pixi-recipes/pi-extensions/recipe.yaml`:

Replace the version in the matching `<package>@<old_version>` entry of the `PLUGINS` list with the new version.

**Example edit** (old text → new text):

```
# Old:
        pi-autoresearch@1.5.0

# New:
        pi-autoresearch@1.6.0
```

Preserve all comments, blank lines, indentation, and ordering exactly as they were. Only change the version number portion.

### 5. Report the result

Print a final summary:

```
Updated pi-extensions recipe.yaml:
  pi-autoresearch:      1.5.0 → 1.6.0
  pi-token-speed:       0.3.1 → 0.4.0

Skipped (no update):
  @juicesharp/rpiv-advisor:       1.18.2 (up to date)
  @juicesharp/rpiv-ask-user-question: 1.18.2 (up to date)

Skipped (fetch failed):
  <some-package>: fetch failed — check network or package name
```
