# OpenDesign integration design

[OpenDesign](https://opendesigner.io/) — the product is written `OpenDesign`, one word; the
website's domain spells it `opendesigner.io`, and the GitHub repository is
`nexu-io/open-design`. Nothing in this project should rename one to match another: prose and
user-facing identifiers say `OpenDesign` / `opendesign`, and the only `opendesigner` left is the
domain in a URL.

## Status and decisions

Slice 1 implements the Linux x86-64 source recipe, `designer` environment, and Linux CI build/smoke
coverage; what the recipe actually assumes about the pinned tag, and the verification evidence, are in
[AGENTS.md](AGENTS.md#opendesign-designer-env-pixi-recipesopendesign) and in the two smoke scripts.
Slice 2 implements the Linux launcher: the
`opendesign` / `opendesign-unsafe` / `opendesign-help` pixi tasks, `scripts/opendesign-bwrap.sh` and
its per-workspace state, the `~/.local/bin/opendesign` wrapper, and the sandboxed CI smoke test. The
Windows unsafe path (`build.bat`, an MSYS-bash launcher) remains later-slice work; the
`update-opendesign` skill and its place in `update-all` are implemented. See
[Implementation sequence](#implementation-sequence).

The requested product choices are:

| Decision              | Selected direction                                                                                                |
| --------------------- | ----------------------------------------------------------------------------------------------------------------- |
| UI                    | OpenDesign's production headless daemon serving its static Next.js UI on loopback; the user supplies the browser  |
| Linux                 | Fully sandboxed with bubblewrap, including the daemon and every agent/process it spawns                           |
| Windows               | Supported only through an explicitly named and warned-about `opendesign-unsafe` path                              |
| State                 | Per-workspace OpenDesign state in `<workspace>/.od`; other writes limited to explicit binds and Pi/Git exceptions |
| Initial feature scope | Core local design: Pi, bundled content, projects, previews, and HTML/ZIP workflows                                |
| Packaging             | A new source-built conda recipe in a separate `designer` environment; leave `agents` unchanged                    |
| Docker                | Not used at build time or runtime                                                                                 |

The Windows choice is a deliberate exception to the original “fully sandboxed with bwrap” goal. Bubblewrap does not exist on Windows, and OpenDesign's `OD_SANDBOX_MODE` is not an OS security boundary. The Windows launcher must therefore be named `opendesign-unsafe`, print a warning on every launch, and reject `--no-git` rather than pretend to enforce it. There must not be a normal Windows `opendesign` task that silently launches without isolation.

## Executive summary

Add a new `opendesign` local path recipe that reproduces the useful parts of OpenDesign's own Docker build directly with conda-forge Node 24 and pnpm 10.33.2. The recipe builds the Express daemon and static web export, deploys production-only npm dependencies, and installs those plus the bundled skills/design resources under `${PREFIX}/opt/opendesign`. It does not build Electron, use Docker, require a system Node installation, or run `pnpm install` at runtime.

Add a separate `designer` environment composed from the existing `pi`, `sandbox`, and `git` features plus the new recipe. This reuses the exact `pi-coding-agent`, `pi-extensions`, and `pi-home` packages and their update path, without adding OpenDesign, Node module trees, or design assets to `agents`. Do not include `pytools` or `herdr` in `designer`.

On Linux, a new foreground launcher runs the whole OpenDesign daemon inside a bubblewrap namespace modeled on `scripts/bwrap-pi.sh`. OpenDesign spawns the raw conda Pi binary with `PI_BIN`; Pi remains inside the same namespace, so there is no nested bubblewrap and no unsandboxed child. Reuse the existing Pi auth/model configuration and the existing non-destructive Git policy. Bind only the workspace, explicitly approved Pi state, requested extra directories, and worktree metadata as writable.

Set `OD_DATA_DIR=<workspace>/.od`, run the daemon on `127.0.0.1:7456` by default, and invoke `od daemon start --serve-web --no-open`. The launcher prints the URL; opening a browser is a host-side convenience, not a runtime dependency. Ctrl-C stops the process tree.

Do not enable OpenDesign's `OD_SANDBOX_MODE` in v1. Bubblewrap supplies the real isolation. OpenDesign's mode relocates `HOME` and XDG paths, which would break the existing Pi/Git/gh configuration assumptions while adding little protection inside the outer namespace. Set the OpenDesign data/resource paths and `PI_CODING_AGENT_DIR` explicitly instead.

## What upstream actually provides

This design was based on the OpenDesign website, release metadata for `open-design-v0.27.0`, and upstream source at commit `20c49e75c9164bb592cfc299d366884ed79f9914`. Implementation must select a release tag and revalidate these observations against that exact source.

Relevant upstream facts:

- OpenDesign is an Express daemon plus a Next.js UI, not an agent runtime of its own. It persists SQLite state and project files below `OD_DATA_DIR`.
- It has a native Pi adapter. It detects `pi`, obtains models with `pi --list-models`, launches `pi --mode rpc`, passes model and thinking flags, forwards prompts and images over RPC, captures Pi session files for later turns, and supports abort.
- `PI_BIN` overrides Pi executable discovery. `PI_CODING_AGENT_DIR` is an allowed per-agent environment setting.
- The daemon can serve the static web export itself. `od daemon start --serve-web` needs neither Electron nor a separate Next.js server.
- The official production Docker build performs approximately:
  1. `pnpm install --frozen-lockfile`;
  2. build `@open-design/daemon` and `@open-design/web`;
  3. `pnpm --filter @open-design/daemon deploy --legacy --prod ...`;
  4. run the daemon with Node and copy the static `apps/web/out` and resource trees into the runtime image.
- The resource closure includes `skills`, `design-templates`, `design-systems`, `craft`, `prompt-templates`, first-party plugin metadata, frame/pet assets, and plugin-preview metadata.
- The daemon declares native/runtime npm dependencies including `better-sqlite3`, `node-pty`, and `@ffmpeg-installer/ffmpeg`. A source build therefore needs the normal native-module toolchain at build time and must ship the resulting production modules, but does not need a compiler at runtime.
- The current `open-design-v0.27.0` release publishes macOS and Windows desktop installers, but no Linux asset. The published Windows installer is about 465 MB and the macOS installers about 430–450 MB; those figures are desktop-installer sizes, not estimates for this headless recipe.
- OpenDesign's `OD_SANDBOX_MODE` creates per-run directories, rewrites agent `HOME`/XDG variables, and constrains imported-folder roots. It does not create namespaces or provide bubblewrap-equivalent filesystem/process isolation.
- Some browser-automation functionality searches for a system Chrome/Edge/Chromium. That is outside the initial core scope and must not become an accidental runtime requirement.

Primary references:

- [Runtime architecture](https://github.com/nexu-io/open-design/blob/20c49e75c9164bb592cfc299d366884ed79f9914/docs/architecture.md)
- [Pi runtime definition](https://github.com/nexu-io/open-design/blob/20c49e75c9164bb592cfc299d366884ed79f9914/apps/daemon/src/runtimes/defs/pi.ts)
- [Pi RPC session handling](https://github.com/nexu-io/open-design/blob/20c49e75c9164bb592cfc299d366884ed79f9914/apps/daemon/src/agent-protocol/pi-rpc/session.ts)
- [OpenDesign's sandbox-mode implementation](https://github.com/nexu-io/open-design/blob/20c49e75c9164bb592cfc299d366884ed79f9914/apps/daemon/src/sandbox-mode.ts)
- [Upstream Docker build](https://github.com/nexu-io/open-design/blob/20c49e75c9164bb592cfc299d366884ed79f9914/deploy/Dockerfile)
- [Bundled resource list](https://github.com/nexu-io/open-design/blob/20c49e75c9164bb592cfc299d366884ed79f9914/tools/pack/src/resources/index.ts)

## Target architecture

```text
host browser
    | HTTP + SSE to 127.0.0.1:7456 only
    v
+-------------------------------------------------------------------+
| bubblewrap namespace (Linux; one boundary for the whole tree)     |
|                                                                   |
| node .../od.mjs daemon start --serve-web --no-open                |
|   |                                                               |
|   +-- SQLite, projects, generated files -> <workspace>/.od        |
|   |                                                               |
|   +-- PI_BIN=<designer-prefix>/bin/pi                             |
|          pi --mode rpc                                            |
|            |                                                      |
|            +-- tools and shell commands in the same namespace     |
|            +-- existing auth.json/models.json/settings.json       |
|            +-- existing git/gh non-destructive policy             |
|            +-- model APIs, local llama-server/Strata, web fetch   |
+-------------------------------------------------------------------+

Read-only: application resources, conda prefixes, Git guards, approved Git config
Read-write: selected workspace/.od, explicit binds, selected Pi state files
Not mounted: host SSH agents, Docker sockets, general host home, host /run
Network: shared with the host, as in the existing Pi sandbox
```

There is one OS sandbox around the daemon and all of its descendants. Do not point `PI_BIN` at `~/.local/bin/pi` or `scripts/bwrap-pi.sh`: doing so either recurses into the launcher or attempts nested namespace creation. Point it at the raw binary in the active designer prefix.

## Packaging and environments

### New recipe

Create:

```text
pixi-recipes/opendesign/
  recipe.yaml
  build.sh
  build.bat
```

`recipe.yaml` should:

- name the package `opendesign` and pin an upstream stable release tag plus SHA-256;
- use the tag's source archive, not a floating `main` archive;
- pin Node to the upstream-compatible `>=24,<25` line and pnpm to the tag's `packageManager` version (`10.33.2` in the reviewed source);
- include the conda compiler/native-build prerequisites needed by `better-sqlite3` and `node-pty` only in build requirements;
- keep runtime requirements to Node and libraries actually reported by rattler-build's dynamic-linking checks;
- use extension-less `script.file: build`, following the existing cross-platform recipe convention;
- run no Docker command and download no Docker image.

The build scripts should reproduce the daemon/static-web portion of upstream's Dockerfile:

1. Use a pnpm store/cache in the build work area or rattler cache, never in the final prefix.
2. Run `pnpm install --frozen-lockfile` with the lockfile's approved native build scripts.
3. Build the daemon and web production static export. Do not request the `server` or `standalone` web output modes and do not build the Electron/packaged closure.
4. Run the production `pnpm deploy` for the daemon.
5. Install the deployed daemon at `opt/opendesign/apps/daemon` and static UI at `opt/opendesign/apps/web/out`; this relative layout is what the daemon's project-root resolution expects.
6. Install bundled resources under `opt/opendesign/resources` using upstream's resource list. Do not invent a smaller resource list before measuring it: silently dropping design content would create a different product and a maintenance burden.
7. Remove npm caches, development dependencies, tests, TypeScript sources/maps, native-module source trees, and build intermediates from the packaged tree. Preserve licenses and bundled third-party notices.
8. Add a small versioned launcher/metadata file if needed, but do not generate absolute host paths into package content.
9. On Linux, any `${PREFIX}/bin` symlinks must be relative. On Windows, invoke the `.mjs` entry through the launcher rather than relying on Unix symlinks.

The resulting logical layout is:

```text
${PREFIX}/
  bin/node
  bin/pi
  bin/opendesign                     # -> ../opt/opendesign/apps/daemon/bin/od.mjs
  opt/opendesign/
    apps/
      daemon/{bin,dist,node_modules,package.json}
      web/out/
    resources/
      skills/
      design-systems/
      design-templates/
      craft/
      prompt-templates/
      plugins/
      frames/
      community-pets/
      data/plugin-previews/
```

`pnpm`, Python, compilers, and node-gyp must not be runtime requirements. Runtime native-module installation is forbidden: a recipe build must leave a tree that starts offline except for the user's chosen model/provider traffic.

### New environment

Add an `opendesign` feature and a `designer` environment conceptually equivalent to:

```toml
[feature.opendesign.dependencies]
opendesign = { path = "pixi-recipes/opendesign" }

[environments]
designer = ["opendesign", "pi", "sandbox", "git"]
agents = ["pi", "pi-launcher", "sandbox", "git", "git-launcher", "herdr", "pytools"]
```

The `pi` and `git` features own shared dependencies only. The `pi-launcher` and
`git-launcher` features contain the existing Pi and Git task aliases and are
included only in `agents`; otherwise adding `designer` makes every unqualified
`pi`, `pi-unsafe`, `git`, and `gh` task ambiguous. This changes no package set.

The OpenDesign recipe's Node 24 constraint will solve the whole `designer` environment on Node 24. The current `agents` environment uses a newer Node, so Pi and every packaged extension need an explicit smoke test on Node 24. If a future Pi release requires a newer Node, fail the solve visibly; do not hide the conflict with an unpinned runtime. A two-prefix launcher is a later fallback, not the initial design because it couples environments and makes installation/lifecycle harder.

The separate environment is required regardless of the final size. Pixi's package cache can avoid duplicate downloads and storage for shared packages, but `agents` must not acquire OpenDesign's production `node_modules`, static web bundle, design assets, or native build chain.

### Deliberately omitted dependencies

For the initial core scope, do not add Electron, Chromium/Chrome, Playwright browsers, or poppler. The browser used to view the UI is the user's existing browser, not an OpenDesign subprocess. The npm daemon deployment already includes an ffmpeg binary through upstream's dependency graph; that is an upstream runtime cost, not a commitment to support motion/video workflows.

Do not add Python solely for `inject-strata-model.sh`. Reuse the host's existing `models.json`; the normal Pi launcher remains responsible for updating its Strata provider entry. The OpenDesign launcher can run `inject-pi-extensions.sh`, which needs only Node.

## Runtime and user interface

### Commands

Provide these pixi tasks:

| Task                            | Platforms                              | Behavior                                                                                          |
| ------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `opendesign <workspace>`        | Linux targets                          | Foreground bubblewrapped daemon; defaults to the caller's workspace through the installed wrapper |
| `opendesign-unsafe <workspace>` | Windows later; Linux for debugging now | Full-host-access foreground daemon with a prominent warning; refuses `--no-git`                   |
| `opendesign-help`               | All supported targets                  | Prints the packaged daemon and Pi versions plus the core-scope support statement                  |

The task, the launcher scripts, the wrapper and the package are all spelled `opendesign`. Upstream's
own binary name is `od`, which inside a conda environment would shadow coreutils' octal dump — so the
packaged symlink is `${PREFIX}/bin/opendesign` and the daemon's entry point keeps upstream's
`od.mjs`. A pixi task named `od` was the same mistake in a different place.

Follow the existing Pi argument convention: one positional workspace, with `--no-git`, `--bind`, `--ro-bind`, and OpenDesign options after `--` when calling the pixi task directly. Add `scripts/install/opendesign` and include it in `install-bin.sh` on platforms where the existing installer runs, so users can run `opendesign` from the desired directory without learning pixi's argument separator rules.

Default port: `7456`, matching upstream and avoiding llama-server `9931`, forge `8080`, and Strata's configured `8082`. Permit an explicit port override. Always force `OD_BIND_HOST=127.0.0.1`; do not offer a LAN bind in this integration.

Use a foreground process in v1. It gives reliable Ctrl-C/signal ownership and avoids a second PID-file/service manager. Browser auto-open, background start/stop tasks, and desktop entries are optional later conveniences, not core architecture.

### Per-workspace state

Resolve the workspace to an absolute path and set:

```text
OD_DATA_DIR=<workspace>/.od
OD_RESOURCE_ROOT=<designer-prefix>/opt/opendesign/resources
OD_INSTALLATION_DIR=<workspace>/.od/installation
OD_NODE_BIN=<designer-prefix>/bin/node
OD_BIN=<designer-prefix>/opt/opendesign/apps/daemon/bin/od.mjs
PI_BIN=<designer-prefix>/bin/pi
PI_CODING_AGENT_DIR=<host-home>/.pi/agent
OD_AGENT_HOME=<workspace>/.od/agent-home
XDG_CACHE_HOME=<workspace>/.od/cache
XDG_CONFIG_HOME=<workspace>/.od/config
XDG_DATA_HOME=<workspace>/.od/data
XDG_STATE_HOME=<workspace>/.od/state
TMPDIR=<workspace>/.od/tmp
OD_SANDBOX_MODE unset
```

`OD_INSTALLATION_DIR` is the one value that moved after implementation: upstream's packaged default
is the channel root, i.e. the installation directory itself, but that directory is mounted read-only
here and the daemon writes `installation.json` (=its telemetry installation id) at boot. Per-workspace
state under `.od` keeps it stable across runs and writable.

`OD_AGENT_HOME` scopes OpenDesign's executable discovery away from unrelated host user toolchains; it is not a replacement for `HOME`. Sanitize inherited `OD_*`/`OPEN_DESIGN_*` launch settings before applying these explicit values, especially any host bind or auth-disable setting. Preserve provider credentials and normal proxy variables as the existing Pi launcher does.

Default to OpenDesign-managed projects below `.od/projects`. Folder imports should be limited by the actual mount policy: writable imports must be inside the workspace or an explicit read-write bind; a read-only bind is suitable for reference input, not in-place editing.

Add `.od/` to this repository's `.gitignore` and document that users should do the same in other source repositories. Do not globally ignore it on the user's behalf.

## Linux bubblewrap contract

Reuse the existing script's mount ordering and hard-won rules rather than creating a generic launcher framework. A small shared helper for Pi config seeding/injection is reasonable; a broad rewrite of `bwrap-pi.sh` is not required for this integration and would expand regression risk.

The OpenDesign launcher should have this mount contract:

| Path                                                                                           | Access                                | Reason                                                           |
| ---------------------------------------------------------------------------------------------- | ------------------------------------- | ---------------------------------------------------------------- |
| `/`                                                                                            | Read-only starting view               | Same baseline as `bwrap-pi.sh`                                   |
| `/dev`, `/proc`, `/tmp`, `/home`, `/root`, `/run`                                              | Fresh tmpfs/synthetic mounts          | Hide host state, agents, sockets, and temporary files            |
| `/run/systemd/resolve`                                                                         | Read-only when present                | Preserve DNS without exposing the rest of `/run`                 |
| Selected workspace                                                                             | Read-write                            | Projects, `.od`, and artifacts                                   |
| Designer conda prefix                                                                          | Read-only, rebound after workspace    | Prevent modification even when the workspace contains the prefix |
| `scripts/git-guards`                                                                           | Read-only, rebound after workspace    | Preserve the non-destructive policy inside this repository       |
| Packaged `home/.pi`                                                                            | Read-write, over the read-only prefix | Pi writes `npm/` and `models-store.json` on every model probe    |
| Host Pi `auth.json`, `trust.json`, `settings.json`, `models.json`, `caveman.json`, `sessions/` | Read-write file/directory exceptions  | Reuse login, providers, preferences, and resumable sessions      |
| `$HOME/.pi/agent/intercom`                                                                     | Fresh tmpfs                           | Prevent cross-sandbox broker/socket sharing                      |
| Host `.gitconfig`, `.config/git`, `.config/gh`                                                 | Read-only in default mode             | Existing HTTPS-only Git policy                                   |
| Extra `--bind` / `--ro-bind` paths                                                             | Explicit access                       | User-approved additional inputs/workspaces                       |
| Git worktree common metadata                                                                   | Read-write only when required         | Same worktree support as Pi                                      |

Do not mount the host's general cache directories, SSH directories/agent sockets, Docker sockets, or GPU devices in the core launcher. They are unnecessary for the selected scope. Add a cache or device only when a concrete core workflow needs it.

As with `bwrap-pi.sh`, use `--unshare-all --share-net --die-with-parent`, hide host `/run`, and retain only the resolver exception. Keep the guard binaries first in the sanitized `PATH`, followed by the designer prefix and minimal system paths. `PI_BIN` must name the raw prefix binary explicitly so OpenDesign cannot resolve the host's `~/.local/bin/pi` wrapper.

### Network and threat-model limits

“Fully sandboxed” here means filesystem/process isolation with explicit persistent-write exceptions. It does not mean network isolation. The namespace shares the host network so Pi can reach model providers, web tools, and local llama-server/Strata, and so the host browser can reach the daemon. The daemon itself must listen only on loopback.

The same residual limitations documented for the Pi sandbox apply: host processes are outside the threat model, provider credentials are visible to the agent that needs them, the gh token can be used outside the PATH guard by a determined process, and the non-destructive Git wrappers are a policy layer rather than a complete security boundary. A fine-grained GitHub token and server-side force-push rules remain advisable.

OpenDesign's sandboxed artifact iframes are an additional browser boundary, not a substitute for bubblewrap. Conversely, bubblewrap does not make generated HTML safe to open outside OpenDesign's preview; downloaded artifacts remain untrusted user-selected content.

## Pi and Git integration

Before entering bwrap, perform the same minimal Pi setup as `bwrap-pi.sh`:

- create missing host Pi config files with the same safe defaults;
- run `inject-pi-extensions.sh` so the packaged extension pins are present;
- preserve the existing host `models.json`, including llama.cpp/Strata providers;
- leave Pi's ordinary launcher responsible for Strata rediscovery and for syncing editable Pi skills back to the recipe.

Inside the namespace, `PI_CODING_AGENT_DIR` keeps Pi on its normal external agent directory even though OpenDesign owns a different data root. This is required for model discovery, auth refresh, extension loading, and session resume. OpenDesign captures Pi's reported native session path; sharing Pi's existing `sessions/` therefore allows normal second-turn resume without copying sessions into each artifact project.

Initially reuse the packaged Pi extensions as well as the binary. OpenDesign auto-resolves Pi extension UI requests in RPC mode, so interactive extensions may not behave exactly as they do in a terminal. Validate the shipped extension set. If an extension materially interferes with design generation, introduce a designer-specific Pi settings/session profile while continuing to share auth/models; do not fork Pi.

Default Linux Git behavior is the existing non-destructive policy. `--no-git` must omit all GitHub credential binds and credential-carrying environment, exactly as in the Pi launcher. The unsafe launcher must reject `--no-git`, because with full host access it cannot honor the guarantee.

Do not enable `OD_SANDBOX_MODE` merely for defense in depth in v1. Its `HOME` relocation would hide Pi's default config and break gh's default `~/.config/gh` lookup; fixing that would require coordinated changes to proven guards. The outer bwrap and explicit data paths already provide the relevant confinement and persistence. Revisit only if a concrete upstream feature requires its media tool tokens or application-level import checks.

## Initial support boundary

### Supported and tested

- Production daemon serving the static UI on loopback.
- Pi as the local agent, including model listing, RPC streaming, a second turn using the saved Pi session, cancellation, and image attachment if the selected model supports it.
- Bundled skills, design systems, design templates, craft rules, and prompt resources.
- Managed projects, SQLite persistence across restart, project files, previews, and ordinary HTML/ZIP export paths.
- User-created skills/design systems below `.od`.
- Local and remote providers already configured for Pi.
- Existing Git read/fast-forward workflows and `--no-git` on Linux.

### Not certified in v1

- Electron desktop/AppImage packaging.
- PDF/PPTX/image exports that require screenshot rendering or a headless browser.
- Motion/video creation and browser automation.
- Hosted team/workspace billing and collaboration features.
- Third-party marketplace plugins, arbitrary MCP servers, connectors, and media-provider integrations.

“Not certified” does not necessarily mean the upstream UI hides these features. Avoid invasive patches that fork large parts of OpenDesign just to remove buttons. The v1 documentation should state what is tested and supported; unsupported paths still run inside the Linux namespace if a user tries them, but may fail because their optional tools are absent.

## Alternatives and goal challenges

### Repack a desktop release — rejected

There is no current Linux release asset, and desktop installers include Electron, which the selected headless UI does not need. Using the Windows installer only for Windows would create a second runtime shape and a much larger support matrix for little user benefit.

### Extract the published GHCR image — rejected for v1

This would avoid a Docker daemon but not the important problems. The upstream runtime is Alpine/musl-based and contains native npm modules; mixing its tree with conda Node and glibc-linked Pi is an ABI and packaging risk. Running the whole imported root would be another container runtime in disguise. Building the small daemon/web closure from the tagged source is easier to audit and patch with conda-native dependencies.

### Add OpenDesign to `agents` — rejected

It would combine an already large environment with a substantial npm/runtime tree and design assets. The separate `designer` environment still reuses package recipes and pixi's cache, so merging provides poor value.

### Build a generic sandbox framework now — rejected

The safest reuse is the existing Pi packages, configuration, injectors, Git guards, and mount patterns. Refactoring every existing launcher into an abstraction before OpenDesign works would make unrelated Pi behavior part of this change's blast radius. Extract only small helpers that are naturally shared and covered by regression tests.

### “No external OS dependency” needs a precise meaning

The integration can eliminate new system Node, pnpm, Docker, Electron, Chrome, and poppler requirements. It cannot literally remove the project's existing Linux kernel/user-namespace and AppArmor requirements for unprivileged bubblewrap, and a browser is still needed to display the browser UI. The deliverable should therefore promise **no new external OS runtime dependency beyond the existing sandbox prerequisites and a user-provided browser**, not an impossible independent GUI system.

### Full media parity is poor initial cost/benefit

Chromium and export/media QA would add substantial size and failure modes before Pi integration is proven. Keep them out of the core environment. If later required, put browser/poppler/media additions in a separate `designer-media` feature/environment rather than growing `agents` or silently changing the core footprint.

### Build cost must be measured, not hand-waved

The source tree and bundled assets are substantial, and upstream allows a 4 GiB Node heap for its packaged web build. The headless build should be cheaper than Electron packaging but may still be a multi-hundred-MB, non-trivial build. The first implementation spike must record cold build time/memory, compressed package size, installed application size, and total `designer` environment size. If the result exceeds roughly 1 GiB installed or 30 minutes for a cold build on the reference machine, prioritize publishing/reusing the conda build artifact rather than merging environments or weakening reproducibility.

## Implementation sequence

1. **Packaging spike**
   - Pin one stable tag and implement the daemon/static-web recipe on Linux x86-64.
   - Confirm Node 24 can run the shipped Pi and extensions.
   - Verify the installed tree starts without source, pnpm, compilers, downloads, or Docker.
   - Record size and build-cost measurements and adjust the plan if they are disproportionate.

2. **Linux launcher**
   - Add the `designer` environment, foreground wrapper, per-workspace state, Pi auth/model reuse, and bubblewrap profile.
   - Reuse the Git guards and add only small shared Pi-setup helpers where duplication is obvious.
   - Add `.od/` to `.gitignore` and document the exact support boundary.

3. **Core end-to-end validation**
   - Run the acceptance tests below, including real artifact generation and resume.
   - Test the existing Pi launcher after any shared-helper change.
   - Add CI build/smoke coverage and the update skill only after the package shape is stable.

4. **Windows unsafe path**
   - Implement `build.bat` and `opendesign-unsafe` with MSYS bash and the existing Windows script constraints.
   - Validate the same core workflows without claiming filesystem or Git isolation.
   - Keep Linux as the only platform exposing the normal sandboxed task.

5. **Optional later scope**
   - Consider background lifecycle commands, host browser auto-open, a designer-specific Pi profile, and media/export variants only from measured user need.

## Acceptance tests

What CI asserts today, on Linux: the recipe/environment items below, the daemon/persistence subset
(`/api/health`, the static UI, all six resource endpoints, `app.sqlite` below
`<workspace>/.od`, a `127.0.0.1`-only listener, `SIGTERM` freeing the port) and — via
`scripts/smoke-opendesign-sandbox.sh` — the launcher's mount contract, the absence of any GitHub
credential under `--no-git`, and `SIGINT` tearing the whole tree down. The Pi workflow items (a real
generated artifact, a resumed second turn) need a live model provider and stay manual.

### Recipe and environment

- `pixi install -e agents` contains no `opendesign` package or OpenDesign resources.
- `pixi install -e designer` succeeds for Linux targets; Windows solve/build is validated when the unsafe path is implemented.
- Node reports a 24.x runtime; `pi --version`, `pi --list-models`, and packaged extension loading work.
- The final package contains no pnpm store, compiler toolchain, development `node_modules`, Electron, or downloaded Chromium.
- `pixi r lint` passes; lockfile changes are generated only through pixi.

### Daemon and persistence

- `GET /api/health` succeeds on `127.0.0.1:7456`, the static UI loads in a host browser, and the daemon is not listening on a non-loopback interface.
- Bundled skill/design-system/template listings are non-empty and packaged plugin-preview/resource paths resolve.
- SQLite, managed projects, and generated files land below `<workspace>/.od`; restarting preserves projects and conversations.
- Ctrl-C during a Pi turn terminates the daemon and descendants without leaving an orphan or a corrupted database.

### Pi workflow

- OpenDesign detects the raw designer-prefix Pi and lists the host-configured providers/models.
- A simple design request streams RPC events and writes a real HTML artifact below the managed project.
- A second turn resumes the captured Pi session and edits the same artifact.
- Cancellation and an image-bearing prompt follow the upstream Pi adapter contract.
- Local loopback providers remain reachable from the namespace.

### Sandbox and Git policy

- Workspace writes appear on the host; writes to unapproved persistent host paths fail or land only in disposable tmpfs.
- The designer prefix and Git guards remain read-only even when this repository is the workspace.
- Extra read-only/read-write binds have the requested semantics; workspace bind precedence matches `bwrap-pi.sh`.
- Host Docker/SSH sockets and general host home state are absent.
- Default Git reads and fast-forward paths work; the existing force-push/mutation probes remain blocked.
- `--no-git` exposes no GitHub credential and blocks network Git/gh operations.
- The existing `pi` sandbox regression tests still pass after any shared refactor.

### Windows unsafe workflow

- Every launch prints that no bubblewrap or filesystem/Git sandbox is active.
- `--no-git` exits with an error instead of continuing.
- Core daemon/Pi/project workflows run through MSYS bash without accidentally invoking WSL or requiring system Node/pnpm.
- Documentation never groups this path under a “fully sandboxed” guarantee.

## Maintenance

Once stable, add an `update-opendesign` skill that pins the new stable tag, updates source SHA-256 and pnpm/Node constraints from that tag, builds the recipe, runs the smoke tests, and regenerates the lockfile. Include it in `update-all` only after the update process is proven. Do not track upstream `main`, floating npm dependencies, or a mutable container tag.

Document the final recipe layout, launcher flags, state exceptions, measured size, supported workflows, and Windows warning in the project instructions when implementation lands. The design intentionally leaves those implementation details out of the current `agents` runtime until the recipe and core flow are demonstrated.
