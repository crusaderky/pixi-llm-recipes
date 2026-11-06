---
name: update-opendesigner
description: Update the opendesigner conda recipe to the latest OpenDesigner release — bump the source tag pin and its archive SHA-256, re-sync the pnpm pin the lockfile demands, re-validate the bundled resource trees and the native/Node-24 boundary, rebuild the designer env and run the empty-environment smoke test. Use when asked to "update opendesigner", "bump OpenDesigner", "update the OpenDesigner recipe", or "update the designer environment".
compatibility: Requires network access to github.com (git ls-remote, gh api) and codeload.github.com (the source archive, for its SHA-256). Rebuild and smoke test need Linux x86-64 and `ss` from iproute2. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

`pixi-recipes/opendesigner/` is a **source** recipe. It unpacks a release tag's tarball, runs
`pnpm install --frozen-lockfile`, builds `@open-design/daemon` and the static Next.js export,
compiles `better-sqlite3` from pinned source with the conda toolchain, and installs the daemon,
`apps/web/out` and ten bundled resource trees under `${PREFIX}/opt/opendesigner` (960 MiB at
`open-design-v0.27.0`). That is upstream's own `deploy/Dockerfile` reproduced without Docker and
without Electron, so **a version bump moves the build script's assumptions, not just a string**.
Node 24 is the only runtime dependency; there is no `pnpm` at runtime and nothing is downloaded
when `od` starts.

## What a bump touches

| pin                                   | where                                                 | new value from                                                                                                                             |
| ------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `version`                             | `context:` in `pixi-recipes/opendesigner/recipe.yaml` | the newest `open-design-v<version>` tag, prefix stripped — **never** the root `package.json` `version` (which is `0.25.1` at tag `0.27.0`) |
| `sha256`                              | `source:`                                             | `sha256sum` of the archive at exactly the `source:` URL, computed after downloading                                                        |
| `pnpm_version`                        | `context:`                                            | root `package.json` `packageManager` **at that tag** (must also satisfy that tag's `engines.pnpm` range)                                   |
| `upstream_reported_version`           | the commented line in `context:`                      | root `package.json` `version` at that tag (documents the tag↔version mapping)                                                              |
| `nodejs >=24,<25` (build **and** run) | `requirements:`                                       | root, `apps/daemon` and `apps/web` `engines.node` (`~24` at the current pin)                                                               |
| `RESOURCE_TREES` in `build.sh`        | only if Phase 2 shows the tree list moved             | upstream `tools/pack/src/resources/index.ts` → `BUNDLED_RESOURCE_TREES`                                                                    |

Leave alone: `build.number` (stays `0`), `package.name`, `source.target_directory`, the
`build.skip` platform gate, `license_file`, and the `designer` environment's feature list in
`pixi.toml`.

**Guardrails — violating any item means STOP, not update:**

> ⚠ **Tags only, from `refs/tags/`, `open-design-v*` only.** Never `main`, never a commit SHA,
> never a tag read off a release title, and never the marketing version in `package.json` as the
> conda `version:`. The candidate must be strictly newer than the current pin, and `prerelease`
> / `draft` releases are skipped unless the current pin is itself one.

> ⚠ **The SHA-256 is computed, never copied.** Download the archive from the exact URL the recipe
> fetches and hash it. Release bodies and third-party mirrors do not carry GitHub's archive
> digest, and GitHub regenerates these tarballs — a stale digest silently fails the build, a
> guessed one silently packages something nobody reviewed.

> ⚠ **`pnpm_version` comes from the same tag, never independently.** `pnpm install
> --frozen-lockfile` accepts only the `packageManager` pnpm that matches the committed
> `pnpm-lock.yaml`. Bumping it alone, or keeping the old one because the recipe "works locally",
> breaks the next clean build.

> ⚠ **Never weaken a tripwire to make a build pass.** The recipe fails on purpose at: the
> resource-tree count cross-check against `BUNDLED_RESOURCE_TREES`, the empty-installed-tree
> check, the `require("better-sqlite3")` ABI probe run after all cleanup, and the smoke test's
> "no pnpm / node-gyp / gcc / make / python in the prefix" assertion. Each one exists because the
> failure mode it catches is silent. Fix the recipe to match upstream; report the drift.

> ⚠ **A version bump changes pins, not code.** Do not touch `build.sh`,
> `scripts/smoke-opendesigner.sh` or `pixi.toml` unless Phase 2/4/5 shows a real interface break,
> and say what broke before rewriting anything.

> ⚠ **`better-sqlite3` stays compiled from pinned source.** The
> `npm_config_build_from_source=true pnpm --filter @open-design/daemon rebuild better-sqlite3`
> step is deliberate: `prebuild-install`'s Debian tarball is not covered by the lockfile's
> integrity hash. Never "fix" a link error by dropping that step, and never export
> `npm_config_build_from_source` globally — `sharp` treats any non-empty value as a request to
> compile itself too.

## Phase 1 — the target version

```bash
git ls-remote --tags --refs https://github.com/nexu-io/open-design.git \
  | awk '$2 ~ /^refs\/tags\/open-design-v/ { sub("^refs/tags/", "", $2); sub("^open-design-v", "", $2); print $2 }' \
  | sort -V | tail -8
gh api repos/nexu-io/open-design/releases \
  --jq '.[0:6][] | "\(.tag_name) prerelease=\(.prerelease) draft=\(.draft) published=\(.published_at)"'
```

Then read the release notes of **every** version between the current pin and the candidate — not
just the candidate's. What matters for this recipe:

- a new or renamed **bundled resource root** (`BUNDLED_RESOURCE_TREES`);
- a **Node or pnpm** bump (`engines.node`, `packageManager`);
- a new **native module** (a second `.node` addon, or a package shipping `prebuilds/` or
  `bin/napi-v3/` payload that has to be trimmed or probed);
- a change to **`deploy/Dockerfile`**'s build order or cleanup list — `build.sh` mirrors it;
- a change to the **daemon's on-disk layout** (`bin/od.mjs`, `dist/`, `apps/web/out`) or to the
  `OD_*` names the packaged tree has to satisfy (`OD_RESOURCE_ROOT`, `OD_DATA_DIR`).

## Phase 2 — fetch the tag and derive every pin

```bash
V=open-design-v0.28.0                                    # the Phase 1 candidate
REPO=$PWD                                                # the repo root; Phase 5 returns here
W=/tmp/od-update; rm -rf "$W"; mkdir -p "$W"; cd "$W"
URL=https://github.com/nexu-io/open-design/archive/refs/tags/$V.tar.gz
curl -fsSL -o src.tar.gz "$URL" && sha256sum src.tar.gz  # → recipe.yaml `sha256`
tar xzf src.tar.gz && SRC="$PWD/open-design-$V"          # GitHub prefixes the dir with the repo name
node -e 'const p=require(process.argv[1]+"/package.json");
  console.log("version:", p.version, "| packageManager:", p.packageManager, "| engines:", JSON.stringify(p.engines))' "$SRC"
node -e 'const d=require(process.argv[1]+"/apps/daemon/package.json");
  console.log("engines:", JSON.stringify(d.engines), "| better-sqlite3:", d.dependencies["better-sqlite3"], "| node-pty:", d.dependencies["node-pty"])' "$SRC"
sed -n '/const BUNDLED_RESOURCE_TREES = \[/,/^] as const;/p' "$SRC/tools/pack/src/resources/index.ts"
```

Diff what actually moved between the two tags instead of eyeballing the release page — the archive
is ~190 MB, so pull the individual files this recipe's assumptions live in rather than extracting
the previous tag too:

```bash
RAW=https://raw.githubusercontent.com/nexu-io/open-design
OLD=open-design-v0.27.0                              # the currently pinned tag
for f in package.json deploy/Dockerfile tools/pack/src/resources/index.ts \
         apps/daemon/package.json apps/web/app/layout.tsx \
         apps/daemon/src/daemon-paths.ts apps/daemon/src/runtimes/defs/pi.ts; do
    echo "### $f"
    diff -u <(curl -fsSL "$RAW/$OLD/$f") <(curl -fsSL "$RAW/$V/$f") | head -40
done
```

`apps/daemon/src/server.ts` (~850 KB) is not in that loop: the checklist below is what proves its
contract (`STATIC_DIR`, `OD_DATA_DIR`, `PLUGIN_PREVIEWS_ROUTE`, the `/api/*` routes the smoke test
hits) still holds, and the release notes are what explain anything that moved.

Then run the **interface checklist** against the extracted tag. These are the exact names
`build.sh` and `scripts/smoke-opendesigner.sh` depend on; anything it prints is a real break to be
fixed in Phase 4, not something to hope the build survives:

```bash
while read -r file pattern; do
  [ -z "$file" ] && continue
  grep -qF -- "$pattern" "$SRC/$file" || echo "MISSING: $file <- $pattern"
done <<'EOF'
package.json                                  "packageManager": "pnpm@
package.json                                  "node": "~24"
apps/daemon/package.json                      "better-sqlite3"
apps/daemon/package.json                      "node-pty"
apps/daemon/bin/od.mjs                        ../dist/cli.js
apps/daemon/src/daemon-paths.ts               resolveDaemonResourceDir
apps/daemon/src/daemon-paths.ts               resolveDaemonPluginPreviewsDir
apps/daemon/src/daemon-paths.ts               OD_RESOURCE_ROOT
apps/daemon/src/server.ts                     OD_DATA_DIR
apps/daemon/src/server.ts                      'apps', 'web', 'out'
apps/daemon/src/server.ts                     PLUGIN_PREVIEWS_ROUTE
apps/daemon/src/server.ts                     app.get('/api/health'
apps/daemon/src/cli.ts                        OD_PORT
apps/daemon/src/runtimes/defs/pi.ts           --mode
apps/web/app/layout.tsx                       title: 'OpenDesign'
deploy/Dockerfile                             --frozen-lockfile
deploy/Dockerfile                             deploy --legacy --prod
deploy/Dockerfile                             better-sqlite3/deps
tools/pack/src/resources/index.ts             const BUNDLED_RESOURCE_TREES = [
tools/pack/src/resources/index.ts             ] as const;
EOF
# Upstream tree count vs what build.sh installs (the build asserts these are equal):
sed -n '/const BUNDLED_RESOURCE_TREES = \[/,/^] as const;/p' \
  "$SRC/tools/pack/src/resources/index.ts" | grep -c '{ from:'
sed -n '/declare -a RESOURCE_TREES=(/,/^)/p' \
  "$REPO/pixi-recipes/opendesigner/build.sh" | grep -cE '^\s+"[^"]+:[^"]+"$'
```

`runtimes/defs/pi.ts` matters beyond the checklist: that is how OpenDesigner runs the agent — it
probes with `pi --version` + `pi --list-models` and drives the conversation with `pi --mode rpc`.
If that file changes, the `pi --list-models` line in
`.github/workflows/opendesigner.yml` and in Phase 5 still proves the wiring, so read the diff
rather than assuming it holds.

## Phase 3 — edit `recipe.yaml`

`version:` (no prefix), `sha256:`, `pnpm_version:` if it moved, the `nodejs >=X,<Y` range in **both**
`requirements.build` and `requirements.run` if `engines.node` moved, and refresh the commented
`upstream_reported_version:` line so the tag↔version mapping stays documented rather than
re-derived. Keep every comment and the field order.

`pixi.lock` does **not** move for a path-recipe pin bump — verified: changing `version:` leaves
`conda_source: opendesigner[cfc7824e] @ pixi-recipes/opendesigner` byte-identical, and `pixi lock`
answers "already up-to-date". Only a `pixi.toml` change re-locks, and then do it with the pixi
version CI pins (`.github/workflows/*.yml`: `v0.81.0`) — a newer local pixi rewrites the whole
lockfile, which the last lockfile regeneration had to undo.

## Phase 4 — `build.sh`, only where the interface moved

| upstream name (at the tag)                                                       | what must follow in this recipe                                        |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `BUNDLED_RESOURCE_TREES` `from`→`to` pairs (`tools/pack/src/resources/index.ts`) | the `RESOURCE_TREES` array; the count check fails the build            |
| `resolveDaemonResourceDir` / `OD_RESOURCE_ROOT` (`daemon-paths.ts`, `server.ts`) | the `resources/<to>` layout                                            |
| `PROJECT_ROOT` + `STATIC_DIR = <root>/apps/web/out` (`server.ts`)                | the `apps/daemon` + `apps/web/out` copy layout                         |
| `deploy/Dockerfile` cleanup list (`@types`, sqlite `deps`/`src`, test/docs dirs) | the strip block, kept in parity                                        |
| `bin/od.mjs` (`apps/daemon/bin/od.mjs`)                                          | the relative `../opt/opendesigner/...` symlink into `${PREFIX}/bin/od` |
| `better-sqlite3`                                                                 | the source rebuild, the `build/Release` trim, the ABI probe            |
| `node-pty`, `onnxruntime-node` (transitive)                                      | the `prebuilds/` and `bin/napi-v3/` dead-platform trims                |

A **new** native module means all three of: trim its unloadable platform payload, keep whatever
its loader needs, and extend the ABI probe so the shipped addon is proven under the build's Node.

`scripts/smoke-opendesigner.sh` is the acceptance contract, so a renamed endpoint (`/api/skills`,
`/api/design-systems`, `/api/design-templates`, `/api/craft`, `/api/prompt-templates`,
`/api/plugins`, `/api/plugin-previews/manifest.json`), a changed `<title>`, or a daemon that
starts writing into `HOME` shows up there. Update the script only for a genuine rename, and say so
in the report.

## Phase 5 — rebuild and smoke

```bash
cd "$REPO"
pixi install -e designer                                     # a few minutes cold: install, both builds, node-gyp, deploy
pixi run -e designer node --version                          # must print v24.x
pixi run -e designer pi --version
pixi run -e designer pi --list-models                        # OpenDesigner's own model probe
pixi run -e designer od --help                               # upstream's bin name; shadows coreutils' od here
pixi run -e designer bash scripts/smoke-opendesigner.sh      # needs port 7456 free; OD_SMOKE_PORT=7788 to move
du -sh .pixi/envs/designer/opt/opendesigner                  # 960 MiB at 0.27.0, ~1 GiB guardrail
```

- `pixi install -e designer` **is safe from the bwrap sandbox**, unlike `-e agents`: the host
  `~/.pi` file binds land in the **agents** prefix only, so nothing inside `.pixi/envs/designer`
  is un-unlinkable during the env sync. Never run `-e agents` from the sandbox (see `update-all`).
- The smoke test runs the daemon with `env -i`, an empty `HOME` and a fresh `OD_DATA_DIR`, so a
  pass proves the package is self-sufficient: Node 24, the `od` symlink target, the static UI's
  title, all six resource endpoints non-empty, every bundled plugin's absolute source path
  existing, a non-empty plugin-preview manifest, `OD_DATA_DIR/app.sqlite` created, nothing written
  into `HOME`, the listener bound to `127.0.0.1` only, and the port freed by `SIGTERM`.
- Installed tree must contain **no** build tool: `pnpm`, `node-gyp`, `gcc`, `g++`, `cc`, `make`,
  `python`, `python3` under `.pixi/envs/designer/bin` all fail the smoke test. A new build dep
  that leaked into `run:` shows up here and nowhere else.
- If the tree crosses ~1 GiB, the first lever is
  `…/onnxruntime-node/bin/napi-v3/linux/arm64` (34 MiB, unloadable on an x86-64-only package) —
  `build.sh` says so in its comment. Do not trim licenses/notices or anything under `linux`.
- Native payload trim after install:
  `ls .pixi/envs/designer/opt/opendesigner/apps/daemon/node_modules/.pnpm/onnxruntime-node@*/node_modules/onnxruntime-node/bin/napi-v3`
  must list `linux` alone.

## Phase 6 — CI and report

`.github/workflows/opendesigner.yml` runs the same four commands with `locked: true`, so CI is the
referee. After pushing: `gh pr checks --watch` (or `gh run watch <id>`); on a designer failure read
the install step first — a lockfile or pixi-version mismatch looks exactly like a build failure.

```
## OpenDesigner updated
- version: 0.27.0 → 0.28.0            (tag open-design-v0.28.0, confirmed in refs/tags; package.json says <marketing version>)
- sha256: <old> → <new>               (computed from the recipe's own URL)
- pnpm_version: <old> → <new> | unchanged   (from the tag's packageManager)
- nodejs range: unchanged | <old> → <new>   (engines.node at the tag)
- RESOURCE_TREES: unchanged (10/10) | <what moved and why>
- interface touch points: all present | MISSING <list> — build.sh changed: no | yes (what)
- build + smoke: pass | fail (<one line>); installed tree <size>
- CI: <run conclusion>
```

When nothing moved, say so explicitly: "already at latest — `open-design-v0.27.0` is the newest
stable tag and the pin matches it; no effective change". Finish with `pixi r lint`.
