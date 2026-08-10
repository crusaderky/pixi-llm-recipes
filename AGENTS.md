# AGENTS.md — Project Guide for Coding Agents

## What this project is

A [pixi](https://pixi.sh/) workspace that:

1. **Builds and packages llama.cpp** as a conda package via **pixi-build / rattler-build**, either compiled from source or repackaged from upstream pre-built release binaries, for four backends: cpu, cuda, vulkan, rocm.
2. **Packages the tooling around it**: the pi coding agent's plugins and home dir, Claude Code, [herdr](https://herdr.dev) and its file-viewer plugin.
3. **Runs pi and Claude Code in a bubblewrap sandbox** against a local llama-server.
4. **Benchmarks** inference throughput (`llama-benchy`), long-context recall (`sample-data/context-bench/`), and model/KV-cache quantization quality (`scripts/perplexity*.py`).
5. **Inspects GGUF headers** to size weights and KV cache without downloading the weights (`scripts/gguf-meta-extract.py`).

## Layout

```
pixi.toml                    # workspace: features, environments, tasks
pixi.lock                    # generated; never hand-edit
dprint.json lefthook.yml pyproject.toml   # lint config (`pixi r lint`)
models.ini                   # llama-server multi-model preset (--models-preset)
perplexity.yaml              # sample config for scripts/perplexity.py
chat-templates/              # Jinja chat templates referenced from models.ini
neon-arena/                  # ad-hoc Reddit-style model comparison prompt
perplexity/                  # archived KLD sweep outputs (logs + html/md/svg reports)
.agents/skills/              # agent skills; .claude/skills symlinks here
.github/workflows/           # claude.yml, lint.yml, llamacpp.yml, pi.yml
sample-data/
  wiki.test.raw, wiki.test.head-2.4k.raw, wiki.train.head-10k.raw  # perplexity/KLD corpora
  describe-me.jpg            # multimodal smoke test
  context-bench/             # long-context recall benchmark (books, answer keys, runner)
scripts/
  start-server.sh stop-server.sh                # llama-server lifecycle
  start-forge-server.sh stop-forge-server.sh    # forge guardrails proxy lifecycle
  bwrap-pi.sh bwrap-claude.sh                   # bubblewrap sandboxes
  pi-unsafe.sh claude-unsafe.sh                 # unsandboxed equivalents (dev/debug only)
  run-herdr.sh                                  # herdr launcher (PATH fixup + plugin inject)
  inject-pi-extensions.sh inject-claude-extensions.sh inject-herdr-file-viewer.sh
  install-bin.sh uninstall-bin.sh               # ~/.local/bin wrappers + herdr desktop entry
  install-apparmor.sh install-memlock.sh install-clipboard.sh
  install-file-viewer-renderers.sh
  install/{pi,claude,herdr,gh}                  # the wrappers themselves
  install/herdr.desktop install/herdr.png
  kv_cache_common.py perplexity_common.py       # importable shared modules
  perplexity.py perplexity-report.py            # KLD sweep + report
  gguf-meta-extract.py llama-cpp-changelog.py
pixi-recipes/
  llama-cpp-source/    recipe.yaml variants.yaml build.sh patches/
  llama-cpp-binary/    recipe.yaml variants.yaml build.sh build.bat
  claude/ claude-extensions/ claude-home/
  pi-extensions/ pi-home/
  herdr/ herdr-file-viewer/
```

## Features & Environments (`pixi.toml`)

| Feature                                           | Adds                                                                                                        | Tasks                                                                                                                                                   |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `llamacpp`                                        | python + pydantic + pyyaml (for the sweeper); pairs with one backend feature                                | `llama-help`, `llama-version`, `llama-hello`, `llama-list-devices`, `start-server`, `perplexity`                                                        |
| `llamacpp-{source,binary}-{cpu,cuda,vulkan,rocm}` | pins `llama-cpp` to one recipe + backend flag                                                               | —                                                                                                                                                       |
| `pi`                                              | `pi-coding-agent`, `pi-extensions`, `pi-home`                                                               | `pi` (Linux), `pi-unsafe`, `pi-export`                                                                                                                  |
| `claude`                                          | `claude`, `claude-extensions`, `claude-home`                                                                | `claude` (Linux), `claude-unsafe`                                                                                                                       |
| `sandbox`                                         | `bubblewrap` (Linux only)                                                                                   | —                                                                                                                                                       |
| `herdr`                                           | `herdr`, `herdr-file-viewer` (linux-64 + win-64 only)                                                       | `herdr`                                                                                                                                                 |
| `git`                                             | `git`, `gh`                                                                                                 | `git`, `gh`                                                                                                                                             |
| `pytools`                                         | python 3.14, `llama-benchy`, `forge-guardrails`, huggingface_hub, transformers, openai, matplotlib, tomli-w | `llama-benchy`, `hf`, `context-bench`, `aggregate-context-bench`, `perplexity-report`, `llama-cpp-changelog`, `gguf-meta-extract`, `start-forge-server` |
| `lint`                                            | lefthook, ruff, dprint, actionlint, shellcheck, pyflakes, codespell, blacken-docs                           | `lint`, `install-git-hooks`, `update-dprint`                                                                                                            |

Environments:

- Eight `llamacpp-{source,binary}-{cpu,cuda,vulkan,rocm}` = `llamacpp` + the matching backend feature.
- `agents` = `pi` + `claude` + `sandbox` + `git` + `herdr` + `pytools`. There is no standalone `herdr` env.
- `lint` = `lint` alone (`no-default-feature`).

Platform gating: source-cuda and source-rocm are linux-64 only; binary-cuda and binary-rocm are linux-64 only; binary-vulkan is linux-64 + win-64 (beellama ships no arm64 vulkan asset).

Root `[tasks]` (present in every env): `stop-server`, `stop-forge-server`, `restart-server`, `restart-forge-server`.
Linux `[target.*.tasks]`: `install-apparmor`, `install-bin`, `install-clipboard`, `install-file-viewer-renderers`, `install-memlock`, `install` (= all five), `uninstall`.

**`-e <env>` is only required when a task exists in more than one environment** — in practice only the `llamacpp` feature's tasks, which exist in all eight `llamacpp-*` envs. Everything else (`pi`, `claude`, `herdr`, `gh`, `llama-benchy`, `perplexity-report`, …) resolves on its own.

> ⚠ **`start-forge-server` / `restart-forge-server` are currently broken.** `start-forge-server` lives in `pytools` (only in `agents`) but declares `depends-on start-server`, which lives in `llamacpp` (only in the `llamacpp-*` envs). No environment has both, so the task fails to resolve everywhere. Start llama-server on 8081 from a `llamacpp-*` env, then run the proxy manually, until the features are realigned.

## llama-cpp recipes

Both recipes hold all four backends in a **single** recipe. The backend comes from the `backend` matrix in `variants.yaml` and is re-exported as a build `flag`, which `pixi.toml` selects per feature:

```toml
llama-cpp = { path = "pixi-recipes/llama-cpp-source", flags = ["cuda"] }
```

Backend-specific requirements and the ROCm `dynamic_linking` exemption are gated with `if: backend == "..."`; platforms that cannot build a backend `skip` it. Build string is `${{ backend }}_${{ build_number }}`; package name is `llama-cpp` for both recipes.

### Fork pinning

Each recipe's `context:` block lists several `fork:` / `version:` pairs with exactly **one uncommented**; the rest are kept as commented-out reference variants. `source:` interpolates `${{ fork }}` / `${{ version }}` — there is no commit-SHA field. The two recipes may pin different forks (the source recipe currently tracks a personal fork of beellama; the binary recipe tracks `Anbeeld/beellama.cpp`, which is allowed to be a `preview-*` tag because pre-built assets ship from preview releases). Mainline `ggml-org/llama.cpp` (`bNNNN` tags) is retained commented-out in both and kept current.

**Never change `fork:`** — it is a deliberate choice. Version bumps go through the **update-llama-cpp** skill, which also enforces the stable-vs-preview rule (source builds clone the tag, so they need a stable `vX.Y.Z`).

### Source build

| Backend  | CMake flag         | Extra build deps                          |
| -------- | ------------------ | ----------------------------------------- |
| `cpu`    | (none)             | —                                         |
| `cuda`   | `-DGGML_CUDA=ON`   | `cuda-nvcc`, `cuda-version =13.1` (conda) |
| `vulkan` | `-DGGML_VULKAN=ON` | `shaderc` (conda)                         |
| `rocm`   | `-DGGML_HIP=ON`    | **system** ROCm (see below)               |

`build.sh` reads `BACKEND` and:

1. Installs into `${PREFIX}/opt/llama` (`CMAKE_INSTALL_LIBDIR`/`BINDIR`) — executables and shared libs **must** coexist there so `dlopen` finds the optional ggml backend libraries.
2. Sets `RPATH=$ORIGIN` so executables find sibling backend DLLs without `LD_LIBRARY_PATH`.
3. Enables `GGML_BACKEND_DL`, `GGML_CPU_ALL_VARIANTS`, `GGML_RPC`; disables tests/examples.
4. Symlinks `llama-*` and `rpc-server` into `${PREFIX}/bin` with **relative** paths (`../opt/llama/...`), required for prefix portability.
5. Compiles through **ccache** via `CMAKE_{C,CXX,CUDA,HIP}_COMPILER_LAUNCHER` (a conda build dep, never a system install).

**ccache**: rattler-build builds in a fresh `.pixi/bld/llama-cpp/<hash>/` tree with `HOME` redirected, so `recipe.yaml` passes `CCACHE_DIR` (default `~/.cache/ccache`) and `CCACHE_MAXSIZE` (default `20G`), both overridable via env at solve time. `build.sh` sets `CCACHE_BASEDIR`, `hash_dir=false`, `compiler_check=content` and the usual conda sloppiness set so objects still hit after `${SRC_DIR}`/`${PREFIX}`/`${BUILD_PREFIX}` move. Setting the launchers explicitly also short-circuits ggml's own `GGML_CCACHE` autodetection. Hit rate is printed after `cmake --build`.

`patches/` currently holds two: a glibc-2.29 guard for `posix_spawn_file_actions_addchdir_np` in vendored `subprocess.h` (conda-forge's sysroot is glibc 2.28 and pixi pins it there), and a CMake `-Wno-unused-parameter` suppression (the warning fires in bulk from conda's CUDA headers, which are included with `-I` rather than `-isystem`, and drowns the build log).

**ROCm is the one backend conda does not fully serve.** conda-forge ships no hipBLAS/rocBLAS, which llama.cpp's HIP backend hard-requires via `find_package`, so this backend links against the **system** ROCm:

- Ubuntu 26.04's standard archive has a complete ROCm 7.1; `build.sh` discovers `HIP_PATH`/`HIPCXX` via `hipconfig`.
- Device targets come from the `gpu_targets` context var (default `gfx1150;gfx1151`, this project owner's Strix Point iGPU); override with `LLAMA_GPU_TARGETS=gfx1100 pixi install -e llamacpp-source-rocm`.
- System ROCm libs resolve via the system linker path, so they are exempted from rattler-build's overlinking check via `build.dynamic_linking`. Consequence: the package needs a matching system ROCm **at runtime** — unlike the self-contained CUDA/Vulkan builds.
- `apt install rocm` on Ubuntu 26.04 omits the gfx1150 rocBLAS kernels, hence `HSA_OVERRIDE_GFX_VERSION=11.5.1` in the feature's activation env (and a matching hack in `build.sh`).

### Binary build

Copies pre-built release binaries into `${PREFIX}/opt/llama` and symlinks `llama-*` into `${PREFIX}/bin`. `FORK`, `VERSION`, `ASSET_PREFIX` (`beellama-<tag>-bin-…` vs mainline `llama-<tag>-bin-…`) come from the recipe context.

Binary envs skip compilation entirely and are far faster to set up.

### `file: build`, not `build.sh`

Both recipes point `script.file` at the **extension-less** `build`: rattler-build resolves it to `build.sh` (bash) on Linux and `build.bat` (cmd.exe) on Windows. Pointing at `build.sh` directly would make rattler-build run it with bash on Windows too, and its generated `build_env.sh` chokes on Windows env vars like `ProgramFiles(x86)`.

On Windows there is no `opt/llama` split and no symlinks: executables and DLLs all go into `%PREFIX%\bin`, which is on `PATH` in activated pixi envs.

### Adding a backend

1. Add the name to `backend:` in `variants.yaml` (both recipes, if binaries exist).
2. Add a `case` branch in `build.sh` for the `-DGGML_*=ON` flag.
3. Add `if: backend == "<name>"` requirements in `recipe.yaml`, plus `skip` entries for unsupported platforms.
4. Add the feature + environment in `pixi.toml`, `pixi lock`, then `pixi install -e llamacpp-source-<name>`.

## Other recipes

| Recipe              | What it packages                                                                                                                                                                                                                                                                                |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `claude`            | Claude Code from npm. Bump `context.version` + `source.sha256`; use the npm `latest` dist-tag.                                                                                                                                                                                                  |
| `claude-extensions` | Runs `rtk init -g --auto-patch` at build time with `HOME`/`CLAUDE_CONFIG_DIR` pointed into the prefix, producing `CLAUDE.md`, `RTK.md` and a patched `settings.json` under `${PREFIX}/home/.claude/`.                                                                                           |
| `claude-home`       | Copies `skills/` into `${PREFIX}/home/.claude/skills/`. Currently empty (`.keep` only).                                                                                                                                                                                                         |
| `pi-extensions`     | Runs `pi install` for each pin in the `PLUGINS` env var of `recipe.yaml`, into `${PREFIX}/home/.pi/agent`. Bump the pins **and** the recipe version when changing plugins.                                                                                                                      |
| `pi-home`           | Copies `skills/`, `AGENTS.md`, `keybindings.json` into `${PREFIX}/home/.pi/agent/`, `web-search.json` into `${PREFIX}/home/.pi/` (one level up — not a typo), and seeds an empty `agent/bin/`.                                                                                                  |
| `herdr`             | Downloads pre-built release binaries. Linux = stable (`https://herdr.dev/latest.json`, tag `v{version_stable}`); Windows = preview-only (`preview.json`, tag `{version_preview}`). Context pins `version_stable`, `sha256_stable_{x86_64,aarch64}`, `version_preview`, `sha256_preview_win_64`. |
| `herdr-file-viewer` | Lays down a herdr plugin root under `${PREFIX}/home/.config/herdr/plugins/herdr-file-viewer/`: prebuilt binary at `target/release/`, plus manifest/scripts/example config fetched from the tagged source.                                                                                       |

Both `herdr` recipes set `dynamic_linking.binary_relocation: false` — the upstream Linux prebuilts are static-pie musl binaries, and rattler-build's default patchelf pass corrupts them (adds RPATH + PT_LOAD; the result segfaults).

`herdr-file-viewer` also writes a portable `entry.json` (version, `min_herdr_version`, description — no absolute paths) next to the manifest. `scripts/inject-herdr-file-viewer.sh` reads it and merges the plugin into `~/.config/herdr/plugins.json`, filling `manifest_path`/`plugin_root` from `$CONDA_PREFIX`. It runs from `run-herdr.sh` before the PIXI/CONDA env is stripped, so nothing but the registry entry and the user's `config.toml` lands in `~/`.

Version bumps for `claude`, `herdr`, `herdr-file-viewer`, `pi-extensions` and `llama-cpp` all have dedicated skills in `.agents/skills/`; `update-all` chains them and refreshes the lockfile.

## Sandboxes and wrappers

### `bwrap-pi.sh` / `bwrap-claude.sh`

Both: read-only root; `/tmp`, `/home`, `/root` as tmpfs; the target workdir bound read-write (or a temp dir if `-` is passed); `$CONDA_PREFIX` and `$PIXI_ROOT` bound read-only; caches under `~/.cache` bound through; `--unshare-all --share-net --die-with-parent`. Both need the AppArmor profile at `/etc/apparmor.d/bwrap` (`pixi run install-apparmor`).

`--with-git` (both): binds `~/.ssh`, `~/.gitconfig`, `~/.config/git`, `~/.git-credentials` read-only and `~/.config/gh` read-write. `SSH_AUTH_SOCK` is reachable automatically under `/run/` (the systemd/gnome-keyring default) and is bound explicitly if it lives under `/tmp`. The conda-forge `gh` shadows any snap-installed one.

pi-specific:

- Mounts `$CONDA_PREFIX/home/.pi` as `~/.pi`; bind-mounts `~/.pi/agent/{auth,trust,settings}.json` and `sessions/` from the host.
- Mounts a fresh **tmpfs** at `~/.pi/agent/intercom`, so the pi-intercom broker and its unix socket stay private per sandbox: a session and its pi-subagents children can talk; independent sandboxes and the host cannot. Without it the extension would write shared state into `$CONDA_PREFIX` through the rw `~/.pi` bind.
- If the workdir is a **git worktree**, binds the main repo's common `.git` dir read-write so git can read shared objects and update worktree admin files, without exposing the main checkout.
- Calls `inject-pi-extensions.sh` to merge the packaged `packages` block into `~/.pi/agent/settings.json`.
- On exit, rsyncs `skills`, `AGENTS.md`, `keybindings.json` back from `$CONDA_PREFIX/home/.pi/agent/` into `pixi-recipes/pi-home/`, so edits made from inside pi can be reviewed and committed. `-c --no-times` keeps mtimes stable when content is unchanged, otherwise pixi-build would rebuild the recipe on every launch.
- Unsets all `PIXI_*` / `CONDA_*` plus `INIT_CWD`, `XML_CATALOG_FILES`, `GSETTINGS_SCHEMA_DIR` before exec.

claude-specific: binds `~/.claude` and `~/.claude.json`; calls `inject-claude-extensions.sh` to deploy the packaged rtk hooks/settings into the host's `~/.claude/`; runs `claude --dangerously-skip-permissions`.

`pi-unsafe.sh` / `claude-unsafe.sh` run with full host access (dev/debug only). `pi-unsafe.sh` additionally symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` (copies on Windows, where MSYS bash cannot symlink, and forces `HOME=%USERPROFILE%` so bash's `~` matches pi's) and cleans up via `trap`.

### `~/.local/bin` wrappers

`pixi r install` runs all five installers; `install-bin.sh` symlinks `scripts/install/{pi,claude,herdr,gh}` into `~/.local/bin` and generates a herdr desktop entry + icon (picking ptyxis / gnome-terminal / plain terminal depending on what exists). `pixi r uninstall` removes them.

The wrappers `cd` into the repo and call the matching pixi task with your cwd as the workspace, forwarding the rest base64-encoded in `_FWD_ARGS` (which dodges pixi's shell-parser mangling of quotes). They resolve `--bind` relative paths against your cwd first, since the task itself runs with the repo as cwd, and honour `--no-sandbox` by routing to the `*-unsafe` task.

Calling the pixi task directly is the awkward path: it takes exactly one positional argument (the workspace), so `--with-git`, `--bind` and any agent flags must follow a `--` separator. `pixi run claude --with-git` does **not** work — `--with-git` is consumed as the workspace directory.

`run-herdr.sh` backs the `herdr` task: it registers the file-viewer plugin, then **removes `$CONDA_PREFIX/bin` from PATH** so `pi`/`claude` spawned inside a herdr pane resolve the `~/.local/bin` wrappers instead of the raw conda binaries (which would bypass the sandbox). It resolves the real herdr binary _before_ the reorder, or `exec herdr` would re-enter its own wrapper forever, and `cd $HOME` so new panes start in `~`.

## Python tooling

### `scripts/kv_cache_common.py` — shared KV-cache sizing

Imported by `perplexity.py`, `perplexity-report.py` and `gguf-meta-extract.py` (hyphenated names, so they are run as `python scripts/<name>.py` and `sys.path[0]` is `scripts/`).

- **`BPW`** / **`resolve_bpw(name)`** — bits per weight per quant, including block overhead (`q8_0` = 8.5, `kvarn4` = 4.375, …); warns and falls back to 32.0 on unknown names.
- **`ModelKV`** — a model's KV geometry (full-attention and sliding-window layer groups, per-group KV-head counts, window size, key/value head dims, loop count, plus `compressed` side caches) and the sizing model for beellama's persistent allocation (quant body + per-sequence f16 exact-tail overlay; bodyless exact ring when the tail covers the SWA window). Key API: `get_total_kv_cache_size()`, `cache_breakdown()` → `CacheGroupSize` rows, `elems_per_token`, `compressed_rows()`/`compressed_note()`, `full_attn_layers_all`/`sliding_window_layers_all`. Only `layers * kv_heads` enters the arithmetic, so a group with varying per-layer head counts is described by its (possibly fractional) average; `value_dim = 0` expresses an MLA / K-only cache; `n_loops > 1` is a looped transformer whose `block_count` blocks each get one cache layer **per pass**.
- **`CompressedKV`** — a cache group that is _not_ one row per token, allocated **in addition** to the token groups (the same layer counts in both). `ratio` = tokens per stored row (`4`/`128` for DeepSeek-V4 CSA/HCA, `1` for a plain full-context side cache such as the DeepSeek-V3.2 / GLM-DSA indexer key cache, `0` for a context-independent buffer of `fixed_rows`). `pad` rounds the row count up; `per_seq = False` marks a single shared allocation.
- **`KVARN_FALLBACK`** — the plain ggml type each `kvarnN` falls back to (`kvarn_fallback_cache_type` in `common/arg.cpp`) when a cache never receives the KVarN params.
- **`KVARN_HEAD_DIMS`** = `(128, 256, 512)` and **`ModelKV.support_kvarn`** — the head dims KVarN can quantize (its kernels rotate each head through a Walsh-Hadamard transform in fixed 128-element slices). This covers head dims only; arch-level exclusions are the caller's. The two failure modes differ: an unsupported **architecture** starts and silently stores the fallback type, whereas an unsupported **head dim** makes llama-server refuse to start (`fail_if_unsupported` has no CLI override) — so the 64-dim-head LFM2 family cannot use `-ctk kvarnN` at all.
- **`MODEL_KV`** / **`resolve_model(ref)`** — hand-curated geometries for the models pinned in `models.ini` (transcribed from their GGUF headers via `gguf-meta-extract.py`), matched case-insensitively by substring against a `-hf`/`-m` reference.

> **All KV-cache arithmetic belongs in `ModelKV`, never in a caller.** Every field must be a hparam transcribed **verbatim** from the GGUF header — never pre-multiplied or pre-summed (`full_attn_layers` is a physical block count, never `block_count * num_loops`). Every derived number is a `ModelKV` method or property; if the one you want doesn't exist, add it — a display value computed the wrong way is indistinguishable from a sizing bug. Whatever `gguf-meta-extract.py` reads out of a header, a curator must be able to type straight into `MODEL_KV` and get the same bytes. (Precedent: `num_loops` was once expanded inside `gguf-meta-extract.py`, which left `MODEL_KV` sizing looped models at half their real cache.)
>
> **A layer count is a _cache_ layer count, not `block_count`** — the two coincide only for an ordinary dense transformer, so curate from `gguf-meta-extract.py`'s derivation block rather than from the raw header. A block can hold no cache in two ways: it is a hybrid's linear-attention block (fixed-size recurrent state instead — a `CompressedKV` row), or it is a NextN/MTP head on one of the `_ARCH_MTP_NO_CACHE` architectures. Qwen3.5/3.6 is both at once: of Qwen3.6-27B's 65 blocks, 48 are recurrent and 1 is MTP, leaving **16** that cache. (Precedent: the whole Qwen3.5/3.6 family was once curated at `block_count`, overstating its cache ~4x.)

### `scripts/perplexity_common.py` — shared flag/log primitives

Imported by `perplexity.py` (runs in `llamacpp-*`) and `perplexity-report.py` (runs in `pytools`), so it is **stdlib only** — the two environments share no third-party dependency.

- **`canon`** / **`ALIASES`** — canonical (long, dash-free) name for every llama.cpp flag spelling this project passes. Not derivable: llama.cpp matches flags by exact string, `--hf` does not exist, and the dash count doesn't follow from the name (`-cmoe`/`-hffv` are single-dash; `--ppl`/`--ui` are double). Canonicalising is what lets a sweep resume against a log full of short spellings.
- **`iter_cmd_options`** / **`parse_cmd_args`** — a command line as `(key, value, raw)` / `{key: value}`. A valueless flag is `True`; a repeated flag keeps its last value, matching llama.cpp's last-wins parsing (which is what lets a combo's `--hf-repo` override an `-hf` in `common:`). A token is a flag iff it is a dash followed by a letter, so `-1` reads as a value and no per-flag arity table is needed.
- **`cmd_signature`** — a run's identity: canonical `(key, value)` pairs minus `KLD_KEYS` (so a logits dump and its `--kl-divergence` rerun match) and `NEUTRAL_KEYS` (`--offline`, which measures nothing and would otherwise force resumed sweeps to re-run their baseline).
- **`iter_runs`** / **`LogRun`** — every run in a log with its command line, output, aborted flag, `# LABEL:` override and `# model:` weight sizes.

### `scripts/perplexity.py` — KLD sweep

Runs `llama-perplexity` over a **cross-product of arbitrary command-line options**, measuring KL divergence against a single baseline. YAML config: `common` (command prefix), `baseline` (creates the logits dump), `cross-product` (list of lists of option dicts, expanded into every union of one dict per list), optional `include`/`exclude`, `max_eta_factor`.

Option keys are llama.cpp flags without dashes, emitted as `--<key> <value>`; a value of `false` omits the flag entirely (e.g. `{kv-tail-tokens: false}` for mainline llama.cpp, which doesn't know it). Nothing is KV-specific, so one sweep can vary cache quants, model quantization (`hf-repo`), or both.

- **`label:`** is the only key not passed through: it becomes a `# LABEL:` comment and overrides the report's own label. Labels from several lists join with `|` in list order.
- **Key collisions** across the lists of one product are a config error (`label:` excepted).
- **`exclude`** drops every combo matching _all_ keys of an entry, whatever its other options.
- **Auto-dropped** from the cross-product (but not from `include`, where an explicit request is intentional): asymmetric KVarN, a non-zero tail on an exact f16/f16 cache, and a value cache finer than the key cache. Evaluated on `common:` overlaid with the combo.
- **Hard config errors**: `--file`/`--ctx-size`/`--chunks`/`--kl-divergence-base` in a combo (all four are fixed by the single logits dump), and a missing `--kl-divergence-base` in `common:`.

The baseline runs twice — once to create the dump, once with `--kl-divergence` against its own logits (noise floor + reference speed without dump I/O). Completed runs are skipped by `cmd_signature`, so a sweep is idempotent and resumable, including against logs from older versions of the script. Output appends to `perplexity.log`.

### `scripts/perplexity-report.py` — KLD report

Parses a `perplexity.log`, extracts per-chunk KL divergence, and writes an interactive HTML report (Chart.js inlined from CDN, with a `<script>` tag fallback) plus a Markdown one (static matplotlib SVG).

Non-obvious behaviour worth knowing before editing:

- **Cost on the x axis is total VRAM**: model weights (summed from the `# model:` provenance the sweeper records) plus the KV cache at the projected context. Weights are all-or-nothing across a log — a row silently missing them would sit at the wrong x — so a log without provenance falls back to KV cache alone, then weights alone, then bpw. The axis is not anchored at 0, or the sweep would collapse into a sliver at the right edge.
- **The KV figure comes from `kv_cache_common.MODEL_KV`**, resolved **per run** and evaluated at `--ctx-size` (default: the run's own context) and `--n-parallel` (default 4).
- **One frontier, drawn on every plot** — the runs with the lowest mean KLD at each cost. Other charts mark those same runs rather than their own optimum, so a point can sit beyond the frontier line there: the fastest run at a cost need not be the most accurate.
- **Labels show only what the sweep varied** (`_build_labels`), `|`-joined in the order model → KV cache → everything else, since a run is only distinguishable from its siblings by what changed. A `# LABEL:` comment overrides the lot.
- **Framing is shared on X, local on Y** — every X change goes through one `setXRange` and lands on all charts; Y belongs to each plot.
- **Filters** `--cache-type-k`/`--cache-type-v`/`--author` (OR within a flag, AND between) and `--whitelist` (exact labels). The baseline and logits runs always survive, and neither labels nor author colours are recomputed on the survivors, so a filtered report stays comparable with the full one.

### `scripts/gguf-meta-extract.py` — GGUF header inspector / VRAM estimator

Points at a HF GGUF repo directory, a single `.gguf` blob/resolve URL, or a glob, and dumps per-tensor metadata to CSV **without downloading the weights**: only the header is fetched, via HTTP Range requests (starting at 8 MiB, growing until the tensor table parses, capped at 512 MiB). It parses the header itself rather than using `gguf.GGUFReader`, which eagerly materialises tensor data and has no header-only mode. Split GGUFs are merged (split 0's hparams win). `--token`/`$HF_TOKEN` for gated repos.

To stderr it prints: the weight split across routed experts (`*_exps.*`, plus the fraction activated per token), shared experts (`*_shexp*`), dense FFN (`ffn_{gate,up,down}`) and everything else, empty buckets omitted; a KV-cache estimate at 256k tokens across `KV_QUANTS`, each with the `--kv-tail-tokens` exact tail that makes it usable; fixed CUDA + logits overhead; the in-KV f32 lightning-indexer stream of an MSA model; and the `--cpu-moe` expert-offload prefill scratch. Recognises mainline ggml and ik_llama.cpp/DFlash quant type ids.

The KV figure derives a `ModelKV` from the GGUF's own hparams — `n_embd_k_gqa(il) = key_length * head_count_kv(il)` is llama.cpp's own formula, and the hparams are the main source for which layers cache at all (`head_count_kv == 0` marks conv/recurrent/linear-attention blocks; `_ARCH_FULL_ATTN_INTERVAL` covers the hybrids that use a stride instead). Unlike the report, it never consults `MODEL_KV`. It sizes a single sequence (`KV_N_PARALLEL`).

Architecture knowledge that the GGUF does **not** carry, and therefore lives in small tables here, each mirroring llama.cpp:

- **MLA**: `attention.key_length` is already the cached latent width (`kv_lora_rank + rope`) and llama.cpp allocates **no V cache** (`has_v = !is_mla`), so the V side is zeroed. `key_length_mla`/`value_length_mla` are the attention math's inner dims, never the cache's; their presence _is_ `llama_hparams::is_mla()`. Per-layer tensor shapes are the fallback for GGUFs missing hparams, with MLA checked first (a hybrid MLA model also carries `attn_k`/`attn_v`, on its non-caching layers).
- **`_ARCH_SWA_PATTERN`** mirrors each loader's `hparams.set_swa_pattern(period, dense_first)`. Not redundant with the GGUF: `attention.sliding_window_pattern` is optional and, when present, overrides only the _period_ — the dense-first phase lives solely in arch code, as does the period for the many GGUFs that omit the key (every Laguna and Gemma-3, for two). Without it such a model is sized as if fully dense, overstating the cache several-fold. Architectures storing the pattern as a per-layer bool array (`gemma4`, `lfm2`, `mimo2`, …) need no entry. If a GGUF declares a window and neither source has a pattern, the script warns and reports an explicit upper bound.
- **`_ARCH_FULL_ATTN_INTERVAL`** — hybrids marking their linear-attention blocks with a **stride** rather than a per-layer array: `is_recr(il)` iff `(il + 1) % {arch}.full_attention_interval != 0` (`qwen35`, `qwen35moe`, `qwen3next`; the loaders default the interval to 4). Every other hybrid zeroes `head_count_kv` on its recurrent blocks, which needs no table — but a Qwen3.5/3.6 GGUF ships a plain **scalar** `head_count_kv`, so without this three quarters of the stack is sized as full attention (Qwen3.6-27B: 65 cache layers instead of 16, ~4x).
- **`_recurrent_state_group()`** — those recurrent blocks are not free: `llama_memory_recurrent` allocates a per-sequence f32 pair (`n_embd_r` conv/rolling + `n_embd_s` state) per layer, from the same pool the KV cache comes from. It rides along as a context-independent `CompressedKV` (ratio 0, forced 32 bpw) so `ModelKV` still owns the arithmetic. The widths mirror llama.cpp's branch order — RWKV, LFM2 short-conv, Kimi KDA, then the Mamba default. **`--spec-draft-n-max` multiplies it**: an MTP/EAGLE3/DFlash draft sets `n_rs_seq` and llama.cpp allocates `(1 + n_rs_seq)` snapshots for draft rollback, so this project's own `models.ini` gets 5x the reported figure. The script sizes the no-speculation case and prints a note.
- **`_ARCH_MTP_NO_CACHE`** — NextN/MTP blocks sit past `hparams.n_layer()`, but that does **not** keep them out of the attention cache in general: `llama_kv_cache` loops over `n_layer_all` and only `qwen35`, `qwen35moe`, `step35`, `hy_v3` pass a filter dropping them (they get a separate MTP context instead, allocated only when speculative decoding is on). Everywhere else — `glm4-moe`, `bailingmoe2`/`3`, `deepseek32`, `exaone-moe`, `mimo2`, `cohere2moe` — the MTP block really is a cache layer. `n_layer()` **does** bound the recurrent state and clears `is_recr`/`is_swa` past it.
- **`_ARCH_INDEXER_KV`** — indexer keys live in the **main** KV cache as an extra f32 stream (`hparams.indexer_kv`). Only `minimax-m3`; reported as its own additive line.
- **`_compressed_groups()`** — every other sparse-attention arch puts them in a side cache at the run's **K quant, not f32**: `deepseek32`/`glm-dsa` get one full-context K-only cache with `head_count_kv` forced to 1 and the head dim replaced by `attention.indexer.key_length`; `deepseek4` gets a compressed one. Charging them an f32 full-context stream instead overstates the width ~4x and leaves it out of the headline.
- **`_ARCH_K_ONLY_CACHE`** — architectures forcing K-only storage **without** declaring MLA head dims: `deepseek4` fakes `is_mla()` (`dsv4_make_k_only`) purely to get `has_v = false`, so its `attention.value_length` must be zeroed exactly as for real MLA.
- **`_ARCH_NO_KVARN`** — architectures branching to a bespoke cache **before** llama-model.cpp's KVarN test, so they never receive the params (`deepseek32`, `glm-dsa`, `deepseek4`). `-ctk kvarnN` there silently stores `KVARN_FALLBACK`, which the report substitutes and labels.
- **Looped transformers** (`{arch}.num_loops`, e.g. `nanbeige`) run `block_count` physical blocks several times and **every pass gets its own cache layer**; the loader tiles the per-layer arrays to `n_layer_all = block_count * num_loops`. `_n_loops()` only _reads_ the key — the expansion is `ModelKV.n_loops` — so layer groups and the SWA pattern here are derived on the **physical** count, which is also what llama.cpp does (the generic `set_swa_pattern` runs before tiling).
- **DeepSeek-V4** is the one arch whose token cache is not where its context lives: every layer is sliding-window at 128 tokens, and the history sits in three K-only side caches (`llama_kv_cache_dsv4`: CSA, HCA, indexer LID) of `GGML_PAD(ceil(n_ctx / ratio), 256)` cells per sequence, tail-less, each with a context-independent f32 compressor ring. Without them the model sizes at ~1% of its real cache.
- **The `tN` column means less than usual on these architectures**, so `_kv_tail_caveats()` prints what the tail can actually reach: side caches are constructed with `tail_tokens = 0`, and `llama-context.cpp` clamps the SWA tail to the window (`min(N, n_swa)`). On DeepSeek-V4 that makes `t128`/`t1024`/`t2048` one and the same allocation, protecting only the ~5 MiB raw window. The byte figures were always right; only the labels overstated the knob.

### `scripts/llama-cpp-changelog.py` — deterministic changelog dumper

Dumps a markdown changelog between two git refs of any llama.cpp fork. Repo defaults to the active `fork:` in the source recipe (`--repo owner/name` to override). `from` defaults to the recipe's `# Last sync with main at <tag>` comment, else the active `version:`, else the commented-out `# version:` for the selected repo; `to` defaults to the repo's latest **stable** release tag (`preview-*` skipped). Handles `bNNNN` and `vX.Y.Z` tags. Git fallback cache is per-fork at `~/.cache/llama-cpp-changelog/<repo>.git`.

Sections: header (refs, dates, counts), tags in range with dates + URLs, PRs merged in range (filtered by merge-commit SHA, body excerpt up to 1200 chars), and direct commits with no PR. PRs need authenticated `gh` (or `GITHUB_TOKEN`/`GH_TOKEN`); tags/commits work unauthenticated but rate-limited.

### `sample-data/context-bench/` — long-context recall benchmark

Measures how well a model recalls facts scattered through a long context, and how that degrades under a quantized KV cache.

- **Books** `16k.txt` … `256k.txt` — public-domain Gutenberg titles, license boilerplate stripped, each sized to fill ~70–90% of the named window, with 20 strict-fact questions appended under a `QUESTIONS` section. Deliberately obscure, recently-digitised titles so answers must come from the context, not training data. When replacing one, keep it well under its window and regenerate the answer key.
- **Answer keys** `<size>.answers.txt` — `A1`–`A20` with source line numbers in `[brackets]`.
- **`AGENTS.md`** — the system prompt for the model under test (no tools; answer from the supplied text only; leave blanks when unknown).
- **`run_benchmark.py`** — reads a Pydantic-validated TOML config (one `[model tag]` table each: `url` defaulting to localhost, optional `api_key`/`api_key_env`/`model_name`, required `ctx-size` list, optional `temperature`/`max_tokens`/`timeout`; an optional `[*]` table supplies defaults). Sends `AGENTS.md` + each book via the `openai` client and grades with normalized string matching (case-insensitive; ignores articles, currency symbols, separators, spacing; number-words → digits). Writes a TOML report keyed `[model tag.context size]` with `raw_answers`, `outcomes` (`PASS`/`NO ANSWER`/`WRONG`) and `grade` = `(#PASS − #WRONG)/20`. Grading is deterministic, so a correct-but-off-format answer scores `WRONG` — `raw_answers` is kept for inspection.

## Common commands

```bash
# Environments (building llama-cpp from source takes a while; binary envs don't compile)
pixi install -e llamacpp-source-cuda
pixi install -e agents

# Serving
pixi run -e llamacpp-source-cuda start-server                  # background, logs to llama-server.log
pixi run -e llamacpp-source-cuda start-server --host-ram 16G   # simulate a 16 GiB host (cgroup v2)
pixi run -e llamacpp-source-cuda stop-server                   # SIGTERM, then SIGKILL
pixi run -e llamacpp-source-cuda restart-server
pixi run -e llamacpp-source-cuda llama-list-devices
pixi run -e llamacpp-source-cuda llama-hello                   # smoke test with llama-cli

# Agents. The task takes exactly one positional arg (the workspace, `-` for a temp
# dir); everything else MUST come after `--`, including --with-git and --bind.
pixi run pi /path/to/workspace
pixi run pi /path/to/workspace -- --with-git
pixi run pi-unsafe /path/to/ws            # full host access, debugging only
pixi run claude /path/to/workspace        # sandboxed, --dangerously-skip-permissions
pixi run claude . -- --with-git --resume
pixi run herdr

# …or, after `pixi r install`, from any directory (the wrapper supplies the cwd):
pi --with-git
claude --with-git --resume

# Benchmarks and analysis
pixi run -e llamacpp-source-cuda perplexity -c perplexity.yaml   # edit/duplicate the yaml first
pixi run perplexity-report perplexity.log -o perplexity-report
pixi run llama-benchy
pixi run context-bench sample-data/context-bench/config.toml -o results.toml
pixi run gguf-meta-extract https://huggingface.co/unsloth/GLM-5.2-GGUF/tree/main/UD-IQ1_S -o glm.csv
pixi r llama-cpp-changelog [from] [to]

# Building packages
pixi build
pixi build --path pixi-recipes/llama-cpp-source
```

To use the file-viewer plugin, bind a key in `~/.config/herdr/config.toml` (e.g. `prefix+f` → `herdr plugin action invoke open-file-viewer --plugin herdr-file-viewer`) and `herdr server reload-config`. Optional renderers (`glow`/`delta`/`bat`) come from `pixi r install-file-viewer-renderers`.

## Rules for coding agents

- **Always run `pixi r lint` after changing files**, and fix what it reports. Linting rewrites files, so re-read from disk before editing further.
- **After changing anything under `pixi-recipes/`, run `pixi install -e <env>`** for every environment using that recipe. A green lint does not mean the recipe builds — resolve "no candidates were found" / "invalid value" before calling it done.
- **Never edit `pixi.lock`** — regenerate with `pixi lock` (or `pixi lock -e <env>`).
- **Never change `fork:` in a llama-cpp recipe**, and never hardcode a commit SHA where the templated `${{ fork }}`/`${{ version }}` belongs.
- **`${PREFIX}` only exists inside build scripts** — never reference it elsewhere.
- **Symlinks into `${PREFIX}/bin` must be relative** (`../opt/llama/...`) for prefix portability.
- **Windows scripts run under the MSYS2 bash shipped by the environment** — the default feature pins `m2-bash`, `m2-coreutils`, `m2-grep`, `m2-sed` on win-64, because a plain `bash` from PATH on Windows resolves to WSL, which discards the pixi environment. Don't use `jq` (not on conda-forge for win-64), `nc`, `pkill`/`pgrep`, or `ln -s` in cross-platform scripts. Use `node -e` for JSON, `curl` for port checks (Windows ships it in System32), `taskkill` behind an `$OSTYPE == msys*` branch, and `cp -r` or an NTFS junction (`cmd //c 'mklink /J <link> <target>'`, no admin rights needed) instead of symlinks. Add another `m2-*` package if a script needs a further external command.
- **All three platforms are targeted**: `linux-64`, `linux-aarch64`, `win-64`.
- **`CLAUDE.md` is a symlink to `AGENTS.md`**, and **`.claude/skills` is a symlink to `.agents/skills/`**, for Claude Code compatibility.
- **Never run `pixi install -e agents` from inside the bwrap sandbox.** The sandbox binds the host's `~/.pi/agent/settings.json` over the package-owned copy, so re-extracting `pi-extensions` fails with `failed to unlink … home/.pi/agent/settings.json` (EBUSY on a mountpoint) and leaves the env half-extracted. Any `pixi run <task>` in `agents` triggers an implicit install whenever a local recipe changed, so it hits the same wall. Repair, and reinstall, from the host.
- **`--host-ram <size>` simulates a smaller host** by launching llama-server in a transient systemd user scope with a cgroup v2 `MemoryMax` and swap off. That is the only limit that accounts the page cache holding the mmap'd weights — RLIMIT_RSS is unenforced and RLIMIT_AS caps address space, not residency. Linux with a delegated memory controller only. Caveats: page cache warmed by an earlier run is charged to that run's cgroup and stays free, and mlock'd or pinned memory that doesn't fit gets the server OOM-killed rather than paged (use `load-mode = mmap`).
