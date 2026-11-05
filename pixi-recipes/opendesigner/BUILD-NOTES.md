# opendesigner packaging spike — build notes

Slice 1 of the OpenDesigner integration (DESIGN.md): the Linux x86-64 source
recipe, the `designer` environment, and Linux CI build/smoke coverage. The
bubblewrap launcher, pixi tasks, `scripts/install/open-designer`, the real
Windows `build.bat`, and the `update-opendesigner` skill are **later slices**
and are not implemented here.

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
   `"version":"0.25.1"` at `/api/health` (verified below). The release tag is
   `open-design-v0.27.0`. The recipe pins `version: "0.27.0"` (the tag, the design's
   choice) and derives the archive URL from it. The tag is the source of truth, as
   DESIGN.md already notes. The marketing version is recorded as the commented
   `upstream_reported_version: "0.25.1"` context field in `recipe.yaml`, which the
   future `opendesigner-help` / `update-opendesigner` skill read instead of
   re-deriving the tag→version mapping.
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
   `onnxruntime-node`, `@ffmpeg-installer/ffmpeg` (~65 MiB), `@img/sharp-linux-x64`,
   and the puppeteer `chromium-bidi` _JS_ (no actual Chromium browser binary),
   `pdf-lib`, `pptxgenjs`. These are upstream's own production dependencies (the Docker
   image ships them); there is **no Electron, no Chromium browser, no pnpm store, and
   no compiler** in the package.

   `build.sh` now trims the native payload that cannot load on this package's only
   platform (it is linux-x86_64-only via `build.skip` + the feature's `platforms`, so
   the loaders can never select the others): `onnxruntime-node/bin/napi-v3/{darwin,
   win32}` (65 + 68 = 133 MiB; `onnxruntime` loads `bin/napi-v3/<platform>/<arch>`) and
   the whole `node-pty/prebuilds/` tree (58 MiB; `node-pty`'s `lib/utils.js`
   `loadNativeModule()` loads `prebuilds/${platform}-${arch}`, and node-pty ships **no
   linux-x64 prebuild at all**). ~191 MiB removed; nothing under `linux` is touched.
   Per-platform payload scoping is already this repo's convention (herdr,
   llama-cpp-binary), and the design's "publish/reuse the conda artifact" lever is not
   available here (`opendesigner` is a `path =` build dep with a conda-forge-only
   channel list, so pixi rebuilds it from the recipe at install), which makes trimming
   the only size lever. Third-party `*.ts` sources under `node_modules` are kept
   deliberately (upstream fidelity: a dep's published `src/*.ts` can be its entry
   point); only the app's own `dist` declaration files/sourcemaps are stripped. That
   strip leaves `apps/daemon/package.json`'s `types` / `exports["."].types` pointing at
   the deleted `dist/cli.d.ts` — cosmetic only: Node resolves through the `default`
   condition (`dist/cli.js`, shipped) and `types` is read by TypeScript/bundlers, which
   never consume this private leaf app. The remaining measured lever is
   `onnxruntime-node/bin/napi-v3/linux/arm64` (34 MiB, equally unloadable on an
   x86_64-only package), left in because the tree is already under the guardrail.

## better-sqlite3 source build

Upstream's `better-sqlite3` install script runs `prebuild-install || node-gyp rebuild
--release`, and the initial `pnpm install` downloads a Debian-built Node-ABI tarball.
That prebuilt addon is not covered by the npm package integrity hash in `pnpm-lock.yaml`.
The recipe therefore deletes `.npm-cache/_prebuilds` and immediately runs a targeted
`pnpm --filter @open-design/daemon rebuild better-sqlite3` with
`npm_config_build_from_source=true` before the production deploy. The variable is scoped
to that command rather than exported: Sharp treats any non-empty value as a request to
compile Sharp too. `node-gyp` fetches the headers for the lockfile-selected Node 24.22.1
and verifies them against Node's published SHA-256 list at build time; nothing downloads
or compiles at runtime.

The deployed addon's `.comment` section reads `GCC: (conda-forge gcc 16.2.0-7) 16.2.0`,
confirming that the conda toolchain produced the packaged binary. The source rebuild
adds about two minutes to the cold build. Its Makefiles, object files, static libraries,
test extension and duplicate linker outputs are stripped after deployment, leaving only
`build/Release/better_sqlite3.node`; the build script then executes a SQLite query
against that exact deployed addon.

## Build-time deviations (do not affect the packaged tree)

- `ELECTRON_SKIP_BINARY_DOWNLOAD=1` / `npm_config_electron_skip_binary_download=1`:
  skips the ~150 MiB Electron binary download. `apps/desktop` is never built or
  deployed, so this only trims the throwaway store, not the prod-only daemon.
- `NODE_OPTIONS=--max-old-space-size=4096`: upstream allows a 4 GiB heap for the web
  build; bounded here to stay within host RAM.
- pnpm store/cache/XDG dirs forced inside the build work area (sandbox `$HOME` is
  tmpfs; a default `~/.local/share/pnpm` store would vanish or balloon RAM).

## Measurements (reference host: 32 cores, ~9 GiB free RAM, fast net)

Figures below are **after** the native-payload/path-strip and SQLite source-build
cleanup fixes of this slice; the first-spike numbers are kept for comparison.

| Metric                       | Before (spike)    | After (this slice)                                     |
| ---------------------------- | ----------------- | ------------------------------------------------------ |
| Cold build wall time         | 2:31.97 (152 s)   | **~4 min** (4:19.74) — well under the 30 min guardrail |
| Peak RSS (whole build)       | 5.17 GB           | ~5 GB (dominated by the `next build` 4 GiB heap)       |
| Installed files              | 18 383            | **15 936**                                             |
| Compressed `.conda`          | 431 / 713 MiB†    | **590 MiB** (install rebuild)†                         |
| Installed `opt/opendesigner` | 1.2 GB (1201 MiB) | **960 MiB — under the ~1 GiB (1024 MiB) guardrail**    |
| Total `designer` env         | 1.9 GB            | 1.7 GB (hardlinked; under-counts, see below)           |

† The compressed `.conda` is **not a stable number**: content is byte-identical
between `pixi build` and the install-triggered build, only the zstd level differs.

**Guardrail status:** the ~1 GiB installed guardrail is now **met** — `opt/opendesigner`
is **960 MiB** (`du -sm`), down from 1201 MiB, after removing ~191 MiB of native
payload that cannot load on linux-x86_64 (divergence 5). The cold-build / 30 min
guardrail remains comfortably met. `du -sh` on `.pixi/envs/designer` reports **1.7 GB**,
but that **under-counts**: pixi hardlinks every extracted package from its shared
cache, so bytes shared with `agents` (Node, Pi, libraries) are counted once across both
envs rather than per-env. The design's "publish/reuse the conda artifact" lever is
unavailable here (`opendesigner` is a `path =` build dep on a conda-forge-only channel
list, so pixi rebuilds from the recipe at install); per-platform trimming is the repo's
own convention (herdr, llama-cpp-binary) and the only size lever, and it was enough to
clear the guardrail.

## Verification commands and results

All run against `.pixi/envs/designer` (abbreviated `$P`).

| Check                                               | Result                                                                                                                    |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `$P/bin/node --version`                             | `v24.22.1` ✅                                                                                                             |
| `$P/bin/pi --version`                               | `1.5.0` ✅ (Pi runs on Node 24)                                                                                           |
| `$P/bin/pi --list-models`                           | exit 0, lists openrouter providers/models ✅                                                                              |
| `node .../od.mjs --help` (pnpm/gyp/compiler absent) | prints usage, exit 0 ✅ (starts offline)                                                                                  |
| daemon start + `GET /api/health`                    | HTTP 200, `{"ok":true,"version":"0.25.1",...}` ✅                                                                         |
| listening socket                                    | `127.0.0.1:7456` only (loopback, not `0.0.0.0`) ✅                                                                        |
| `OD_DATA_DIR` state                                 | `app.sqlite`, `memory/`, `diagnostics/`, `artifacts/`, managed project dirs under `/tmp/od-test-data` ✅                  |
| key files                                           | `apps/daemon/bin/od.mjs`, `apps/daemon/dist/cli.js`, `apps/web/out/index.html`, `bin/od` (relative) ✅                    |
| no pnpm in env                                      | `$P/bin/pnpm` absent ✅                                                                                                   |
| no compilers in env                                 | no `gcc/g++/cc/make/node-gyp/python` in `$P/bin` ✅                                                                       |
| no Electron / Chromium browser                      | no `*electron*` dirs; only `chromium-bidi` JS + posthog `chrome.ts` parser, no browser binary ✅                          |
| no host absolute paths                              | `grep -rl home/crusaderky opt/opendesigner` = **0** (`.modules.yaml` + 15 `.bin` dirs removed) ✅                         |
| TS sources / binding.gyp stripped                   | 0 `*.d.ts`, 0 `binding.gyp` under daemon `node_modules`; **0** `.d.ts` / **0** `.map` under app `dist` ✅                 |
| licenses/notices preserved                          | 321 license/notice/third-party/copying files kept (was 295 before the `*.md` exclusion) ✅                                |
| native payload trimmed                              | `napi-v3/` = `linux` only (darwin/win32 gone); `node-pty/prebuilds/` absent (no linux-x64 to keep) ✅                     |
| native modules kept (linux)                         | `better_sqlite3.node`, `onnxruntime` linux `.so`, `sharp-linux-x64` ✅ (**node-pty: no linux binary — see below**)        |
| better-sqlite3 source build + cleanup               | conda GCC 16.2.0 in `.comment`; deployed SQLite query returns 3.53.1; only `build/Release/better_sqlite3.node` remains ✅ |
| resource trees                                      | all 10 installed and non-empty; drift tripwire asserts count == `BUNDLED_RESOURCE_TREES` (10) ✅                          |
| `bin/od` symlink                                    | relative → `../opt/opendesigner/apps/daemon/bin/od.mjs` ✅                                                                |
| `pixi r lint`                                       | passes ✅                                                                                                                 |
| `pixi lock`                                         | regenerated with CI-pinned pixi 0.81.0; no unrelated package-version changes ✅                                           |

The daemon started with a PATH containing **only** `node` (no pnpm, node-gyp, or
compiler), proving the tree starts offline except for the user's chosen model traffic.

### Runtime acceptance evidence (`OD_RESOURCE_ROOT` set)

The DESIGN acceptance test "bundled skill/design-system/template listings are
non-empty and packaged plugin-preview/resource paths resolve" holds only with
`OD_RESOURCE_ROOT` set. Launched the packaged daemon with `OD_DATA_DIR` in `/tmp`,
`OD_RESOURCE_ROOT=$P/opt/opendesigner/resources`, `OD_BIND_HOST=127.0.0.1`, then:

| Endpoint                                 | Result                                                                                          |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `GET /api/health`                        | HTTP 200, `{"ok":true,"version":"0.25.1",...}`                                                  |
| `GET /` (static UI)                      | HTTP 200, `<!DOCTYPE html>` + `<title>OpenDesign</title>`                                       |
| `GET /api/skills`                        | HTTP 200, 170 KB, `{"skills":[...]}` non-empty                                                  |
| `GET /api/design-systems`                | HTTP 200, 49 KB, `{"designSystems":[...]}` non-empty                                            |
| `GET /api/design-templates`              | HTTP 200, 195 KB, `{"designTemplates":[...]}` non-empty                                         |
| `GET /api/craft`                         | HTTP 200, `{"craft":[...]}` non-empty                                                           |
| `GET /api/prompt-templates`              | HTTP 200, 88 KB, `{"promptTemplates":[...]}` non-empty                                          |
| `GET /api/plugins`                       | HTTP 200, 3.6 MB; bundled plugin `source` paths resolve under `resources/plugins/_official/...` |
| `GET /api/plugin-previews/manifest.json` | HTTP 200, 54 KB baked-preview manifest (route `PLUGIN_PREVIEWS_ROUTE = /api/plugin-previews`)   |
| listening socket (`ss -ltnp`)            | `127.0.0.1:7456` only — not `0.0.0.0`                                                           |
| after `kill`                             | process gone, port 7456 free, `/api/health` no response                                         |

## Pi and Git task ownership

Sharing the original `pi` and `git` features with `designer` also exported their
task aliases into that environment, making the existing unqualified `pi`,
`pi-unsafe`, `git`, and `gh` commands ambiguous. `pixi.toml` therefore keeps the
package dependencies in `pi` and `git`, but moves those aliases into `pi-launcher`
and `git-launcher`, which only `agents` includes. The package set is unchanged,
existing commands resolve in `agents` again, and CI invokes designer binaries
explicitly with `pixi run -e designer ...`.

## CI build and smoke coverage

`.github/workflows/opendesigner.yml` installs `designer` through `setup-pixi`
with `locked: true`, which builds the local recipe from the pinned source archive.
It then checks Node 24, Pi, model discovery and `od --help`, and runs
`scripts/smoke-opendesigner.sh`.

The lockfile is generated with the same pixi 0.81.0 that CI pins. A host pixi 0.84.1
refresh had rewritten `__unix=0=0` to `__unix=0` and changed the path-recipe hashes,
which made every 0.81.0 CI job reject the lockfile before building. Regenerating from
the base lockfile with 0.81.0 restored compatibility without unrelated dependency
updates; a fresh 0.81.0 `pixi install -e designer --locked` and the smoke test pass.

The smoke script launches the installed `od` with `env -i` and a PATH containing
only the packaged Node and `od` symlinks. It verifies the static UI, health and
all bundled resource APIs, every absolute bundled plugin source path, the
plugin-preview manifest, SQLite state below `OD_DATA_DIR`, no writes to the
empty HOME, an exclusive `127.0.0.1` listener, and a clean SIGTERM shutdown. The
same command passes locally: `pixi run -e designer bash scripts/smoke-opendesigner.sh`.

## Runtime boundary: node-pty / interactive Terminal

node-pty ships **no linux-x86_64 binary**: its `prebuilds/` held only darwin and
win32 entries, it has no `build/Release` (pnpm never ran its install script —
upstream's `pnpm.onlyBuiltDependencies` allowlist omits `node-pty`, and
`.modules.yaml` `ignoredBuilds` listed `node-pty@1.1.0`; upstream's own Docker image
has the same gap). So the whole `prebuilds/` tree was dead weight here and is removed.
The `ignoredBuilds` line is a **build-time** observation only: `build.sh` deletes
`.modules.yaml` from the package (DESIGN step 8), so a reader re-checking this against
an installed tree must use upstream's `package.json` allowlist in the tag archive, not
the shipped tree. The 58 MiB removed is measured, not inferred — the pre-fix installed
tree held `prebuilds/{win32-arm64 28 MiB, win32-x64 30 MiB, darwin-* 0.2 MiB}`.

The consequence is a confined feature loss, **not** a core-scope regression: the
interactive Terminal feature cannot load its PTY native module. The failure is
confined there — `dist/services/node-pty.js` loads it lazily via
`await import('node-pty')` and `dist/routes/terminal.js` explicitly handles the
missing-native case — and the Pi RPC agent path (`dist/agent-protocol/pi-rpc/`) does
not use a PTY. Every DESIGN-certified core workflow (daemon + static UI, Pi over RPC,
bundled resources, managed projects/persistence, HTML/ZIP export) is unaffected.

## Not done (later slices)

Bubblewrap launcher (`scripts/`), `scripts/install/open-designer`, pixi
`opendesigner` / `opendesigner-unsafe` / `opendesigner-help` tasks, real Windows
`build.bat`, the `update-opendesigner` skill, and `.gitignore`/AGENTS.md
documentation.
