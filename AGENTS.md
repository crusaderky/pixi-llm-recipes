# AGENTS.md — Project Guide for Coding Agents

## What this project is

A [pixi](https://pixi.sh/) workspace that:

1. Builds and packages llama.cpp as a conda package via **pixi-build / rattler-build**, from source or from upstream release binaries, for cpu, cuda, vulkan and rocm.
2. Packages the tooling around it: the Pi coding agent's plugins and home dir, [herdr](https://herdr.dev) and its file-viewer plugin.
3. Runs Pi in a bubblewrap sandbox against a local llama-server.
4. Benchmarks throughput (`llama-benchy`), long-context recall (`sample-data/context-bench/`) and model/KV-cache quantization quality (`scripts/perplexity*.py`).
5. Inspects GGUF headers to size weights and KV cache without downloading the weights (`scripts/gguf-meta-extract.py`).

## Layout

```text
pixi.toml                    # workspace: features, environments, tasks
pixi.lock                    # generated; never hand-edit
dprint.json lefthook.yml pyproject.toml   # lint config (`pixi r lint`)
models.ini                   # llama-server multi-model preset (--models-preset)
strata.ini                   # Strata's command line
perplexity.yaml              # sample config for scripts/perplexity.py
chat-templates/              # Jinja chat templates referenced from models.ini
perplexity/                  # archived KLD sweep outputs
.agents/skills/              # agent skills
.github/workflows/           # lint.yml, llamacpp.yml, pi.yml, opendesign.yml
sample-data/
  wiki*.raw                  # perplexity/KLD corpora; describe-me.jpg = multimodal smoke test
  context-bench/             # books, answer keys, runner, report aggregator
scripts/
  start-llamacpp.sh stop-llamacpp.sh
  start-strata.sh stop-strata.sh strata-install.sh strata-run.py strata-help.py
  strata_common.py llama-benchy-strata.py
  bwrap-pi.sh pi-unsafe.sh run-herdr.sh
  opendesign-bwrap.sh opendesign-unsafe.sh opendesign-help.sh
  smoke-opendesign.sh smoke-opendesign-sandbox.sh
  inject-pi-extensions.sh inject-strata-model.sh inject-herdr-file-viewer.sh
  install-bin.sh uninstall-bin.sh install-{apparmor,clipboard,file-viewer-renderers,
    git,memlock,oomd}.sh
  git-guards/                # git/gh PATH wrappers + hook farm
  install/{pi,opendesign,herdr,gh,herdr.desktop,herdr.png}
  gguf_common.py kv_cache_common.py perplexity_common.py   # shared, stdlib-only
  perplexity.py perplexity-report.py gguf-meta-extract.py llama-cpp-changelog.py
pixi-recipes/
  llama-cpp-source/    recipe.yaml variants.yaml build.sh patches/
  llama-cpp-binary/    recipe.yaml variants.yaml build.sh build.bat
  strata/              recipe.yaml build.sh   # app + engine + image encoder
  opendesign/          recipe.yaml build.sh   # daemon + static web UI
  pi-extensions/ pi-home/ herdr/ herdr-file-viewer/
```

## Features & Environments (`pixi.toml`)

| Feature                                           | Adds                                                                                    | Tasks                                                                                                                                                                                        |
| ------------------------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `llamacpp`                                        | python + pydantic + pyyaml (sweeper); pairs with one backend feature                    | `llama-help`, `llama-version`, `llama-hello`, `llama-list-devices`, `start-llamacpp`, `perplexity`                                                                                           |
| `llamacpp-{source,binary}-{cpu,cuda,vulkan,rocm}` | pins `llama-cpp` to one recipe + backend flag                                           | —                                                                                                                                                                                            |
| `pi`                                              | `pi-coding-agent`, `pi-extensions`, `pi-home`                                           | —                                                                                                                                                                                            |
| `pi-launcher`                                     | no packages; keeps Pi launchers out of `design`                                         | `pi`, `pi-unsafe`, `pi-export` (Linux)                                                                                                                                                       |
| `sandbox`                                         | `bubblewrap` (Linux only)                                                               | —                                                                                                                                                                                            |
| `herdr`                                           | `herdr`, `herdr-file-viewer` (linux-64 + win-64)                                        | `herdr`                                                                                                                                                                                      |
| `git`                                             | `git`, `gh`                                                                             | —                                                                                                                                                                                            |
| `git-launcher`                                    | no packages; keeps Git launchers out of `design`                                        | `git`, `gh`                                                                                                                                                                                  |
| `opendesign`                                      | the `opendesign` recipe (linux-64 only)                                                 | `opendesign`, `opendesign-unsafe`, `opendesign-help`                                                                                                                                         |
| `strata`                                          | the `strata` recipe (linux-64 only) + `huggingface_hub`, `curl`                         | `start-strata`, `stop-strata`, `restart-strata`, `strata-install`                                                                                                                            |
| `pytools`                                         | python 3.14, `llama-benchy`, huggingface_hub, transformers, openai, matplotlib, tomli-w | `llama-benchy`, `llama-benchy-strata`, `strata-help`, `hf`, `context-bench`, `aggregate-context-bench`, `perplexity-report`, `llama-cpp-changelog`, `gguf-meta-extract`, `openrouter-models` |
| `lint`                                            | lefthook, ruff, dprint, actionlint, shellcheck, pyflakes, codespell, blacken-docs       | `lint`, `install-git-hooks`, `update-dprint`                                                                                                                                                 |

Environments: eight `llamacpp-{source,binary}-{cpu,cuda,vulkan,rocm}`; `agents` = `pi` +
`pi-launcher` + `sandbox` + `git` + `git-launcher` + `herdr` + `pytools` (no standalone
`herdr` env); `design` = `opendesign` + `pi` + `sandbox` + `git`; `lint` and `strata` are
their features alone (`no-default-feature`). `strata-help` and `llama-benchy-strata` live
in `pytools` so they do not trigger the Strata env's engine compile.

Ports: llama-server 9931, Strata 8082 (from `strata.ini`). Root tasks present
everywhere: `stop-llamacpp`, `restart-llamacpp`. Linux target tasks: `install-apparmor`, `install-bin`,
`install-clipboard`, `install-file-viewer-renderers`, `install-git`, `install-memlock`,
`install-oomd`, `install` (all seven), `uninstall`.

`-e <env>` is needed only for the `llamacpp` tasks, which exist in all eight
`llamacpp-*` envs. Everything else resolves on its own.

## llama-cpp recipes

Both recipes hold all backends in one recipe: the `backend` matrix in `variants.yaml` is
re-exported as a build flag, which `pixi.toml` selects
(`llama-cpp = { path = "pixi-recipes/llama-cpp-source", flags = ["cuda"] }`). Backend
requirements and the ROCm `dynamic_linking` exemptions are gated with
`if: backend == "…"`; unsupported platforms `skip`. Package name is `llama-cpp`, build
string `${{ backend }}_${{ build_number }}`.

### Fork pinning

Each recipe's `context:` block lists several `fork:` / `version:` pairs, exactly one
uncommented. Both recipes currently pin mainline `ggml-org/llama.cpp` version `b12613`;
beellama (`v0.5.0`, `asset_prefix: beellama`) and the personal patch fork
(`beellama-staging-v0.5.0-r3`) are kept as commented references. The source recipe clones
`${{ fork }}` at tag `${{ version }}`; the binary recipe downloads
`<asset_prefix>-<version>-bin-…` release assets. There is no commit-SHA field.

**Never change `fork:`** without an explicit request; it is a deliberate choice. Version
bumps use the **update-llama-cpp** skill, which keeps tag families intact: `bNNNN` →
newer `bNNNN`, stable `vX.Y.Z` → newer stable, preview → exact stable when it exists.

### Source build

| Backend  | CMake flag                                       | Extra build deps                  |
| -------- | ------------------------------------------------ | --------------------------------- |
| `cpu`    | —                                                | —                                 |
| `cuda`   | `-DGGML_CUDA=ON`, `-DGGML_CUDA_FA_ALL_QUANTS=ON` | `cuda-nvcc`, `cuda-version =13.5` |
| `vulkan` | `-DGGML_VULKAN=ON`                               | `shaderc`                         |
| `rocm`   | `-DGGML_HIP=ON`                                  | **system** ROCm                   |

`build.sh` installs executables and shared libs into `${PREFIX}/opt/llama` (so `dlopen`
finds backend DLLs), sets `RPATH=$ORIGIN`, enables `GGML_BACKEND_DL`,
`GGML_CPU_ALL_VARIANTS`, `GGML_RPC`, disables tests/examples, symlinks `llama-*` and
`rpc-server` into `${PREFIX}/bin` with relative paths, and compiles through conda's
`ccache`. CUDA architectures default to `native` and are overridable with
`LLAMA_CUDA_ARCHS` (`all` uses ggml's portable list). `recipe.yaml` also exposes
`LLAMA_ROCM_PATH` (default `/opt/rocm/core-10.0`) and `LLAMA_GPU_TARGETS` (default
`gfx1150`).

ccache needs `CCACHE_DIR` / `CCACHE_MAXSIZE` passed from `recipe.yaml` (rattler-build
redirects `HOME`); `build.sh` sets `CCACHE_BASEDIR`, `hash_dir=false`,
`compiler_check=content` and the conda sloppiness set so objects survive moving build
trees. Patches: `0003-cmake-suppress-unused-parameter-warnings.patch` (conda CUDA headers
are included with `-I`) and `0004-ui-asset-validation.patch` (the HF UI now ships hashed
SvelteKit chunks; validate by file type).

**ROCm is not fully served by conda.** conda-forge has no hipBLAS/rocBLAS, so the build
uses system ROCm 10 under `/opt/rocm/core-10.0`; `build.sh` derives `HIP_PATH`/`HIPCXX`
from `ROCM_PATH` and never calls `hipconfig -R` (Debian alternatives priority, not a
pin). Device targets come from `LLAMA_GPU_TARGETS`; AMD splits device code per gfx, so
other targets need their `amdrocm-core10.0-<gfx>` package. `${ROCM_PATH}/lib` is appended
to `CMAKE_INSTALL_RPATH` and allowlisted in `build.dynamic_linking`, because ROCm 10's
`libamdhip64.so.7` would otherwise silently bind to Ubuntu's ROCm 7.1 under `/usr`.
Verify with `ldd .pixi/envs/llamacpp-source-rocm/opt/llama/libggml-hip.so`. Neither ROCm
feature sets `HSA_OVERRIDE_GFX_VERSION` any more (ROCm 10 has native gfx1150 kernels);
both set `GGML_CUDA_ENABLE_UNIFIED_MEMORY=1` for iGPU hosts.

### Binary build

Copies the matching release asset into `${PREFIX}/opt/llama` and symlinks `llama-*` into
`${PREFIX}/bin`; `FORK`, `VERSION` and `ASSET_PREFIX` come from the recipe context. Binary
envs skip compilation entirely.

The source recipe uses `script.file: build.sh`; the binary recipe uses `file: build`,
which rattler-build resolves to `build.sh` on Linux and `build.bat` on Windows. Naming
`build.sh` directly would run bash on Windows, where the generated `build_env.sh` chokes
on vars like `ProgramFiles(x86)`. On Windows there is no `opt/llama` split or symlinks:
executables and DLLs all go into `%PREFIX%\bin`.

Adding a backend: add it to both `variants.yaml` matrices, add a `case` to `build.sh`,
add gated requirements and `skip` entries in `recipe.yaml`, then add the feature/env in
`pixi.toml`, `pixi lock`, `pixi install -e llamacpp-source-<name>`.

## Other recipes

| Recipe              | What it packages                                                                                                                                                           |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pi-extensions`     | Runs `pi install` for each source in `PLUGINS` into `${PREFIX}/home/.pi/agent`; npm pins carry `npm:`. Bump pins, not the recipe version.                                  |
| `pi-home`           | Copies `skills/`, `AGENTS.md`, `keybindings.json` into `${PREFIX}/home/.pi/agent/`, `web-search.json` one level up, and seeds an empty `agent/bin/`.                       |
| `herdr`             | Prebuilt release binaries: Linux stable (`latest.json`, `v{version_stable}`), Windows preview (`preview.json`). Context pins versions and sha256s.                         |
| `herdr-file-viewer` | Plugin root under `${PREFIX}/home/.config/herdr/plugins/herdr-file-viewer/`: prebuilt binary + manifest/scripts/config from the tagged source, plus portable `entry.json`. |
| `strata`            | Strata app tree plus C++/CUDA engine and image encoder, compiled at install time.                                                                                          |
| `opendesign`        | OpenDesign daemon + static UI, built from the pinned tag with conda Node + pnpm.                                                                                           |

Both `herdr` recipes set `dynamic_linking.binary_relocation: false`: rattler-build's
patchelf pass corrupts the static-pie musl Linux binaries. `inject-herdr-file-viewer.sh`
merges the plugin into `~/.config/herdr/plugins.json` from `run-herdr.sh`, before the
PIXI/CONDA env is stripped. Version bumps for all of these plus `llama-cpp` have skills
in `.agents/skills/`; `update-all` chains them and refreshes the lockfile.

## Strata (`strata` env, `strata.ini`)

[Strata](https://github.com/Niko1221/Strata) runs Qwen3.8-Flash-Next (125B MoE) on one
GPU plus system RAM, serving OpenAI/Anthropic/Responses APIs on `127.0.0.1:<port from
strata.ini>` (8082). It refuses a port someone else owns and distinguishes its own
`/health` (carries `"loaded"`) from llama-server's. Whether it can run alongside
llama-server is a VRAM question, not a port one.

```bash
pixi install -e strata     # solves + compiles the engine (~8 min on 32 cores, once)
pixi run start-strata      # first run packs the model (~2 min), then serves
pixi run strata-install    # prepare only, no server
pixi run stop-strata       # SIGTERM, then SIGKILL
pixi run restart-strata
pixi r strata-help         # every setup.py parameter + current strata.ini value
```

### `strata.ini`

Every key is a Strata `setup.py` argument; `scripts/strata-run.py` has no defaults for
them and refuses to run without the file. Precedence: command line after `--`, then
`STRATA_*` env vars (`STRATA_FAMILY`, `STRATA_MODEL`, `STRATA_VISION`, `STRATA_CONTEXT`,
`STRATA_VRAM_RESERVE_MIB`, `STRATA_PORT`, `STRATA_DATA`, `STRATA_KEEP_MTP_INPUTS`), then
the file. `key = value`, dashes or underscores, leading `--` optional; empty value is a
flag, `off`/`no`/`false` drops one. `STRATA_INI` selects another file. `port`, `--port`
and `STRATA_PORT` drive both the server and `start-strata.sh`'s health check (the script
asks `strata-run.py --print port`).

`family` and `model` are read live from setup.py's `FAMILIES`/`MODELS`; a `MODELS` entry
may pin its own Hub `revision` and the `quant` its files actually carry. `OWN_PATH_FAMILIES`
(old name `PREVIEW_FAMILIES`) such as `qwen36` keeps its own engine path and GGUF: no
expert pack, profile or MTP fetch, and `vram-reserve-mib` is applied at prepare as
`--expert-vram`. `nvfp4` (ready-made packs) and `MULTI_FILE_FAMILIES` (`deepseek41`) are
not offered — the wrapper only implements GGUF shards into the Hub cache.

A start applies run-config engine arguments verbatim, so a flag read only while preparing
does nothing there. Preparation keys are recorded in `<data-dir>/strata-prepared.json`;
changing one triggers one re-prepare (`~2 min`, no download) on the next start, and
`strata-install` re-prepares whatever the file says. `--context` is the exception: it
rewrites `--max-context` in the run config before every start. A pruned Hub cache makes
the recorded `--gguf-dir` links dangle (vision then dies with `failed to open GGUF
file`); the wrapper stats the folder and forces a re-link. `setup`, `no-start`,
`update`, `check`, `calibrate` and `gguf-dir` are refused in the file — pass one-shot
commands after `--`.

The VRAM reserve is a pin (`vram-reserve-mib = 2048`): the engine's 700 MiB default lets
the expert cache evict the desktop and kill X. On a 3090 with `UD-IQ3_XXS` and
`drafter = dflash2`, the engine logs ~59–60 tok/s decode and ~1500 tok/s cold prefill;
serve's `prompt … (R tok/s)` divides by total prompt tokens, not newly read ones.
Ubuntu also ships `user@.service` as a `systemd-oomd` kill candidate; the recorded victim
was `systemd --user`, taking the whole session down. `scripts/install-oomd.sh` writes a
drop-in setting `ManagedOOMMemoryPressure=auto` for that unit. Starting from inside the
Pi sandbox unshares cgroups, so the engine cannot determine its RAM budget and streams
experts from the page cache instead of RAM — correct answers, lower speed; start from
the host when that matters.

| What                                                             | Where                                                         |
| ---------------------------------------------------------------- | ------------------------------------------------------------- |
| app + run configs (in `terminal/`), built engine + image encoder | `$CONDA_PREFIX/opt/strata/terminal`                           |
| engine sources hashed into `BUILD.json`                          | `$CONDA_PREFIX/opt/strata/engine`                             |
| expert pack, MTP layer, settings                                 | `$CONDA_PREFIX/strata-data`, `$CONDA_PREFIX/.config/strata`   |
| model GGUF shards                                                | `~/.cache/huggingface/hub` (same blobs as `llama-server -hf`) |
| symlink folder passed as `--gguf-dir`                            | `$CONDA_PREFIX/strata-models/<tag>`                           |
| last prepared `strata.ini` signature                             | `$CONDA_PREFIX/strata-data/strata-prepared.json`              |
| server output                                                    | `strata.log` in the invoking directory                        |

Strata publishes no Linux prebuilt, so the recipe runs upstream `setup.py`'s own
`cmake_build` plus its definition helpers (`host_compiler_defs`, `engine_defs`), excluding
`toolkit_root_defs` and `isa_floor_defs` (`ggml_defs`/`vision_llama` no longer exist since
0.2.16), and writes `terminal/engine/BUILD.json`. The engine is Strata's own fused-kernel code;
confirm from `strata.log` (`prompt kernels: … on the fused kernels`), not the file list.
`llama_cpp_commit` in `pixi-recipes/strata/recipe.yaml` must match setup.py's
`LLAMA_CPP_COMMIT`.

Everything in `ENGINE_SOURCES` (`CMakeLists.txt`, `cmake`, `src`, `include`,
`third_party/ggml`, `third_party/strata-ggml`; plus `terminal/tools/vision` and
`engine/third_party/stb` for `VISION_SOURCES`) must be installed byte for byte, or setup.py
recomputes a stale hash and asks to compile with sudo. `strata-run.py` replaces
`install_build_tools` with a clear error. `strata_control/` is never compiled but imported
at module level by both setup.py and the server, so it must be installed too.
`cuda_architectures` defaults to `86`; override with `STRATA_CUDA_ARCHITECTURES`.

`strata-run.py` loads setup.py as a module and replaces: dependency installation
(CUDA libs come from conda, `${PREFIX}/lib` in `BUILD.json`), model download (Hub cache

- symlink folder instead of a second copy), and engine compilation. The MTP layer is
  fetched as byte ranges from the BF16 checkpoint into `$CONDA_PREFIX/strata-data/mtp`;
  only `rt/` is loaded, and the ~5 GB tensors plus intermediate GGUF are deleted after the
  build unless `--keep-mtp-inputs`. DFlash2 is fetched by `strata-run.py` itself; GGUF-published
  MTP heads are llama.cpp sidecars, not Strata's `rt/` format.

Three settings are applied directly to the run config: `context` (`--max-context`),
`drafter` (the `drafting` key; `dflash2` must be named to survive one-slot starts) and
`vram-reserve-mib`. Other changed keys re-prepare. Sampling comes from the model card's
`MODEL_CARD_SAMPLING` as of Strata 0.1.45; request fields and Chat settings win, and
`strata.ini` sets none. Generated run configs/log files live next to setup.py in
`terminal/` and survive a relink but not a recreated env prefix.

## OpenDesign (`design` env, `pixi-recipes/opendesign/`)

[OpenDesign](https://github.com/nexu-io/open-design) is a local-first design product:
an Express daemon plus static Next.js UI on `127.0.0.1:7456`, driving Pi over
`pi --mode rpc` (`apps/daemon/src/runtimes/defs/pi.ts`). Pi comes from the `pi` recipe,
not this one.

`build.sh` reproduces upstream's Dockerfile stage with conda Node + pnpm:
`pnpm install --frozen-lockfile`, daemon/web builds, `pnpm --filter @open-design/daemon
deploy --legacy --prod`, then cleanup. Its assumptions are asserted:

- `version:` is the git tag `open-design-v<version>`, not the root `package.json` version;
  `source:` fetches that tag's archive with its exact sha256.
- `pnpm_version` must equal the tag's `packageManager`; Node `>=24,<25` is a run
  requirement, and pnpm/Python/compilers are not.
- `better-sqlite3` is compiled from pinned source (`npm_config_build_from_source=true`
  scoped to its rebuild), then `require()`d under the build Node after cleanup.
- `RESOURCE_TREES` mirrors upstream's `BUNDLED_RESOURCE_TREES`; count and non-emptiness
  are asserted.
- Layout: daemon at `opt/opendesign/apps/daemon`, UI at `apps/web/out`, ten trees under
  `resources/` for `OD_RESOURCE_ROOT`; `${PREFIX}/bin/opendesign` is a relative symlink
  to upstream's `od.mjs` (renamed to avoid shadowing coreutils `od`).
- Cleanup removes host absolute paths (`.modules.yaml`, `.bin/`) and non-x86-64 native
  payload; the 34 MiB `napi-v3/linux/arm64` stays inside the ~1 GiB guardrail (960 MiB
  installed at 0.27.0; cold install ~3 min on CI).

`scripts/smoke-opendesign.sh` is the packaged-tree acceptance test: empty `HOME`, fresh
data/resource roots, static UI and six resource endpoints, plugin paths, `app.sqlite`,
localhost-only listener, SIGTERM frees the port, no build tools left in the env.
`scripts/smoke-opendesign-sandbox.sh` checks the launcher's mount contract from outside
the namespace. `design` has no Pi/Git launcher tasks (they stay in `pi-launcher` /
`git-launcher`), is linux-64 only, and `pixi install -e design` is safe from the sandbox
unlike `-e agents`. Bump the tag with the **update-opendesign** skill.

### Launchers

`pixi r opendesign <workspace> [-- <args>]` runs the daemon in the foreground in one
bubblewrap namespace (no nested sandbox); loopback is shared with the host. Mounts:
read-only `/`; tmpfs `/dev`, `/proc`, `/tmp`, `/home`, `/root`, `/run`, except read-only
`/run/systemd/resolve`; read-write workspace, `~/.od`, the design prefix's `home/.pi`,
and selected host Pi config files/sessions; read-only design prefix, `scripts/git-guards`,
`~/.gitconfig`, `~/.config/git`, `~/.config/gh`; tmpfs `~/.pi/agent/intercom`.

Load-bearing choices:

- `OD_DATA_DIR=~/.od` is the single shared state root (SQLite DB, projects, credentials,
  plugin/skill state). A per-workspace root meant a fresh install per directory; one
  daemon at a time, since two writers would corrupt `app.sqlite`. `~/.open-design` is
  deliberately not bound, so deploy tokens and local profiles do not persist.
- `OD_INSTALLATION_DIR=~/.od/installation` (the prefix is read-only and the daemon
  writes there at boot). `PI_BIN=$PREFIX/bin/pi` is the raw binary, never a launcher.
  `PI_CODING_AGENT_DIR` is the host `~/.pi/agent`, bound read-write for Pi's writes.
- `OD_SANDBOX_MODE` stays unset; bubblewrap is the isolation. `--no-git` binds no
  credential at all (`PI_GIT_GUARD=blocked`, empty `GH_CONFIG_DIR`, tokens unset): absence
  of a token is the enforcement, the env layers are UX. `OD_BIND_HOST=127.0.0.1` always;
  `--host` is refused, `--port` is consumed (default 7456). No CUDA devices or host
  caches. The launcher runs `inject-pi-extensions.sh` but not `inject-strata-model.sh`
  (Python), so Strata's provider entry is the normal Pi launcher's job.
- Not validated end-to-end yet: a real design turn with the packaged extension set. The
  smoke test only asserts `/api/agents` reports Pi available via `pi --list-models`.

`pixi r opendesign-unsafe` is the full-host path (warning on every launch; `--bind`
ignored; `--no-git` refused). `~/.local/bin/opendesign` supplies the caller's cwd as
workspace and base64-encodes other args. Both launchers are exercised in
`.github/workflows/opendesign.yml`; the sandbox step needs AppArmor.

## Sandboxes and wrappers

### `bwrap-pi.sh`

Read-only `/`; tmpfs `/tmp`, `/home`, `/root`, `/run`; workdir bound read-write (or a
temp dir for `-`); `$CONDA_PREFIX` and `$PIXI_ROOT` read-only; `~/.cache` binds through;
`--unshare-all --share-net --die-with-parent`. Needs `/etc/apparmor.d/bwrap` (`pixi r
install-apparmor`).

`--bind` / `--ro-bind <dir>` repeat, mount at the same host path, and are checked with
`realpath -e`. They are emitted before the workdir bind, so the workspace wins over an
ancestor read-only bind; a bind of an ancestor of an existing mount detaches it. Mount
lists are bash arrays expanded as `"${array[@]}"` — never space-joined strings — since
paths may contain spaces and bwrap silently creates truncated bind targets.

CUDA passthrough binds every `/dev/nvidia*` device and `/proc/driver/nvidia` when the
driver is loaded, after a best-effort `nvidia-modprobe -u -c=0` for `/dev/nvidia-uvm`.

GitHub access is https-only by default: `~/.gitconfig`, `~/.config/git`, `~/.config/gh`
read-only, no SSH/credential/agent sockets. `/run` is a tmpfs so keyring, SSH and docker
sockets are unreachable (AF_UNIX connect() works across read-only mounts); only
`/run/systemd/resolve` is re-exposed for DNS. The gh token must be in plain `hosts.yml`
storage — a keyring token is invisible in the sandbox because its D-Bus socket lives in
`/run`; `install-git.sh` migrates it. `scripts/git-guards/` provides PATH wrappers and a
`pre-push` hook injected via `GIT_CONFIG_COUNT` → `core.hooksPath`; fetch/pull,
fast-forward pushes and gh reads/writes for issues/PRs are allowed, while force-push,
ref/post deletion, state-changing reviews, repo mutations and raw API writes are blocked.
Aliases, `git send-pack`, `core.hooksPath` overrides and `gh api` mutations are covered.
This is a guard layer, not a security boundary: absolute-path binaries, the readable
token with raw HTTP, and unauthenticated network access remain. Use a fine-grained PAT
plus server-side rulesets; use `pi --no-git` to remove credentials entirely.

Pi-specific behavior: mounts `$CONDA_PREFIX/home/.pi` as `~/.pi`, binds host
`~/.pi/agent/{auth,trust,settings,models,caveman}.json` and `sessions/`, and gives
`~/.pi/agent/intercom` a fresh tmpfs. `inject-pi-extensions.sh` and
`inject-strata-model.sh` run on the host before bwrap. A git worktree workspace binds
the main repo's common `.git` read-write. On exit, `skills`, `AGENTS.md` and
`keybindings.json` are rsynced back into `pixi-recipes/pi-home/` (`--no-times` to avoid
needless recipe rebuilds). All `PIXI_*`/`CONDA_*` and `HERDR_*` control vars are unset.
`pi-unsafe.sh` grants full host access, symlinks the packaged `agent/npm` into
`~/.pi/agent`, and refuses `--no-git` rather than pretending to enforce it.

### Wrappers and Herdr

`pixi r install` runs seven installers (bin, AppArmor, clipboard, renderers, git, memlock,
oomd); `install-bin.sh` symlinks the `~/.local/bin` wrappers and installs the herdr
desktop entry. Wrappers `cd` into the repo, pass your cwd as the workspace, and forward
other args base64-encoded in `_FWD_ARGS`; they resolve relative bind paths against your
cwd and route `--no-sandbox` to the `*-unsafe` task. Calling the pixi task directly needs
a `--` separator before any flag (`pixi run pi --no-git` alone treats the flag as the
workspace). `run-herdr.sh` registers the file-viewer plugin and strips `$CONDA_PREFIX/bin`
from PATH so panes use the sandbox wrapper, not the raw Pi binary.

pi-subagents' `machine: "local"` / saved Herdr machines do not work inside this sandbox:
`~/.config/herdr` and `/run` are absent, so the server socket is unreachable and the
launcher unsets all `HERDR_*` variables. Background/foreground children inherit the
namespace, binds and `--no-git` contract. `worktree: true` gets
`PI_SUBAGENTS_WORKTREE_DIR=$HOME/.herdr/worktrees` bound read-write for git roots.
`herdr integration install pi` is intentionally not installed.

### CI

`.github/workflows/{lint,llamacpp,pi,opendesign}.yml`. Every Pi step has
`timeout-minutes: 10` as a backstop for turns that finish but leave an idle SSE socket
open; `pi.yml` also caps the job at 30 minutes. The model is `LFM2.5-230M` (147 MB, fast
on a cold runner); its tool calls are guesses, so `-nt` keeps pure-text steps honest and
a `--tools bash` step may emit a question instead of a command. The wrapper steps use
`working-directory: ../dummy` to avoid prefilling this repo's large `AGENTS.md` on a
small CPU-only model. Both Pi and llamacpp workflows cache
`~/.cache/huggingface/hub` under `hf-hub-<os>-${{ hashFiles('models.ini') }}`; the model
pin belongs in the key because an existing cache entry is never overwritten.
`llamacpp.yml` has no job timeout because source backends legitimately compile for a
long time.

## Python tooling

### `scripts/gguf_common.py`

Stdlib-only header parser shared by `gguf-meta-extract.py` (pytools) and `perplexity.py`
(llamacpp envs). Holds `GGML_SIZES`/`quant_info`/`tensor_nbytes`, `parse_header`,
`Truncated`, `read_header`, and `GgufHeader`.

- `ARCH_LAZY_TENSORS`: `per_layer_token_embd.weight` on `gemma4`, `qwen4exp`,
  `deepseek41` and `hy_v4_flash` is read lazily from the mmap (n-gram hash tables, up to
  a quarter of a Qwen3.8-Flash-Next file). Such bytes are on disk, not resident weights
  or VRAM. Keep this table in sync with `git grep -l TENSOR_READ_LAZY src/models/`.
- `LAZY_AUTO_MIN_BYTES` (4 GiB), `LAZY_MODE_FLAG` (`--lazy-mode`/`-lzm`): lazy reads
  depend on size, the flag and mmap support, not on `--load-mode`.
- `GgufHeader.sizes()` / `TensorSize`: sizes come from the GGUF **offset column**, not
  `GGML_SIZES`, because a fork may renumber type ids. A table/gap mismatch beyond
  alignment (32) takes the gap; `renumbered_types()` reports those ids.
- `lazy_tensor_names(metadata)` / `GgufHeader.lazy_bytes(names)`: the arch lives in
  split 0's metadata but the tensor may sit in any shard, so merge metadata first.

### `scripts/kv_cache_common.py`

Stdlib-only KV sizing shared by `perplexity.py`, `perplexity-report.py` and
`gguf-meta-extract.py`. `BPW`/`resolve_bpw` (bits per weight including block overhead),
`ModelKV` (attention/SWA/compressed groups, head dims, loop count, exact tail),
`CompressedKV` (side caches and context-independent buffers),
`KVARN_FALLBACK`, `KVARN_HEAD_DIMS` (128/256/512; unsupported head dims refuse to start),
and `MODEL_KV`/`resolve_model(ref)` (hand-curated geometries from GGUF headers).

> All KV arithmetic belongs in `ModelKV`; callers must not derive bytes. Curate hparams
> verbatim from GGUF headers, and count **cache layers**, not `block_count`: hybrid
> recurrent blocks have no KV, MTP blocks may be excluded, and looped models count visits
> (Qwen3.5/3.6: 16 cache layers out of 65 blocks; SenseNova-Loop-9B: 14 + 42 visits).

### `scripts/perplexity_common.py`

Stdlib-only flag/log primitives: `canon`/`ALIASES` (llama.cpp's exact flag spellings),
`iter_cmd_options`/`parse_cmd_args` (valueless flags → `True`, last value wins),
`cmd_signature` (run identity, ignoring KLD/neutral/placement keys so a resumed sweep
survives changes in offload policy), `iter_runs`/`LogRun`/`ModelFile` (log parsing,
including split blocks left by a killed run; `ModelFile.resident = size - lazy`), and
`select_model_files` (which GGUF `-hf <repo>:<tag>` loads: case-insensitive tag followed
by `.` or `-`, first repo-listing match, one model only, sidecars excluded). Feed it the
**repository listing**, not the local cache; `perplexity.py` fetches that via the Hub API
and falls back to the cache offline.

### `scripts/perplexity.py`

Runs `llama-perplexity` over a cross-product of options against one logits dump. YAML:
`common`, `baseline`, `cross-product` (lists of option dicts), optional `include`/
`exclude`, `max_eta_factor`. `label:` becomes `# LABEL:`; duplicate keys across one
product are an error; `exclude` matches all its keys; asymmetric KVarN, an exact-cache
tail and finer value than key quants are auto-dropped; `--file`/`--ctx-size`/`--chunks`/
`--kl-divergence-base` in a combo are hard errors. Combos whose `:quant` tags resolve to
the same file are collapsed. The baseline runs twice (dump creation and noise floor);
completed runs are skipped by `cmd_signature`, output appends to `perplexity.log`.

### `scripts/perplexity-report.py`

Parses `perplexity.log` into interactive HTML + Markdown/SVG reports. X axis is total
VRAM = **resident** weights (`lazy=` bytes get their own "On disk" column) + KV cache at
the run's context; one KLD frontier is drawn on every chart. Labels show only what the
sweep varied and name models by the file loaded, not the `-hf` tag. X ranges are shared
across charts, Y ranges local; `--cache-type-*`/`--author`/`--whitelist` filter runs
without recomputing labels or colours.

### `scripts/gguf-meta-extract.py`

Range-fetches only GGUF headers (8 MiB → 512 MiB) to dump per-tensor CSV and stderr
estimates without downloading weights. Sizes come from `GgufHeader.sizes`;
`bits_per_weight = total_bytes * 8 / n_points`. Stderr reports weight buckets (routed
experts and fraction activated, shared experts, dense FFN, ngrams, other), KV estimates
at 256k across `KV_QUANTS`, fixed overhead, and MSA indexer streams. Totals split into
resident / on-disk ngrams / all weights, plus an activated-weights line for MoE and
looped models. The KV estimate derives `ModelKV` from the GGUF's own hparams
(`n_embd_k_gqa(il) = key_length * head_count_kv(il)`), never from `MODEL_KV`.

Architecture knowledge absent from GGUFs lives in tables here, mirroring llama.cpp:
`_ARCH_SWA_PATTERN`, `_ARCH_FULL_ATTN_INTERVAL` (hybrids with scalar `head_count_kv`),
`_recurrent_state_group()`, `_ARCH_MTP_NO_CACHE`, `_ARCH_INDEXER_KV`,
`_ARCH_INDEXER_SIDE_CACHE` with `_INDEXER_KPOOL_ROW_SLOTS`,
`_ARCH_INDEXER_TOP_K_GATED`, `_ple_conv_state_group()`, `_ARCH_K_ONLY_CACHE`,
`_ARCH_NO_KVARN`, `_loop_schedule` (repeated passes and aliased visits), DeepSeek-V4 /
V4.1 side caches, and SWA tail clamps. `-v` adds the per-group KV derivation.

### `scripts/llama-cpp-changelog.py`

Deterministic markdown changelog between git refs of any fork. Defaults: repo = active
`fork:` in the source recipe (`--repo` overrides), `from` = recipe's
`# Last sync with main at <tag>` comment if present, else that fork's active/commented
pin, `to` = latest stable `bNNNN`/`vX.Y.Z` tag. Commits-only git clone cache per fork at
`~/.cache/llama-cpp-changelog/<owner>-<repo>.git` (`LLAMA_CPP_CHANGELOG_CACHE` overrides);
PR bodies need `GITHUB_TOKEN`/`GH_TOKEN`. The **llama-cpp-changelog** skill summarizes its
output.

### `sample-data/context-bench/`

Books `16k.txt` … `256k.txt` with 20 strict-fact questions each and matching
`<size>.answers.txt` keys; `AGENTS.md` is the no-tools system prompt. `run_benchmark.py`
reads a Pydantic-validated TOML config (`[model tag]` tables: `url`, optional keys,
required `ctx-size` list), sends each book via the `openai` client, and grades normalized
answers into `PASS`/`NO ANSWER`/`WRONG` with `grade = (#PASS − #WRONG)/20`. Grading is
deterministic; inspect `raw_answers` for correct-but-off-format replies.

## Common commands

```bash
# Environments (source builds compile llama.cpp; binary envs do not)
pixi install -e llamacpp-source-cuda
pixi install -e agents

# Serving
pixi run -e llamacpp-source-cuda start-llamacpp                  # logs to llama-server.log
pixi run -e llamacpp-source-cuda start-llamacpp --host-ram 16G   # simulate a 16 GiB host
pixi run -e llamacpp-source-cuda stop-llamacpp
pixi run -e llamacpp-source-cuda restart-llamacpp
pixi run -e llamacpp-source-cuda llama-list-devices
pixi run -e llamacpp-source-cuda llama-hello

# Strata
pixi install -e strata
pixi run start-strata
pixi run strata-install
pixi run stop-strata
pixi r strata-help

# Agents. Exactly one positional arg (workspace, `-` = temp dir); flags go after `--`.
pixi run pi /path/to/workspace
pixi run pi /path/to/workspace -- --no-git
pixi run pi /path/to/workspace -- --ro-bind /path/to/reference
pixi run pi-unsafe /path/to/ws            # full host access, debugging only
pixi run herdr
# …or, after `pixi r install`, from any directory:
pi
pi --no-git

# OpenDesign
pixi install -e design
pixi r opendesign /path/to/workspace              # 127.0.0.1:7456
pixi r opendesign-help
pixi r opendesign-unsafe /path/to/workspace       # refuses --no-git
pixi run -e design bash scripts/smoke-opendesign.sh
pixi run -e design bash scripts/smoke-opendesign-sandbox.sh  # needs AppArmor

# Benchmarks and analysis
pixi run -e llamacpp-source-cuda perplexity -c perplexity.yaml
pixi run perplexity-report perplexity.log -o perplexity-report
pixi run llama-benchy
pixi r llama-benchy-strata
pixi run context-bench sample-data/context-bench/config.toml -o results.toml
pixi run gguf-meta-extract https://huggingface.co/unsloth/GLM-5.2-GGUF/tree/main/UD-IQ1_S -o glm.csv
pixi r llama-cpp-changelog [from] [to]
pixi run openrouter-models z-ai/glm-5.3

# Building packages
pixi build
pixi build --path pixi-recipes/llama-cpp-source
pixi build --path pixi-recipes/strata
```

The file-viewer plugin needs a Herdr key binding (e.g. `prefix+f` → `herdr plugin action
invoke open-file-viewer --plugin herdr-file-viewer`) and `herdr server reload-config`.
Optional renderers (`glow`/`delta`/`bat`) come from `pixi r install-file-viewer-renderers`.

## Rules for coding agents

- **Always run `pixi r lint` after changing files**, and fix what it reports. Linting rewrites files, so re-read from disk before editing further.
- **After changing anything under `pixi-recipes/`, run `pixi install -e <env>`** for every environment using that recipe. A green lint does not mean the recipe builds.
- **Never edit `pixi.lock`** — regenerate with `pixi lock` (or `pixi lock -e <env>`).
- **Never change `fork:` in a llama-cpp recipe** without an explicit request, and never hardcode a commit SHA where `${{ fork }}` / `${{ version }}` belongs. Bump versions through the **update-llama-cpp** skill.
- **`${PREFIX}` only exists inside build scripts** — never reference it elsewhere.
- **Symlinks into `${PREFIX}/bin` must be relative** (`../opt/llama/...`) for prefix portability.
- **Windows scripts run under the MSYS2 bash shipped by the environment** (`m2-bash`, `m2-coreutils`, `m2-grep`, `m2-sed` on win-64): a plain `bash` from PATH resolves to WSL and loses the pixi env. Don't use `jq`, `nc`, `pkill`/`pgrep`, or `ln -s`. Use `node -e` for JSON, `curl` for ports, `taskkill` behind an `$OSTYPE == msys*` branch, and `cp -r` or an NTFS junction (`cmd //c 'mklink /J <link> <target>'`) instead of symlinks. Add another `m2-*` package if needed.
- **All three platforms are targeted**: `linux-64`, `linux-aarch64`, `win-64`.
- **Never run `pixi install -e agents` from inside the bwrap sandbox — it leaves a half-extracted env.** The workdir bind shadows the read-only prefix mount; env sync deletes files, then fails with EBUSY on the host-bound `home/.pi/agent/settings.json`. Repair from the host. `pixi update` / `pixi lock` are lockfile-only and safe in the sandbox.
- **`--host-ram <size>` simulates a smaller host** with a transient systemd user scope and cgroup v2 `MemoryMax`, swap off: the only limit that accounts for mmap page cache. Linux with delegated memory controller only; warmed page cache stays charged to its earlier run, and pinned memory that does not fit gets the server OOM-killed (use `load-mode = mmap`).
