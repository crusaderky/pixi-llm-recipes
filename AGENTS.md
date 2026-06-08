# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that serves multiple purposes:

1. **Builds and packages llama.cpp** as a conda/pixi package using **pixi-build** (rattler-build backend), compiling from source for multiple hardware backends (CPU, CUDA, Vulkan).
2. **Packages pi-extensions** — a curated set of pi coding agent plugins.
3. **Runs the pi coding agent** in a bubblewrap sandboxed environment with local LLM inference.
4. **Benchmarks LLM inference** via `llama-benchy`.

## Key Technologies

- **pixi** — Cross-platform dependency/environment manager (conda-compatible)
- **pixi-build / rattler-build** — Conda recipe building system
- **llama.cpp** — Open-source LLM inference engine by ggml-org (MIT license), built from source
- **bubblewrap (bwrap)** — Containerized sandbox for running the pi agent securely
- **pi-coding-agent** — The pi coding agent framework (installed via npm)
- **pi-extensions** — Curated pi plugins installed via a conda package

## Project Structure

```
pixi-llm-recipes/
├── pixi.toml                         # Root workspace: features, environments, tasks
├── pixi.lock                         # Lockfile for reproducible builds
├── .gitignore                        # Excludes .pixi/ (envs) but keeps .pixi/config.toml
├── .gitattributes                    # Marks pixi.lock as binary
├── models.ini                        # llama-server preset config (multi-model)
├── scripts/
│   ├── bwrap-pi.sh                   # Bubblewrap sandbox wrapper for pi agent
│   ├── start-server.sh               # Background llama-server with logging
│   └── unsafe-pi.sh                  # Unsandboxed pi wrapper (full host access)
├── sample-data/
│   ├── wiki.test.raw                 # Wikitext-2 benchmark corpus for llama-perplexity
│   └── describe-me.jpg               # Arbitrary image for multimodal testing
└── pixi-recipes/
    ├── llama-cpp/
    │   ├── pixi.toml                 # Package workspace + build-variants (cpu/cuda/vulkan)
    │   ├── recipe.yaml               # Conda recipe: builds llama.cpp from source
    │   └── build.sh                  # CMake build + install script
    └── pi-extensions/
        ├── recipe.yaml               # Packages curated pi plugins
        └── build.sh                  # Runs `pi install` for each plugin
```

## Core Concepts

### Variants and Backends (llama-cpp)

Build variants are declared in `pixi-recipes/llama-cpp/pixi.toml`:

```toml
[workspace.build-variants]
backend = ["cpu", "cuda", "vulkan"]
```

The default build variant used by the root `pixi.toml` is `cuda`. The `BACKEND` env var controls which CMake flags are passed:

| Backend | CMake flag | Extra build deps |
|---------|-----------|-----------------|
| `cpu` | (none) | — |
| `cuda` | `-DGGML_CUDA=ON` | `cuda-nvcc`, `cuda-version =12.6` |
| `vulkan` | `-DGGML_VULKAN=ON` | `shaderc`, `libvulkan-headers`, `spirv-*` |

### The Build Recipe (`pixi-recipes/llama-cpp/recipe.yaml`)

Key aspects:

- **Version**: `b9518` (maps to llama.cpp git tag/rev)
- **Source**: Clones from `https://github.com/ggml-org/llama.cpp` at a pinned commit rev
- **Build script**: `build.sh` runs CMake + Ninja
- **Build string**: `${{ backend }}_${{ build_number }}`
- **Output**: Conda package named `llama-cpp`

### The Build Script (`pixi-recipes/llama-cpp/build.sh`)

1. Runs CMake with `-DCMAKE_INSTALL_BINDIR=opt/llama` and `-DCMAKE_INSTALL_LIBDIR=opt/llama` — executables and shared libraries land in `${PREFIX}/opt/llama`
2. Sets `RPATH=$ORIGIN` so executables find sibling backend DLLs (e.g. `libggml-cuda.so`) at runtime without `LD_LIBRARY_PATH`
3. Enables dynamic backend loading (`-DGGML_BACKEND_DL=ON`) and all CPU dispatch variants (`-DGGML_CPU_ALL_VARIANTS=ON`)
4. Symlinks `llama-*` executables and `rpc-server` into `${PREFIX}/bin` via relative paths (`../opt/llama/...`)

**Important**: Executables and DLLs must coexist in `opt/llama` so that `dlopen` can locate optional backend libraries at runtime.

### Root `pixi.toml` — Features & Environments

| Feature | Dependencies | Key Tasks |
|---------|-------------|-----------|
| `llama` | `llama-cpp` (cuda, from `pixi-recipes/llama-cpp`) | `start-server`, `stop-server`, `list-devices`, `download-model`, `llama-perplexity` |
| `pi` | `pi-coding-agent`, `pi-extensions`, `bubblewrap` | `pi`, `unsafe-pi`, `pi-export` |
| `llama-benchy` | `python =3.14`, `llama-benchy` (PyPI) | `llama-benchy` |

| Environment | Feature |
|------------|---------|
| `llama` | `llama` |
| `llama-benchy` | `llama-benchy` |
| `pi` | `pi` |

### `models.ini` — llama-server Preset Configuration

The `models.ini` file uses the native llama-server preset format (`--models-preset`). It defines multiple named model profiles served on demand:

| Section | Model | VRAM | Speed |
|---------|-------|------|-------|
| `Qwen3.6-35B-A3B` | byteshape/Qwen3.6-35B-A3B-MTP-GGUF (IQ4_XS) | ~17.6 GiB | ~56 tok/s |
| `MiniCPM5-1B` | openbmb/MiniCPM5-1B-GGUF (Q4_K_M) | ~0.7 GiB | ~455 tok/s |
| `Gemma4-E2B` | HauhauCS/Gemma-4-E2B-Uncensored | ~3.5 GiB | ~198 tok/s |
| `Gemma4-E4B` | HauhauCS/Gemma-4-E4B-Uncensored | ~5.5 GiB | ~130 tok/s |
| `Gemma4-12B` | unsloth/gemma-4-12b-it-GGUF (IQ4_XS) | ~6.4 GiB | ~72 tok/s |

Global settings include KV cache quantization (`q8_0`), flash attention, MTP speculation for Qwen, and reasoning budgets.

### `scripts/bwrap-pi.sh` — Bubblewrap Sandbox

Wraps the pi coding agent in a bubblewrap container:
- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir if `-` is passed)
- Binds `$CONDA_PREFIX` read-only; mounts `$CONDA_PREFIX/home/.pi` as `~/.pi` inside the sandbox
- Binds caches: `~/.cache/{ccache,pip,pre-commit,rattler,uv}` and `~/.config/rpiv-web-tools`
- Unsets all `PIXI_*` and `CONDA_*` env vars before exec to isolate the pi agent from the host environment
- Models config file: `models.$PIXI_ENVIRONMENT_NAME.json` (per-environment override; create this file next to `models.ini` if needed)
- Requires AppArmor profile at `/etc/apparmor.d/bwrap` (template provided in script comments)

### `scripts/unsafe-pi.sh` — Unsandboxed Pi Wrapper

Runs pi with full host access. Symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` and copies `settings.json`, then cleans up on exit. Use only for development/debugging.

### `pixi-recipes/pi-extensions` — Pi Plugin Package

Installs a pinned set of pi plugins into `$PREFIX/home/.pi/agent` during the conda build. Plugins installed:

- `pi-autoresearch@1.5.0`, `pi-btw@0.4.0`, `pi-llama-cpp@0.5.1`, `pi-ollama-cloud@0.5.0`, `pi-token-speed@0.3.1`
- `@juicesharp/rpiv-advisor@1.18.2`, `@juicesharp/rpiv-ask-user-question@1.18.2`
- `@tmustier/pi-usage-extension@0.3.2`

## Build System

### Configuration

All package workspaces use:

```toml
[workspace]
channels = ["https://prefix.dev/conda-forge"]
preview = ["pixi-build"]
platforms = ["linux-64"]

[package.build.backend]
name = "pixi-build-rattler-build"
version = "*"
```

`pixi-recipes/llama-cpp/pixi.toml` additionally declares `[workspace.build-variants]` for `backend`.

### Constraints

- **Platforms**: `linux-64` only
- **llama-cpp CUDA build**: Requires `cuda-nvcc` and `cuda-version =12.6` in the build environment
- **Root workspace preview feature**: `pixi-build` required

## Working with This Project

### Building Packages

```bash
# Build llama-cpp (default cuda backend, from root)
pixi build

# Build all variants (cpu, cuda, vulkan) from the recipe workspace
cd pixi-recipes/llama-cpp
pixi build
```

### Setting Up Environments

```bash
pixi install -e llama         # llama.cpp server (cuda build)
pixi install -e pi            # pi coding agent + extensions + bubblewrap
pixi install -e llama-benchy  # LLM benchmark tool
```

### Serving Models

```bash
pixi run -e llama start-server                              # Start llama-server in background (logs to llama-server.log)
pixi run -e llama stop-server                               # Kill llama-server process
pixi run -e llama list-devices                              # List available compute devices
pixi run -e llama download-model model=Qwen3.6-35B-A3B      # Download and smoke-test a model
```

### Running the Pi Agent

```bash
# Sandboxed (recommended)
pixi run -e pi pi /path/to/workspace

# Pass `-` to start in a temporary empty directory
pixi run -e pi pi -

# Unsandboxed (full host access, for debugging)
pixi run -e pi unsafe-pi /path/to/workspace
```

The sandbox mounts extensions from `$CONDA_PREFIX/home/.pi/agent` as `~/.pi` inside the container.

### Running Benchmarks

```bash
# Perplexity benchmark against wiki.test.raw (requires llama-server running on :8080)
pixi run -e llama llama-perplexity

# Throughput benchmark
pixi run -e llama-benchy llama-benchy
```

### Adding a New Backend (llama-cpp)

1. Add `"<name>"` to `backend` in `pixi-recipes/llama-cpp/pixi.toml` `[workspace.build-variants]`
2. Add a `case` branch in `pixi-recipes/llama-cpp/build.sh` with the relevant `-DGGML_*=ON` flag
3. Add `if: backend == "<name>"` blocks in `pixi-recipes/llama-cpp/recipe.yaml` for any new build/host/run dependencies

### Version Updates (llama-cpp)

1. Find the new tag and commit hash at `https://github.com/ggml-org/llama.cpp/releases`
2. Update `context.version` in `pixi-recipes/llama-cpp/recipe.yaml`
3. Update `source.rev` to the commit hash for the new tag
4. Run `pixi lock -e llama` to regenerate the lockfile
5. Test all backends

## File Reference

| File | Purpose |
|------|---------|
| `pixi.toml` | Root workspace: features, tasks, environments |
| `pixi.lock` | Locked dependency versions (binary; never edit) |
| `models.ini` | llama-server multi-model preset config |
| `llama-server.log` | Server log (gitignored) |
| `scripts/bwrap-pi.sh` | Bubblewrap sandbox wrapper for pi agent |
| `scripts/unsafe-pi.sh` | Unsandboxed pi wrapper (dev/debug only) |
| `sample-data/wiki.test.raw` | Wikitext-2 corpus for `llama-perplexity` |
| `sample-data/describe-me.jpg` | Image for multimodal testing |
| `pixi-recipes/llama-cpp/pixi.toml` | llama-cpp package + build-variants |
| `pixi-recipes/llama-cpp/recipe.yaml` | Conda recipe: source, deps, build string |
| `pixi-recipes/llama-cpp/build.sh` | CMake build + install + symlink script |
| `pixi-recipes/pi-extensions/recipe.yaml` | Packages pi plugin set |
| `pixi-recipes/pi-extensions/build.sh` | Runs `pi install` for each plugin |

## Conventions

- All llama-cpp backends share the same `recipe.yaml` — differences are controlled by the `backend` build variant
- The `.pixi/` directory contains build artifacts and environments (gitignored except `config.toml`)
- Built packages produce `.conda` files suitable for `pixi add` or `conda install`
- The `llama` environment uses the `cuda` backend by default
- The `pi` feature uses `pi-extensions` (a conda package) so plugins are versioned and reproducible
- Plugin state lives in `$CONDA_PREFIX/home/.pi/agent`; the sandbox bind-mounts it as `~/.pi`

## Notes for Coding Agents

- **Never edit `pixi.lock`** — regenerate with `pixi lock` or `pixi lock -e <env>`
- **Never hardcode git revisions** — update `source.rev` in `recipe.yaml` to the commit hash for the new tag
- **`build.sh` uses `${PREFIX}`** — set by rattler-build; never reference it outside build scripts
- **Symlinks use relative paths** (`../opt/llama/...`) — required for correct conda prefix portability
- **All workspaces target `linux-64` only** — cross-platform support requires additional logic
- **`bwrap-pi.sh` unsets all `PIXI_*`/`CONDA_*` vars** before calling pi — the agent must not see conda internals
- **`start-server.sh` starts llama-server in background** with logging to `llama-server.log`; use `stop-server` to kill it
- **Models file per environment**: the sandbox looks for `models.$PIXI_ENVIRONMENT_NAME.json`; if absent it falls back to nothing — create it when running a non-default pi environment
- **`unsafe-pi.sh` symlinks extensions** from the conda prefix into `~/.pi/agent` and always cleans up on exit via `trap`
- **AppArmor is required for bwrap** — the profile must be loaded (`sudo systemctl reload apparmor`) before running the sandboxed pi agent
- **`pi-extensions` pins plugin versions explicitly** — bump versions in `build.sh` and update `recipe.yaml` version when adding or upgrading plugins
- **`CLAUDE.md` is a symlink** to `AGENTS.md` for Claude Code compatibility
- **`.claude/skills` is a symlink** to `.agents/skills/` for Claude Code compatibility
