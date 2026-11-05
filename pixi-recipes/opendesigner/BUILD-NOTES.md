# opendesigner packaging spike — build notes

Slice 1 of the OpenDesigner integration (DESIGN.md): the Linux x86-64 source
recipe and the `designer` environment. The bubblewrap launcher, pixi tasks,
`scripts/install/open-designer`, the real Windows `build.bat`, CI and the
`update-opendesigner` skill are **later slices** and are not implemented here.

## Pin

| Field          | Value                                                                                 |
| -------------- | ------------------------------------------------------------------------------------- |
| Repo           | `nexu-io/open-design`                                                                 |
| Git tag        | `open-design-v0.27.0` (stable release, not prerelease)                                |
| Recipe version | `0.27.0` (the tag; see divergence 1)                                                  |
| Source archive | `https://github.com/nexu-io/open-design/archive/refs/tags/open-design-v0.27.0.tar.gz` |
| Archive size   | 192.5 MiB                                                                             |
| sha256         | `6fb2dedea9fc5d1bf01cd158fd7597b62a171aad60ae344716b5a9ed4a1c4987`                    |
| pnpm           | `10.33.2` (root `package.json` `packageManager`)                                      |
| Node           | `>=24,<25` (root `engines.node: "~24"`); solved to `nodejs 24.22.1`                   |
| daemon bin     | `od` = `./apps/daemon/bin/od.mjs`                                                     |

## Upstream files read at the tag

- `package.json` — `packageManager`, `engines`, `bin`, `pnpm.onlyBuiltDependencies`.
- `apps/daemon/package.json` — build script (`tsc -p tsconfig.json` → `dist/cli.js`),
  `main`/`bin`, production dependencies (incl. native `better-sqlite3`, `node-pty`,
  and `@ffmpeg-installer/ffmpeg`).
- `apps/web/package.json` — build script (`next build && tsc -b --noEmit`).
- `apps/web/next.config.ts` — static export (`out/`) unless `OD_WEB_OUTPUT_MODE`
  selects `server`/`standalone`; the headless build takes the default static export.
- `deploy/Dockerfile` — the production closure this recipe reproduces (install →
  daemon build → web build → `pnpm deploy --legacy --prod` → copy daemon + web/out +
  resource trees).
- `tools/pack/src/resources/index.ts` — `BUNDLED_RESOURCE_TREES`, the authoritative
  source→dest mapping for bundled resources.
- `apps/daemon/src/daemon-paths.ts` and `apps/daemon/src/server.ts` — how the daemon
  resolves each resource tree (`resolveDaemonResourceDir(OD_RESOURCE_ROOT, segment,
  fallback)`), `STATIC_DIR = <project-root>/apps/web/out`, and `resolveProjectRoot`.
- `apps/daemon/src/project-root.ts` — confirms `PROJECT_ROOT` resolves to
  `opt/opendesigner` from `apps/daemon/dist`, so `apps/web/out` must be its sibling.
- `apps/daemon/bin/od.mjs` — `#!/usr/bin/env node` entrypoint.

## Installed layout

```
opt/opendesigner/
  apps/daemon/{bin,dist,node_modules,package.json}   # pnpm deploy --prod output
  apps/web/out/index.html ...                        # Next.js static export
  resources/
    skills/ design-templates/ design-systems/ craft/
    frames/ community-pets/ prompt-templates/
    plugins/_official/ plugins/registry/
    data/plugin-previews/
bin/od -> ../opt/opendesigner/apps/daemon/bin/od.mjs  # relative symlink
```

Resource directories installed (10), with upstream's `tools/pack` source→dest mapping:

| source tree             | installed at                     | entries |
| ----------------------- | -------------------------------- | ------- |
| `skills`                | `resources/skills`               | 171     |
| `design-templates`      | `resources/design-templates`     | 116     |
| `design-systems`        | `resources/design-systems`       | 154     |
| `craft`                 | `resources/craft`                | 13      |
| `plugins/_official`     | `resources/plugins/_official`    | 6       |
| `plugins/registry`      | `resources/plugins/registry`     | 2       |
| `assets/frames`         | `resources/frames`               | 6       |
| `assets/community-pets` | `resources/community-pets`       | 8       |
| `prompt-templates`      | `resources/prompt-templates`     | 2       |
| `data/plugin-previews`  | `resources/data/plugin-previews` | 1       |

The daemon reads these via `OD_RESOURCE_ROOT=<prefix>/opt/opendesigner/resources`
(set by the future launcher). Without that env var it falls back to project-root
siblings — which is the layout upstream's Docker image uses instead.

## Divergences between DESIGN.md and upstream at the tag

1. **Marketing version ≠ tag.** Root `package.json` is `0.25.1`; the daemon reports
   `"version":"0.25.1"` at `/api/health`. The release tag is `open-design-v0.27.0`.
   The recipe pins `version: "0.27.0"` (the tag, the design's choice) and derives the
   archive URL from it. The tag is the source of truth, as DESIGN.md already notes.
2. **Resource layout is the Electron-pack model, not the Docker model.** DESIGN.md
   step 5–6 (and the launcher's `OD_RESOURCE_ROOT`) describe a `resources/` subtree.
   Upstream's production **Dockerfile** instead lays resources out as _siblings of
   `apps/`_ with no `OD_RESOURCE_ROOT` (project-root resolution). Both are valid code
   paths in `daemon-paths.ts`/`server.ts`. This slice follows the design (resources/
   subdir) and the `tools/pack` mapping, and verified the daemon resolves them when
   `OD_RESOURCE_ROOT` points at `resources/`. The launcher slice must set that var.
3. **DSH runtime tarball omitted.** `tools/pack`'s `packBundledDshRuntime` also packs
   `packages/dsh-runtime` into `resources/agent-runtimes/deepseek-harness` via
   `pnpm pack`. It is **not** a static tree, it is the DeepSeek-harness agent runtime
   (out of the Pi-only core scope), and the Docker closure omits it too. Left out;
   add it only if a DSH runtime is ever in scope.
4. **Start invocation.** DESIGN.md says `od daemon start --serve-web --no-open`. The
   actual CLI (and the Docker `CMD`) is the bare `od --no-open` / `node dist/cli.js
   --no-open`, which starts the daemon _and_ serves the static UI by default. The
   launcher slice should confirm the exact flag spelling against `od --help`.
5. **Production closure carries ML/media deps.** The daemon's prod graph pulls
   `onnxruntime-node` (with macOS `.dylib` and Windows `.node` binaries),
   `@ffmpeg-installer/ffmpeg` (~65 MiB), `@img/sharp-linux-x64`, and the puppeteer
   `chromium-bidi` _JS_ (no actual Chromium browser binary), `pdf-lib`, `pptxgenjs`.
   These are upstream's own production dependencies (the Docker image ships them);
   there is **no Electron, no Chromium browser, no pnpm store, and no compiler** in the
   package. The cross-platform native prebuilds (darwin/win `onnxruntime` + `node-pty`,
   ~130 MiB) are dead weight on this linux-64 package and are the obvious future size
   lever, but stripping them diverges from upstream, so they are left in for now.

## Build-time deviations (do not affect the packaged tree)

- `ELECTRON_SKIP_BINARY_DOWNLOAD=1` / `npm_config_electron_skip_binary_download=1`:
  skips the ~150 MiB Electron binary download. `apps/desktop` is never built or
  deployed, so this only trims the throwaway store, not the prod-only daemon.
- `NODE_OPTIONS=--max-old-space-size=4096`: upstream allows a 4 GiB heap for the web
  build; bounded here to stay within host RAM.
- pnpm store/cache/XDG dirs forced inside the build work area (sandbox `$HOME` is
  tmpfs; a default `~/.local/share/pnpm` store would vanish or balloon RAM).

## Measurements (reference host: 32 cores, ~9 GiB free RAM, fast net)

| Metric                         | Value                                                 |
| ------------------------------ | ----------------------------------------------------- |
| Cold build wall time           | **2:31.97** (152 s) — well under the 30 min guardrail |
| Peak RSS (whole build)         | 5.17 GB (dominated by the `next build` 4 GiB heap)    |
| CPU                            | 407 %                                                 |
| Package content (uncompressed) | 1125 MiB, 18 383 files                                |
| Compressed `.conda`            | 431 MiB (`pixi build`) / 713 MiB (install rebuild)†   |
| Installed `opt/opendesigner`   | **1.2 GB — over the ~1 GiB guardrail**                |
| Total `designer` env           | 1.9 GB                                                |

† Both builds produce **byte-identical content** (18 383 files, 1125 MiB
uncompressed); only the zstd compression level differs between `pixi build` and the
install-triggered build. Content is reproducible; the compressed figure is not a
stable number.

**Guardrail status:** the ~1 GiB installed guardrail is **exceeded** (1.2 GB). The
cold-build / 30 min guardrail is comfortably met. Per DESIGN.md, the lever is to
**publish/reuse the conda build artifact** rather than weaken reproducibility or merge
environments; pixi's package cache already avoids duplicating the shared Node/Pi
packages between `agents` and `designer`. A faithful ~130 MiB trim is available by
dropping the non-linux `onnxruntime`/`node-pty` prebuilds, but that diverges from
upstream's closure and was intentionally not done in this spike.

## Verification commands and results

All run against `.pixi/envs/designer` (abbreviated `$P`).

| Check                                               | Result                                                                                                   |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `$P/bin/node --version`                             | `v24.22.1` ✅                                                                                            |
| `$P/bin/pi --version`                               | `1.5.0` ✅ (Pi runs on Node 24)                                                                          |
| `$P/bin/pi --list-models`                           | exit 0, lists openrouter providers/models ✅                                                             |
| `node .../od.mjs --help` (pnpm/gyp/compiler absent) | prints usage, exit 0 ✅ (starts offline)                                                                 |
| daemon start + `GET /api/health`                    | HTTP 200, `{"ok":true,"version":"0.25.1",...}` ✅                                                        |
| listening socket                                    | `127.0.0.1:7456` only (loopback, not `0.0.0.0`) ✅                                                       |
| `OD_DATA_DIR` state                                 | `app.sqlite`, `memory/`, `diagnostics/`, `artifacts/`, managed project dirs under `/tmp/od-test-data` ✅ |
| key files                                           | `apps/daemon/bin/od.mjs`, `apps/daemon/dist/cli.js`, `apps/web/out/index.html`, `bin/od` (relative) ✅   |
| no pnpm in env                                      | `$P/bin/pnpm` absent ✅                                                                                  |
| no compilers in env                                 | no `gcc/g++/cc/make/node-gyp/python` in `$P/bin` ✅                                                      |
| no Electron / Chromium browser                      | no `*electron*` dirs; only `chromium-bidi` JS + posthog `chrome.ts` parser, no browser binary ✅         |
| TS sources / binding.gyp stripped                   | 0 `*.d.ts`, 0 `binding.gyp` under daemon `node_modules` ✅                                               |
| native modules kept                                 | `better_sqlite3.node`, `node-pty` prebuilds, `onnxruntime`, `sharp-linux-x64` ✅                         |
| `pixi r lint`                                       | passes ✅                                                                                                |
| `pixi lock`                                         | regenerated; `designer` env resolves on `nodejs 24.22.1` ✅                                              |

The daemon started with a PATH containing **only** `node` (no pnpm, node-gyp, or
compiler), proving the tree starts offline except for the user's chosen model traffic.

## Not done (later slices)

Bubblewrap launcher (`scripts/`), `scripts/install/open-designer`, pixi
`opendesigner` / `opendesigner-unsafe` / `opendesigner-help` tasks, real Windows
`build.bat`, CI jobs, the `update-opendesigner` skill, and `.gitignore`/AGENTS.md
documentation.
