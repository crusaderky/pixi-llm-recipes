# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that serves multiple purposes:

1. **Builds and packages llama.cpp** as a conda/pixi package using **pixi-build** (rattler-build backend), compiling from source for multiple hardware backends (CPU, CUDA, Vulkan), or using pre-built binaries from upstream releases (CPU, Vulkan, ROCm).
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
│   ├── stop-server.sh                # Graceful llama-server shutdown
│   ├── unsafe-pi.sh                  # Unsandboxed pi wrapper (full host access)
│   └── diff-llama-cpp-variants.sh    # Compare llama-cpp recipe variants
├── sample-data/
│   ├── wiki.test.raw                 # Wikitext-2 benchmark corpus for llama-perplexity
│   ├── describe-me.jpg               # Arbitrary image for multimodal testing
│   └── README.md                     # Sample data documentation
└── pixi-recipes/
    ├── llama-cpp-source/
    │   ├── build.sh                  # Shared CMake build + install + symlink script
    │   ├── cpu/recipe.yaml           # CPU build recipe
    │   ├── cuda/recipe.yaml          # CUDA build recipe
    │   └── vulkan/recipe.yaml        # Vulkan build recipe
    ├── llama-cpp-binary/
    │   ├── build.sh                  # Copy files + create symlinks
    │   ├── cpu/recipe.yaml           # CPU binary recipe
    │   ├── vulkan/recipe.yaml        # Vulkan binary recipe
    │   └── rocm/recipe.yaml          # ROCm binary recipe
    └── pi-extensions/
        ├── recipe.yaml               # Packages curated pi plugins
        └── build.sh                  # Runs `pi install` for each plugin
```

## Core Concepts

### Variants and Backends (llama-cpp)

#### Source Builds

Build variants are organized in `pixi-recipes/llama-cpp-source/` with separate recipe directories per backend:

```
llama-cpp-source/
├── build.sh          # Shared CMake build script (reads BACKEND env var)
├── cpu/recipe.yaml   # CPU variant
├── cuda/recipe.yaml  # CUDA variant
└── vulkan/recipe.yaml # Vulkan variant
```

#### Binary Builds

Pre-built binaries from upstream GitHub releases (no build deps needed):

```
llama-cpp-binary/
├── build.sh          # Copy files + create symlinks
├── cpu/recipe.yaml   # CPU binary
├── vulkan/recipe.yaml # Vulkan binary
└── rocm/recipe.yaml  # ROCm binary
```

The `BACKEND` env var controls which CMake flags are passed:

| Backend | CMake flag | Extra build deps |
|---------|-----------|-----------------|
| `cpu` | (none) | — |
| `cuda` | `-DGGML_CUDA=ON` | `cuda-nvcc`, `cuda-version =12.6` |
| `vulkan` | `-DGGML_VULKAN=ON` | `shaderc` |

### The Build Recipe (`pixi-recipes/llama-cpp-source/*/recipe.yaml`)

Key aspects:

- **Version**: `b9553` (maps to llama.cpp git tag/rev)
- **Source**: Clones from `https://github.com/ggml-org/llama.cpp` at a pinned commit rev (`9e3b928fd8c9d14dbf15a8768b9fdd7e5c721d66`)
- **Build script**: `../build.sh` (shared across variants) runs CMake + Ninja
- **Build string**: `${{ backend }}_${{ build_number }}`
- **Output**: Conda package named `llama-cpp`

### The Build Script (`pixi-recipes/llama-cpp-source/build.sh`)

1. Runs CMake with `-DCMAKE_INSTALL_LIBDIR=opt/llama` and `-DCMAKE_INSTALL_BINDIR=opt/llama` — executables and shared libraries land in `${PREFIX}/opt/llama`
2. Sets `RPATH=$ORIGIN` so executables find sibling backend DLLs (e.g. `libggml-cuda.so`) at runtime without `LD_LIBRARY_PATH`
3. Enables dynamic backend loading (`-DGGML_BACKEND_DL=ON`), all CPU dispatch variants (`-DGGML_CPU_ALL_VARIANTS=ON`), RPC (`-DGGML_RPC=ON`), and disables tests/examples
4. Symlinks `llama-*` executables and `rpc-server` into `${PREFIX}/bin` via relative paths (`../opt/llama/...`)

**Important**: Executables and DLLs must coexist in `opt/llama` so that `dlopen` can locate optional backend libraries at runtime.

### Root `pixi.toml` — Features & Environments

| Feature | Dependencies | Key Tasks |
|---------|--------------|-----------|
| `llamacpp` | `llama-cpp` (from `pixi-recipes`) | `llama-help`, `llama-version`, `list-devices`, `start-server`, `download-model`, `llama-perplexity` |
| `llamacpp-source-cpu` | `llama-cpp` (cpu compiled from sources) | — |
| `llamacpp-source-cuda` | `llama-cpp` (cuda compiled from sources) | — |
| `llamacpp-source-vulkan` | `llama-cpp` (vulkan compiled from sources) | — |
| `llamacpp-binary-cpu` | `llama-cpp` (cpu pre-built binary) | — |
| `llamacpp-binary-vulkan` | `llama-cpp` (vulkan pre-built binary) | — |
| `llamacpp-binary-rocm` | `llama-cpp` (rocm pre-built binary) | — |
| `pi` | `pi-coding-agent`, `pi-extensions` (from `pixi-recipes/pi-extensions`), `bubblewrap`, `jq` | `pi`, `pi-unsafe`, `pi-export` |
| `pytools` | `python =3.14`, `llama-benchy` (PyPI), `huggingface_hub`, `transformers` etc. | `llama-benchy`, `hf`, `download-gemma-drafters` |

| Environment | Feature(s) |
|------------|-----------|
| `llamacpp-source-cpu` | `llamacpp` + `llamacpp-source-cpu` |
| `llamacpp-source-cuda` | `llamacpp` + `llamacpp-source-cuda` |
| `llamacpp-source-vulkan` | `llamacpp` + `llamacpp-source-vulkan` |
| `llamacpp-binary-cpu` | `llamacpp` + `llamacpp-binary-cpu` |
| `llamacpp-binary-vulkan` | `llamacpp` + `llamacpp-binary-vulkan` |
| `llamacpp-binary-rocm` | `llamacpp` + `llamacpp-binary-rocm` |
| `pytools` | `pytools` |
| `pi` | `pi` |

### `models.ini` — llama-server Preset Configuration

The `models.ini` file uses the native llama-server preset format (`--models-preset`). It defines multiple named model profiles served on demand:

| Section | Model | VRAM | Speed |
|---------|-------|------|-------|
| `Qwen3.6-35B-A3B` | byteshape/Qwen3.6-35B-A3B-MTP-GGUF:Qwen3.6-35B-A3B-IQ4_XS-3.97bpw | ~17.6 GiB | ~56 tok/s |
| `MiniCPM5-1B` | openbmb/MiniCPM5-1B-GGUF:Q4_K_M | ~0.7 GiB | ~455 tok/s |
| `Gemma4-E2B` | unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL | ~2.6 GiB | ~222 tok/s |
| `Gemma4-E4B` | unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL | ~4.2 GiB | ~137 tok/s |
| `Gemma4-12B` | unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL | ~6.7 GiB | ~70 tok/s |

Global settings include Jinja templating, flash attention, KV cache quantization (`q8_0`), reasoning budgets, and `models-max = 1`.

### `scripts/bwrap-pi.sh` — Bubblewrap Sandbox

Wraps the pi coding agent in a bubblewrap container:
- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir if `-` is passed)
- Binds `$CONDA_PREFIX` read-only; mounts `$CONDA_PREFIX/home/.pi` as `~/.pi` inside the sandbox
- Binds caches: `~/.cache/{ccache,pip,pre-commit,rattler,uv}` and `~/.config/rpiv-web-tools`
- Creates and bind-mounts `~/.pi/agent/sessions` and `~/.pi/agent/auth.json`
- Bind-mounts `$PIXI_ROOT` (typically `~/.pixi`) read-only
- Unsets all `PIXI_*`, `CONDA_*`, and `INIT_CWD` env vars before exec to isolate the pi agent from the host environment
- Uses `--unshare-all --share-net` for additional isolation
- Models config file: `models.$PIXI_ENVIRONMENT_NAME.json` (per-environment override; create this file next to `models.ini` if needed)
- Requires AppArmor profile at `/etc/apparmor.d/bwrap` (template provided in script comments)

### `scripts/unsafe-pi.sh` — Unsandboxed Pi Wrapper

Runs pi with full host access. Symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` and copies `settings.json`, then cleans up on exit. Handles `-` argument by creating a temp directory. Unsets `PIXI_*`, `CONDA_*`, and `INIT_CWD` env vars. Use only for development/debugging.

### `pixi-recipes/pi-extensions` — Pi Plugin Package

Installs a pinned set of pi plugins into `$PREFIX/home/.pi/agent` during the conda build. Plugins installed:

- `pi-autoresearch@1.6.0`, `pi-btw@0.4.0`, `pi-llama-cpp@0.6.0`, `pi-ollama-cloud@0.6.0`, `pi-token-speed@0.3.1`
- `@juicesharp/rpiv-advisor@1.18.2`, `@juicesharp/rpiv-ask-user-question@1.18.2`
- `@tmustier/pi-usage-extension@0.3.2`

## Build System

### Configuration

All package workspaces use:

```toml
[workspace]
channels = ["https://prefix.dev/conda-forge"]
preview = ["pixi-build"]
platforms = ["linux-64", "linux-aarch64", "win-64"]

[package.build.backend]
name = "pixi-build-rattler-build"
version = "*"
```

### Constraints

- **Platforms**: `linux-64`, `linux-aarch64`, and `win-64`

## Working with This Project

### Building Packages

```bash
# Build from root (selects default variant via environment)
pixi build

# Build a specific backend variant
cd pixi-recipes/llama-cpp-source/cuda
pixi build
```

### Setting Up Environments

```bash
pixi install -e llamacpp-source-cuda   # llama.cpp server (cuda build)
pixi install -e pi                     # pi coding agent + extensions + bubblewrap
pixi install -e pytools                 # LLM benchmark tool & py-utils
```

### Serving Models

```bash
pixi run -e llamacpp-source-cuda start-server       # Start llama-server in background (logs to llama-server.log)
pixi run -e llamacpp-source-cuda stop-server         # Graceful llama-server shutdown (SIGTERM → SIGKILL)
pixi run -e llamacpp-source-cuda restart-server      # Stop + start in one command
pixi run -e llamacpp-source-cuda list-devices        # List available compute devices
pixi run -e llamacpp-source-cuda llama-help          # llama-server help
pixi run -e llamacpp-source-cuda download-model model=Qwen3.6-35B-A3B  # Download and smoke-test a model
```

**Note**: Binary environments (`llamacpp-*-binary`) skip compilation entirely, making them much faster to set up. They provide pre-built binaries from upstream llama.cpp releases for CPU, Vulkan, and ROCm backends. (No Linux CUDA binary is provided upstream — use `llamacpp-source-cuda` for CUDA.)

### Running the Pi Agent

```bash
# Sandboxed (recommended)
pixi run -e pi pi /path/to/workspace

# Pass `-` to start in a temporary empty directory
pixi run -e pi pi -

# Unsandboxed (full host access, for debugging)
pixi run -e pi pi-unsafe /path/to/workspace
```

The sandbox mounts extensions from `$CONDA_PREFIX/home/.pi/agent` as `~/.pi` inside the container.

### Running Benchmarks

```bash
# Perplexity benchmark against wiki.test.raw (requires llama-server running on :8080)
pixi run -e llamacpp-source-cuda llama-perplexity

# Throughput benchmark
pixi run -e pytools llama-benchy
```

### Adding a New Backend (llama-cpp)

1. Create a new directory `pixi-recipes/llama-cpp-source/<name>/` with a `recipe.yaml`
2. The shared `build.sh` reads `BACKEND` env var — add a `case` branch with the relevant `-DGGML_*=ON` flag
3. Add conditional dependencies in the recipe.yaml for `if: backend == "<name>"` blocks

### Version Updates (llama-cpp)

1. Find the new tag and commit hash at `https://github.com/ggml-org/llama.cpp/releases`
2. Update `context.version` in all three `pixi-recipes/llama-cpp-source/{cpu,cuda,vulkan}/recipe.yaml`
3. Update `source.rev` to the commit hash for the new tag in all three recipes
4. Run `pixi lock` to regenerate the lockfile
5. Test all backends

## File Reference

| File | Purpose |
|------|---------|
| `pixi.toml` | Root workspace: features, tasks, environments |
| `pixi.lock` | Locked dependency versions (binary; never edit) |
| `models.ini` | llama-server multi-model preset config |
| `llama-server.log` | Server log (gitignored) |
| `scripts/bwrap-pi.sh` | Bubblewrap sandbox wrapper for pi agent |
| `scripts/stop-server.sh` | Graceful llama-server shutdown (SIGTERM → SIGKILL) |
| `scripts/start-server.sh` | Background llama-server with logging |
| `scripts/unsafe-pi.sh` | Unsandboxed pi wrapper (dev/debug only) |
| `sample-data/wiki.test.raw` | Wikitext-2 corpus for `llama-perplexity` |
| `sample-data/describe-me.jpg` | Image for multimodal testing |
| `sample-data/README.md` | Sample data documentation |
| `pixi-recipes/llama-cpp-source/build.sh` | Shared CMake build + install + symlink script |
| `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` | CPU build recipe |
| `pixi-recipes/llama-cpp-source/cuda/recipe.yaml` | CUDA build recipe |
| `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml` | Vulkan build recipe |
| `pixi-recipes/llama-cpp-binary/build.sh` | Copy files + create symlinks for pre-built binaries |
| `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml` | CPU binary recipe |
| `pixi-recipes/llama-cpp-binary/vulkan/recipe.yaml` | Vulkan binary recipe |
| `pixi-recipes/llama-cpp-binary/rocm/recipe.yaml` | ROCm binary recipe |
| `pixi-recipes/pi-extensions/recipe.yaml` | Packages pi plugin set |
| `pixi-recipes/pi-extensions/build.sh` | Runs `pi install` for each plugin |

## Conventions

- All llama-cpp backends share the same `build.sh` — differences are controlled by the `BACKEND` env var and conditional recipe dependencies
- The `.pixi/` directory contains build artifacts and environments (gitignored except `config.toml`)
- Built packages produce `.conda` files suitable for `pixi add` or `conda install`
- The `llamacpp-source-cuda` environment uses the `cuda` backend by default
- The `pi` feature uses `pi-extensions` (a conda package) so plugins are versioned and reproducible
- Plugin state lives in `$CONDA_PREFIX/home/.pi/agent`; the sandbox bind-mounts it as `~/.pi`

## Notes for Coding Agents

- **Never edit `pixi.lock`** — regenerate with `pixi lock` or `pixi lock -e <env>`
- **Never hardcode git revisions** — update `source.rev` in all three `recipe.yaml` files to the commit hash for the new tag
- **`build.sh` uses `${PREFIX}`** — set by rattler-build; never reference it outside build scripts
- **Symlinks use relative paths** (`../opt/llama/...`) — required for correct conda prefix portability
- **All workspaces target `linux-64`, `linux-aarch64`, and `win-64`** — cross-platform support requires additional logic
- **`bwrap-pi.sh` unsets all `PIXI_*`/`CONDA_*`/`INIT_CWD` vars** before calling pi — the agent must not see conda internals
- **`start-server.sh` starts llama-server in background** with logging to `llama-server.log`; use `stop-server` to gracefully kill it
- **`stop-server.sh` uses SIGTERM first, then SIGKILL after timeout** — graceful shutdown pattern
- **Models file per environment**: the sandbox looks for `models.$PIXI_ENVIRONMENT_NAME.json`; if absent it falls back to nothing — create it when running a non-default pi environment
- **`unsafe-pi.sh` symlinks extensions** from the conda prefix into `~/.pi/agent` and always cleans up on exit via `trap`
- **AppArmor is required for bwrap** — the profile must be loaded (`sudo systemctl reload apparmor`) before running the sandboxed pi agent
- **`pi-extensions` pins plugin versions explicitly** — bump versions in `build.sh` and update `recipe.yaml` version when adding or upgrading plugins
- **`CLAUDE.md` is a symlink** to `AGENTS.md` for Claude Code compatibility
- **`.claude/skills` is a symlink** to `.agents/skills/` for Claude Code compatibility
