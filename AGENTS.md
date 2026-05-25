# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that serves multiple purposes:

1. **Builds and packages llama.cpp** as a conda/pixi package using **pixi-build** (rattler-build backend), producing pre-built binaries for multiple hardware backends (CPU, Vulkan, ROCm).
2. **Runs the pi coding agent** in a bubblewrap sandboxed environment with model-based LLM inference.
3. **Provides a forge proxy** (LM Studio-compatible) for local LLM serving.

## Key Technologies

- **pixi** — Cross-platform dependency/environment manager (conda-compatible)
- **pixi-build / rattler-build** — Conda recipe building system
- **llama.cpp** — Open-source LLM inference engine by ggml-org (MIT license)
- **bubblewrap (bwrap)** — Containerized sandbox for running the pi agent securely
- **forge** — LM Studio-compatible proxy server for LLM inference
- **pi-coding-agent** — The coding agent framework (installed via npm)

## Project Structure

```
pixi-llm-recipes/
├── pixi.toml                  # Root workspace: features, environments, tasks
├── pixi.lock                  # Lockfile for reproducible builds
├── .gitignore                 # Excludes .pixi/ (envs) but keeps .pixi/config.toml
├── .gitattributes             # Marks pixi.lock as binary
├── models.json                # Pi provider/model configuration (OpenAI-compatible)
├── bwrap-pi.sh                # Bubblewrap sandbox wrapper for pi agent
└── llama-cpp/
    ├── recipe.yaml            # Main conda build recipe (shared across all backends)
    ├── build.sh               # Install script: splits binaries into PREFIX/bin and PREFIX/opt/llama
    ├── cpu/
    │   ├── pixi.toml          # CPU variant workspace definition
    │   └── variants.yaml      # backend: [cpu]
    ├── vulkan/
    │   ├── pixi.toml          # Vulkan variant workspace definition
    │   └── variants.yaml      # backend: [vulkan]
    └── rocm/
        ├── pixi.toml          # ROCm variant workspace definition
        └── variants.yaml      # backend: [rocm]
```

## Core Concepts

### Variants and Backends

Each backend subdirectory (`cpu/`, `vulkan/`, `rocm/`) contains:

- **pixi.toml** — Defines the workspace and points to `../recipe.yaml` for the build config
- **variants.yaml** — Specifies which `backend` variant to use

The `backend` value controls which pre-built binary tarball is downloaded from llama.cpp GitHub releases:

| Backend | URL suffix | SHA256 |
|---------|------------|--------|
| `cpu` | `-bin-ubuntu-x64.tar.gz` | `0a32013a7cca1c51652bba69799d793886372ec922dbc3aa0788a1f45a7059ba` |
| `vulkan` | `-bin-ubuntu-vulkan-x64.tar.gz` | `98e5edcf2f5d5e3d41aa791af0bc73d1ff8269e40b48558517cbe746982cfa37` |
| `rocm` | `-bin-ubuntu-rocm-7.2-x64.tar.gz` | `0805b002e8439cc5a95ef360382c2ad83236e9b8acd164af7faf76bbfe0f5076` |

### The Build Recipe (`recipe.yaml`)

Key aspects:

- **Version**: Pinned to `9305` (build number, mapped to llama.cpp release tag `b9305`)
- **Base URL**: `https://github.com/ggml-org/llama.cpp/releases/download/b${{ version }}`
- **Source**: Downloads pre-built tarballs for each backend variant
- **Build script**: `build.sh` handles the installation
- **Build number**: 0
- **Build string**: `${{ backend }}_h${{ hash }}_${{ build_number }}`
- **Output**: Produces a `.conda` package named `llama-cpp`

### The Install Script (`build.sh`)

The `build.sh` script:

1. Creates `${PREFIX}/bin` and `${PREFIX}/opt/llama`
2. Separates `.so` libraries → `${PREFIX}/opt/llama` (needed for runtime DLL discovery)
3. Installs executables (`llama*`, `rpc-server`) → `${PREFIX}/opt/llama`, then symlinks them into `${PREFIX}/bin`
4. Skips `LICENSE`, `build_env.sh`, and `conda_build.*` files
5. Exits with error on unknown files

**Important**: DLLs and executables must coexist in the same directory so llama.cpp can find its optional backend DLLs (e.g., `libggml-vulkan.so`).

### Root `pixi.toml` — Features & Environments

The root workspace defines three **features** (sets of dependencies and tasks) and three **environments**:

| Feature | Dependencies | Tasks |
|---------|-------------|-------|
| `llama` | `llama-cpp` (from vulkan backend) | `list-devices`, `serve-qwen36` |
| `pi` | `pi-coding-agent`, `bubblewrap` | `pi`, `pi-install`, `install-pi-extensions` |
| `forge` | `python = "=3.14"`, `forge-guardrails` (PyPI) | `forge-proxy` |

| Environment | Feature Used |
|------------|-------------|
| `llama` | `llama` |
| `pi` | `pi` |
| `forge` | `forge` |

The default environment is `llama` (vulkan backend). To use a specific environment:
```bash
pixi install --feature llama   # llama.cpp with vulkan
pixi install --feature pi      # pi coding agent sandbox
pixi install --feature forge   # LM Studio proxy
```

### `models.json` — Pi Provider Configuration

Configures llama.cpp as an OpenAI-compatible provider:
- Base URL: `http://localhost:8080/v1`
- Model: `Qwen3.6-35B-A3B-MTP-GGUF:IQ3_S`
- Naming: `qwen36`
- Special: reasoning enabled, uses Qwen chat template

### `bwrap-pi.sh` — Bubblewrap Sandbox

Wraps the pi coding agent in a bubblewrap container:
- Read-only root filesystem binding
- Shared network (`--share-net`)
- Binds project directory, `.pi` config, and pixi prefixes read-only
- Requires AppArmor profile configuration at `/etc/apparmor.d/bwrap`
- Passes through the `PIXI_ROOT` and `CONDA_PREFIX` into the sandbox

## Build System

### Configuration

All workspaces use the same configuration pattern:

```toml
[workspace]
channels = ["https://prefix.dev/conda-forge"]
preview = ["pixi-build"]
platforms = ["linux-64"]
```

### Build Backend

All variant workspaces use:

```toml
[package.build.backend]
name = "pixi-build-rattler-build"
version = "*"

[package.build.config]
recipe = "../recipe.yaml"
```

### Constraints

- **Platforms**: `linux-64` only
- **Build dependency**: `patchelf` (for binary patching in conda packages)
- **Root workspace preview feature**: `pixi-build` required

## Working with This Project

### Building llama.cpp Packages

To build a specific backend variant:

```bash
cd llama-cpp/<backend>
pixi build
```

Or at the root to use the default (vulkan):

```bash
pixi build
```

### Setting Up Environments

The root workspace defines three environments, each with distinct features:

```bash
pixi install --feature llama   # llama.cpp (vulkan by default)
pixi install --feature pi      # pi coding agent + bubblewrap sandbox
pixi install --feature forge   # LM Studio proxy (Python 3.14)
```

### Running the Pi Agent

```bash
pixi run --feature pi -- pi /path/to/workspace
```

This runs the pi agent inside a bubblewrap sandbox. The sandbox:
- Binds the project directory read-write
- Exposes llama.cpp via OpenAI-compatible API (configured in `models.json`)
- Shares network for llama.cpp server communication
- Restricts filesystem access to specified paths

### Running the Forge Proxy

```bash
pixi run --feature forge -- forge-proxy
```

Starts the LM Studio-compatible proxy on port 8081, forwarding to the local llama.cpp server on 8080.

### Adding a New Backend (llama.cpp)

1. Create a new directory `llama-cpp/<name>/`
2. Add `pixi.toml` (copy from another variant, update if needed)
3. Add `variants.yaml` with `backend: [<name>]`
4. Add the source URL and SHA256 to `recipe.yaml`
5. Update `build.sh` if the new backend has different file layout requirements

### Adding a New Variant

To add another backend (e.g., `cuda`, `metal`):

1. **recipe.yaml**: Add a new source entry with the `if: backend == "<name>"` condition
2. **llama-cpp/<name>/variants.yaml**: Set `backend: [<name>]`
3. **llama-cpp/<name>/pixi.toml**: Copy structure from existing variants

### Version Updates

To update llama.cpp:

1. Check the latest release at `https://github.com/ggml-org/llama.cpp/releases`
2. Update the `version` context variable in `recipe.yaml`
3. Update `base_url` tag reference
4. Calculate and update SHA256 hashes for the new tarball(s)
5. Test all backends

## File Reference

| File | Purpose |
|------|---------|
| `pixi.toml` | Root workspace: features, tasks, environments |
| `pixi.lock` | Locked dependency versions (mark as binary in git) |
| `llama-cpp/recipe.yaml` | Conda recipe: metadata, sources, build, requirements |
| `llama-cpp/build.sh` | Install script for packaging |
| `llama-cpp/<backend>/pixi.toml` | Variant workspace config |
| `llama-cpp/<backend>/variants.yaml` | Variant parameter (`backend` value) |
| `bwrap-pi.sh` | Bubblewrap sandbox wrapper for pi agent |
| `models.json` | Pi provider/model configuration (OpenAI-compatible) |

## Conventions

- All backends share the same `recipe.yaml` — differences are controlled purely by the `backend` variant
- The `.pixi/` directory contains build artifacts and environments (gitignored except config)
- The built llama.cpp package produces a `.conda` file suitable for `pixi add` or `conda install`
- The `llama` feature uses `llama-cpp` from the vulkan backend by default
- The `pi` feature runs inside a bubblewrap sandbox with restricted filesystem access
- The `forge` feature uses PyPI dependencies (`forge-guardrails`) alongside conda-forge Python

## Notes for Coding Agents

- **Never edit `pixi.lock`** — regenerate it with `pixi lock`
- **Never hardcode SHA256** — recalculate from the actual tarball download
- **The `build.sh` uses `${PREFIX}`** — this is set by rattler-build during the build
- **Symlinks in build.sh use relative paths** (`../opt/llama/...`) — important for the conda prefix structure
- **All workspaces target `linux-64` only** — cross-platform support would require additional logic
- **The `models.json` file is copied into `~/.pi/agent/` inside the sandbox** — it's not committed to the agent's filesystem
- **The bubblewrap sandbox requires AppArmor configuration** — see comments in `bwrap-pi.sh` for the required profile
- **Forge proxy forwards to llama.cpp at localhost:8080** — update `models.json` or `bwrap-pi.sh` if port changes
- **`pixi.lock` should be regenerated after any dependency change** — run `pixi lock --features <name>` for specific features
