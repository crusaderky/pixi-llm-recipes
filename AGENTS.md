# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that builds and packages **llama.cpp** as a conda/pixi package using **pixi-build** (rattler-build backend). It produces pre-built llama.cpp binaries for multiple hardware backends: CPU, Vulkan, and ROCm.

## Key Technologies

- **pixi** — Cross-platform dependency/environment manager (conda-compatible)
- **pixi-build / rattler-build** — Conda recipe building system
- **conda recipes** — The `recipe.yaml` defines how llama.cpp is packaged as a conda package
- **llama.cpp** — Open-source LLM inference engine by ggml-org (MIT license)

## Project Structure

```
pixi-llm-recipes/
├── pixi.toml                  # Root workspace: depends on llama-cpp from vulkan backend
├── pixi.lock                  # Lockfile for reproducible builds
├── .gitignore                 # Excludes .pixi/ (envs) but keeps .pixi/config.toml
├── .gitattributes             # Marks pixi.lock as binary
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
- **Output**: Produces a `.conda` package named `llama-cpp`

### The Install Script (`build.sh`)

The `build.sh` script:

1. Creates `${PREFIX}/bin` and `${PREFIX}/opt/llama`
2. Separates `.so` libraries → `${PREFIX}/opt/llama` (needed for runtime DLL discovery)
3. Installs executables (`llama*`, `rpc-server`) → `${PREFIX}/opt/llama`, then symlinks them into `${PREFIX}/bin`
4. Skips `LICENSE`, `build_env.sh`, and `conda_build.*` files
5. Exits with error on unknown files

**Important**: DLLs and executables must coexist in the same directory so llama.cpp can find its optional backend DLLs (e.g., `libggml-vulkan.so`).

### Root `pixi.toml`

The root workspace depends on `llama-cpp = { path = "llama-cpp/vulkan" }`, meaning the default environment includes the **vulkan** backend by default.

## Build System

### Configuration

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

## Working with This Project

### Building

To build a specific backend variant:

```bash
cd llama-cpp/<backend>
pixi build
```

Or at the root to use the default (vulkan):

```bash
pixi build
```

### Adding a New Backend

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
| `pixi.toml` | Root workspace definition, default dependency |
| `pixi.lock` | Locked dependency versions (mark as binary in git) |
| `llama-cpp/recipe.yaml` | Conda recipe: metadata, sources, build, requirements |
| `llama-cpp/build.sh` | Install script for packaging |
| `llama-cpp/<backend>/pixi.toml` | Variant workspace config |
| `llama-cpp/<backend>/variants.yaml` | Variant parameter (`backend` value) |

## Conventions

- All backends share the same `recipe.yaml` — differences are controlled purely by the `backend` variant
- The `.pixi/` directory contains build artifacts and environments (gitignored except config)
- No Python code, no tests — this is purely a build/packaging project
- The built package produces a `.conda` file suitable for `pixi add` or `conda install`

## Notes for Coding Agents

- **Never edit `pixi.lock`** — regenerate it with `pixi lock`
- **Never hardcode SHA256** — recalculate from the actual tarball download
- **The `build.sh` uses `${PREFIX}`** — this is set by rattler-build during the build
- **Symlinks in build.sh use relative paths** (`../opt/llama/...`) — important for the conda prefix structure
- **All workspaces target `linux-64` only** — cross-platform support would require additional logic
