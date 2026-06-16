# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that serves multiple purposes:

1. **Builds and packages llama.cpp** as a conda/pixi package using **pixi-build** (rattler-build backend), compiling from source for multiple hardware backends (CPU, CUDA, Vulkan), or using pre-built binaries from upstream releases (CPU, Vulkan, ROCm).
2. **Packages pi-extensions** — a curated set of pi coding agent plugins.
3. **Runs the pi coding agent** in a bubblewrap sandboxed environment with local LLM inference.
4. **Benchmarks LLM inference** via `llama-benchy`.
5. **Benchmarks long-context recall** (and how it degrades under quantized KV cache) via the `context-bench` harness in `sample-data/context-bench/`.

## Key Technologies

- **pixi** — Cross-platform dependency/environment manager (conda-compatible)
- **pixi-build / rattler-build** — Conda recipe building system
- **llama.cpp** — Open-source LLM inference engine by ggml-org (MIT license), built from source
- **bubblewrap (bwrap)** — Containerized sandbox for running the pi agent securely
- **pi-coding-agent** — The pi coding agent framework (installed via npm)
- **pi-extensions** — Curated pi plugins installed via a conda package
- **claude** — Claude Code CLI (`@anthropic-ai/claude-code`) installed via a conda package (fetches from npm)

## Project Structure

```
pixi-llm-recipes/
├── pixi.toml                         # Root workspace: features, environments, tasks
├── pixi.lock                         # Lockfile for reproducible builds
├── .gitignore                        # Excludes .pixi/ (envs) but keeps .pixi/config.toml
├── .gitattributes                    # Marks pixi.lock as binary
├── models.ini                        # llama-server preset config (multi-model)
├── scripts/
│   ├── bwrap-claude.sh               # Bubblewrap sandbox wrapper for Claude Code
│   ├── bwrap-pi.sh                   # Bubblewrap sandbox wrapper for pi agent
│   ├── diff-llama-cpp-variants.sh    # Compare llama-cpp recipe variants
│   ├── inject-pi-extensions.sh       # Merge pi-extensions packages into settings.json
│   ├── install-apparmor.sh           # Install AppArmor profile for bwrap (sudo/CI)
│   ├── start-server.sh               # Background llama-server with logging
│   ├── stop-server.sh                # Graceful llama-server shutdown
│   └── pi-unsafe.sh                  # Unsandboxed pi wrapper (full host access)
├── sample-data/
│   ├── wiki.test.raw                 # Wikitext-2 benchmark corpus for llama-perplexity
│   ├── describe-me.jpg               # Arbitrary image for multimodal testing
│   ├── context-bench/                # Long-context recall benchmark
│   │   ├── AGENTS.md                 # System prompt given to the model under test
│   │   ├── run_benchmark.py          # Prompts each model, grades answers, writes a TOML report
│   │   ├── config.toml               # Runner config (one table per model)
│   │   ├── 16k.txt … 256k.txt        # Books sized to fill 16k/32k/64k/128k/256k contexts (20 questions appended)
│   │   └── 16k.answers.txt …         # Reference answers (A1–A20) with source line numbers
│   └── README.md                     # Sample data documentation
└── pixi-recipes/
    ├── llama-cpp-source/
    │   ├── build.sh                  # Shared CMake build + install + symlink script
    │   ├── cpu/recipe.yaml           # CPU build recipe
    │   ├── cuda/recipe.yaml          # CUDA build recipe
    │   └── vulkan/recipe.yaml        # Vulkan build recipe
    ├── llama-cpp-binary/
    │   ├── build.sh                  # Linux: copy files + create symlinks
    │   ├── build.bat                 # Windows: copy exes + DLLs into bin
    │   ├── cpu/recipe.yaml           # CPU binary recipe
    │   ├── vulkan/recipe.yaml        # Vulkan binary recipe
    │   └── rocm/recipe.yaml          # ROCm binary recipe
    ├── claude/
    │   ├── recipe.yaml               # Claude Code conda package (fetches from npm)
    │   ├── build.sh                  # Linux: npm install --global into prefix
    │   └── build.bat                 # Windows: npm install --global into prefix
    └── pi-extensions/
        ├── recipe.yaml               # Packages curated pi plugins (pins in PLUGINS env var)
        ├── build.sh                  # Linux: runs `pi install` for each plugin
        └── build.bat                 # Windows: runs `pi install` for each plugin
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
├── build.sh          # Linux: copy files + create symlinks
├── build.bat         # Windows: copy exes + DLLs into bin
├── cpu/recipe.yaml   # CPU binary
├── vulkan/recipe.yaml # Vulkan binary
└── rocm/recipe.yaml  # ROCm binary
```

The recipes reference the build script as an extension-less `file: ../build`:
rattler-build resolves it to `build.sh` (bash) on Linux and `build.bat`
(cmd.exe) on Windows. Do not point `script.file` at the `.sh` file directly —
rattler-build would then run it with bash on Windows too, and its generated
`build_env.sh` chokes on Windows env vars like `ProgramFiles(x86)`.

The pinned upstream release tag lives in `context.version` of each binary
recipe.yaml and is passed to both build scripts as the `VERSION` env var.

On Windows there are no symlinks and no `opt/llama` split: executables and
DLLs are all copied into `%PREFIX%\bin`, which is on `PATH` in activated pixi
environments and lets executables find their DLLs and dynamically loaded
ggml backends.

The `BACKEND` env var controls which CMake flags are passed:

| Backend | CMake flag | Extra build deps |
|---------|-----------|-----------------|
| `cpu` | (none) | — |
| `cuda` | `-DGGML_CUDA=ON` | `cuda-nvcc`, `cuda-version =12.6` |
| `vulkan` | `-DGGML_VULKAN=ON` | `shaderc` |

### The Build Recipe (`pixi-recipes/llama-cpp-source/*/recipe.yaml`)

Key aspects:

- **Version**: `b9587` (maps to llama.cpp git tag/rev)
- **Source**: Clones from `https://github.com/ggml-org/llama.cpp` at a pinned commit rev (`d2e22ed975e3464ff8108542c840733b488f165f`)
- **Build script**: `../build.sh` (shared across variants) runs CMake + Ninja
- **Build string**: `${{ backend }}_${{ build_number }}`
- **Output**: Conda package named `llama-cpp`

### The Build Script (`pixi-recipes/llama-cpp-source/build.sh`)

1. Runs CMake with `-DCMAKE_INSTALL_LIBDIR=opt/llama` and `-DCMAKE_INSTALL_BINDIR=opt/llama` — executables and shared libraries land in `${PREFIX}/opt/llama`
2. Sets `RPATH=$ORIGIN` so executables find sibling backend DLLs (e.g. `libggml-cuda.so`) at runtime without `LD_LIBRARY_PATH`
3. Enables dynamic backend loading (`-DGGML_BACKEND_DL=ON`), all CPU dispatch variants (`-DGGML_CPU_ALL_VARIANTS=ON`), RPC (`-DGGML_RPC=ON`), and disables tests/examples
4. Symlinks `llama-*` executables and `rpc-server` into `${PREFIX}/bin` via relative paths (`../opt/llama/...`)

**Important**: Executables and DLLs must coexist in `opt/llama` so that `dlopen` can locate optional backend libraries at runtime.

### The Binary Build Script (`pixi-recipes/llama-cpp-binary/build.sh`)

Copies pre-built binaries from upstream releases into `${PREFIX}/opt/llama`, then symlinks `llama-*` executables into `${PREFIX}/bin`. The `VERSION` env var (from `context.version`) determines which upstream release to fetch.

On Windows (`build.bat`): executables and DLLs are all copied into `%PREFIX%\bin`, which is on `PATH` in activated pixi environments.

The binary recipes use `file: ../build` (extension-less) so rattler-build resolves to `build.sh` on Linux and `build.bat` on Windows.

### Root `pixi.toml` — Features & Environments

| Feature | Dependencies | Key Tasks |
|---------|--------------|-----------|
| `llamacpp` | `llama-cpp` (from `pixi-recipes`) | `llama-help`, `llama-version`, `llama-hello`, `llama-list-devices`, `start-server`, `llama-perplexity` |
| `llamacpp-source-cpu` | `llama-cpp` (cpu compiled from sources) | — |
| `llamacpp-source-cuda` | `llama-cpp` (cuda compiled from sources) | — |
| `llamacpp-source-vulkan` | `llama-cpp` (vulkan compiled from sources) | — |
| `llamacpp-binary-cpu` | `llama-cpp` (cpu pre-built binary) | — |
| `llamacpp-binary-vulkan` | `llama-cpp` (vulkan pre-built binary) | — |
| `llamacpp-binary-rocm` | `llama-cpp` (rocm pre-built binary) | — |
| `pi` | `pi-coding-agent`, `pi-extensions` (from `pixi-recipes/pi-extensions`), `bubblewrap` (Linux only) | `pi` (Linux only), `pi-unsafe`, `pi-export` |
| `claude` | `claude` (from `pixi-recipes/claude`), `bubblewrap` (Linux only) | `claude` (Linux only), `claude-unsafe` |
| `git` | `git` and `gh` (GitHub CLI from conda-forge) | `git`, `gh` |
| `pytools` | `python =3.14`, `llama-benchy` (PyPI), `huggingface_hub`, `transformers`, `openai`, `tomli-w` etc. | `llama-benchy`, `hf`, `context-bench` |

| Environment | Feature(s) |
|------------|-----------|
| `llamacpp-source-cpu` | `llamacpp` + `llamacpp-source-cpu` |
| `llamacpp-source-cuda` | `llamacpp` + `llamacpp-source-cuda` | — (FIXME: Linux ARM fails to compile) |
| `llamacpp-source-vulkan` | `llamacpp` + `llamacpp-source-vulkan` |
| `llamacpp-binary-cpu` | `llamacpp` + `llamacpp-binary-cpu` |
| `llamacpp-binary-vulkan` | `llamacpp` + `llamacpp-binary-vulkan` |
| `llamacpp-binary-rocm` | `llamacpp` + `llamacpp-binary-rocm` |
| `agents` | `pi` + `claude` + `git` + `pytools` |

### `models.ini` — llama-server Preset Configuration

The `models.ini` file uses the native llama-server preset format (`--models-preset`). It defines multiple named model profiles served on demand:

| Section | Model | VRAM | Speed |
|---------|-------|------|-------|
| `Qwen3.6-35B-A3B` | byteshape/Qwen3.6-35B-A3B-MTP-GGUF:Qwen3.6-35B-A3B-IQ4_XS-3.97bpw | 18 GiB | ~56 tok/s |
| `Qwen3.6-27B` | unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_M | 18 GiB | ~2 tok/s (does not fit) |
| `MiniCPM5-1B` | openbmb/MiniCPM5-1B-GGUF:Q4_K_M | 0.7 GiB | ~455 tok/s |
| `Gemma4-E2B` | unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL | 3.5 GiB | ~222 tok/s |
| `Gemma4-E4B` | unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL | 5.0 GiB | ~137 tok/s |
| `Gemma4-12B` | unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL | 6.7 GiB | ~112 tok/s (limited context) |
| `Gemma4-26B-A4B` | unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL | 15 GiB | ~32 tok/s |
| `Gemma4-31B` | unsloth/gemma-4-31B-it-qat-GGUF:UD-Q4_K_XL | 18 GB | ~2 tok/s (does not fit) |

Global settings include Jinja templating, flash attention, KV cache quantization (`q8_0`), reasoning budgets, and `models-max = 1`.

### `sample-data/context-bench/` — Long-Context Recall Benchmark

Measures how well a model recalls facts scattered through a long context, and how that degrades under pressure such as a quantized KV cache. It consists of:

- **Books** `16k.txt`, `32k.txt`, `64k.txt`, `128k.txt`, `256k.txt` — public-domain Project Gutenberg books, each sized so its text plus questions comfortably fills the named context window. The PG license boilerplate is stripped; 20 questions about strict, unambiguous facts (drawn from paragraphs spread evenly through the book) are appended under a `QUESTIONS` section.
- **Answer keys** `<size>.answers.txt` — reference answers `A1`–`A20`, each with the original source line number(s) in `[brackets]`.
- **`AGENTS.md`** — the system prompt handed to the model under test (no tools): answer `A1`–`A20` from the supplied text only, in the requested format, leaving an answer blank if unknown.
- **`run_benchmark.py`** — the runner (see below).

The books are deliberately obscure, recently-digitised titles so that answers must come from the context, not the model's training data. When bumping/replacing a book, keep it well under its target window (the existing ones fill ~70–90%) and regenerate the matching `.answers.txt`.

#### `run_benchmark.py`

Reads a Pydantic-validated TOML config (one `[model tag]` table each: `url` defaulting to localhost, optional `api_key`/`api_key_env`/`model_name`, required `max_context` accepting `65536`/`64k`/`1M`, and optional `temperature`/`max_tokens`/`timeout`). An optional `[*]` table supplies defaults applied to every model (per-model values override it). For every model it sends `AGENTS.md` (system) + each book (user) via the `openai` client, for **all books whose nominal size ≤ `max_context`** (cumulative). Answers are graded with normalized string matching (case-insensitive; ignores articles, currency symbols, separators and spacing; maps number-words to digits, e.g. `nine`→`9`). It writes a TOML report keyed `[model tag.context size]` with `raw_answers` (verbatim), `outcomes` (20× `PASS`/`NO ANSWER`/`WRONG`) and `grade` = `(#PASS − #WRONG)/20` in `[-1, 1]`. Grading is deterministic, so a correct-but-off-format answer can score `WRONG`; `raw_answers` is preserved for inspection.

### `scripts/bwrap-claude.sh` — Claude Code Bubblewrap Sandbox

Wraps Claude Code (`claude`) in a bubblewrap container using the current working directory:
- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir at `/tmp/claude` if `-` is passed)
- Binds `~/.claude`, `~/.claude.json` (Claude Code config/auth)
- Binds caches: `~/.cache/{ccache,claude,claude-cli-nodejs,pip,pre-commit,rattler,uv}`
- Binds `$CONDA_PREFIX` read-only (claude binary and Node.js runtime); also binds the pixi root read-only for shared packages
- Uses `--unshare-all --share-net --die-with-parent` for isolation; runs `claude --dangerously-skip-permissions`
- Requires AppArmor profile at `/etc/apparmor.d/bwrap` — same profile used by `bwrap-pi.sh`
- Optional `--with-git` flag: binds `~/.ssh`, `~/.gitconfig`, `~/.config/git`, `~/.git-credentials` (read-only) and `~/.config/gh` (read-write) so that `git push` and the `gh` CLI work inside the sandbox. The SSH agent socket (`SSH_AUTH_SOCK`) is accessible automatically when it lives under `/run/` (gnome-keyring/systemd default); if it lives under `/tmp` it is also bound automatically. The conda-forge `gh` from the `agents` environment is on `PATH` inside the sandbox (via `$CONDA_PREFIX/bin`), shadowing any system-installed snap version.

### `scripts/bwrap-pi.sh` — Bubblewrap Sandbox

Wraps the pi coding agent in a bubblewrap container:
- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir if `-` is passed)
- Binds `$CONDA_PREFIX` read-only; mounts `$CONDA_PREFIX/home/.pi` as `~/.pi` inside the sandbox
- Binds caches: `~/.cache/{ccache,pip,pre-commit,rattler,uv}` and `~/.config/rpiv-web-tools`
- Creates and bind-mounts `~/.pi/agent/sessions`, `auth.json`, `trust.json`, and `settings.json`
- Calls `inject-pi-extensions.sh` to merge pi-extensions packages into `settings.json`
- Bind-mounts `$PIXI_ROOT` (typically `~/.pixi`) read-only
- Unsets all `PIXI_*`, `CONDA_*`, and `INIT_CWD` env vars before exec to isolate the pi agent from the host environment
- Uses `--unshare-all --share-net --die-with-parent` for additional isolation
- Models config file: `models.$PIXI_ENVIRONMENT_NAME.json` (per-environment override; create this file next to `models.ini` if needed)
- Requires AppArmor profile at `/etc/apparmor.d/bwrap` — install it with `pixi run install-apparmor` (see `scripts/install-apparmor.sh`)
- Optional `--with-git` flag: binds `~/.ssh`, `~/.gitconfig`, `~/.config/git`, `~/.git-credentials` (read-only) and `~/.config/gh` (read-write) so that `git push` and the `gh` CLI work inside the sandbox. The SSH agent socket (`SSH_AUTH_SOCK`) is accessible automatically when it lives under `/run/` (gnome-keyring/systemd default); if it lives under `/tmp` it is also bound automatically. The conda-forge `gh` from the `agents` environment is on `PATH` inside the sandbox (via `$CONDA_PREFIX/bin`), shadowing any system-installed snap version.

### `scripts/pi-unsafe.sh` — Unsandboxed Pi Wrapper

Runs pi with full host access. Calls `inject-pi-extensions.sh` to merge pi-extensions packages into `settings.json`. Symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` (copies on Windows, where MSYS bash can't create symlinks; also forces `HOME=%USERPROFILE%` there so bash's `~` matches pi's home dir), then cleans up on exit. Handles `-` argument by creating a temp directory. Unsets all `PIXI_*` and `CONDA_*` env vars plus `INIT_CWD`. Use only for development/debugging.

### `pixi-recipes/pi-extensions` — Pi Plugin Package

Installs a pinned set of pi plugins into `$PREFIX/home/.pi/agent` during the conda build. The plugin pins live in the `PLUGINS` env var in `recipe.yaml`, which is consumed by both `build.sh` (Linux) and `build.bat` (Windows) via the extension-less `script.file: build` mechanism. See `recipe.yaml` for the current plugin list and versions.

`@juicesharp/rpiv-web-tools` is excluded from the plugin list — it is redundant with `pi-ollama-cloud`.

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

**`-e <env>` is only required when a task name exists in multiple environments.** Tasks that belong to exactly one environment (e.g. `pi`, `claude`, `gh`, `llama-benchy`) can be invoked with plain `pixi run <task>` — pixi selects the environment automatically. The llamacpp tasks (`start-server`, `llama-help`, etc.) exist in all six `llamacpp-*` environments, so `-e` is mandatory there to pick the right backend.

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
pixi install -e agents                 # pi agent, Claude Code, gh CLI, pytools
```

### Serving Models

```bash
pixi run -e llamacpp-source-cuda start-server       # Start llama-server in background (logs to llama-server.log)
pixi run -e llamacpp-source-cuda stop-server         # Graceful llama-server shutdown (SIGTERM → SIGKILL)
pixi run -e llamacpp-source-cuda restart-server      # Stop + start in one command
pixi run -e llamacpp-source-cuda llama-list-devices  # List available compute devices
pixi run -e llamacpp-source-cuda llama-help          # llama-server help
pixi run -e llamacpp-source-cuda llama-hello         # Quick smoke test with llama-cli
```

**Note**: Binary environments (`llamacpp-*-binary`) skip compilation entirely, making them much faster to set up. They provide pre-built binaries from upstream llama.cpp releases for CPU, Vulkan, and ROCm backends. (No Linux CUDA binary is provided upstream — use `llamacpp-source-cuda` for CUDA.)

### Running the Pi Agent

```bash
# Sandboxed (recommended)
pixi run pi /path/to/workspace

# Pass `-` to start in a temporary empty directory
pixi run pi -

# Unsandboxed (full host access, for debugging)
pixi run pi-unsafe /path/to/workspace
```

The sandbox mounts extensions from `$CONDA_PREFIX/home/.pi/agent` as `~/.pi` inside the container.

### Running Claude Code

```bash
# Sandboxed in the current directory (recommended)
pixi run claude

# With git push / gh CLI access
pixi run claude --with-git

# Pass additional args to claude (after --)
pixi run claude -- --resume
```

Claude Code is installed as a conda package in the `agents` environment (`pixi-recipes/claude/recipe.yaml`). The sandbox runs `claude --dangerously-skip-permissions` so no interactive prompts interrupt agent work. Requires the same AppArmor profile as `bwrap-pi.sh` — install with `pixi run install-apparmor`.

### Running Benchmarks

```bash
# Perplexity benchmark against wiki.test.raw (requires llama-server running on :8080)
pixi run -e llamacpp-source-cuda llama-perplexity

# Throughput benchmark
pixi run llama-benchy

# Long-context recall benchmark (edit the config first; one table per model endpoint)
pixi run context-bench sample-data/context-bench/config.toml -o results.toml
```

### Adding a New Backend (llama-cpp)

1. Create a new directory `pixi-recipes/llama-cpp-source/<name>/` with a `recipe.yaml`
2. The shared `build.sh` reads `BACKEND` env var — add a `case` branch with the relevant `-DGGML_*=ON` flag
3. Add conditional dependencies in the recipe.yaml for `if: backend == "<name>"` blocks

### Version Updates (llama-cpp)

1. Find the new tag and commit hash at `https://github.com/ggml-org/llama.cpp/releases`
2. Update `context.version` in all three `pixi-recipes/llama-cpp-source/{cpu,cuda,vulkan}/recipe.yaml`
3. Update `source.rev` to the commit hash for the new tag in all three recipes
4. Update `context.version` in all three `pixi-recipes/llama-cpp-binary/{cpu,vulkan,rocm}/recipe.yaml`
5. Run `pixi lock` to regenerate the lockfile
6. Test all backends

## File Reference

| File | Purpose |
|------|---------|
| `pixi.toml` | Root workspace: features, tasks, environments |
| `pixi.lock` | Locked dependency versions (binary; never edit) |
| `models.ini` | llama-server multi-model preset config |
| `llama-server.log` | Server log (gitignored) |
| `scripts/bwrap-claude.sh` | Bubblewrap sandbox wrapper for Claude Code |
| `scripts/claude-unsafe.sh` | Unsandboxed Claude Code wrapper (dev/debug only) |
| `scripts/bwrap-pi.sh` | Bubblewrap sandbox wrapper for pi agent |
| `scripts/diff-llama-cpp-variants.sh` | Compare llama-cpp recipe variants |
| `scripts/inject-pi-extensions.sh` | Merge pi-extensions packages into settings.json |
| `scripts/install-apparmor.sh` | Install/load AppArmor profile for bwrap (local sudo or CI) |
| `scripts/stop-server.sh` | Graceful llama-server shutdown (SIGTERM → SIGKILL) |
| `scripts/start-server.sh` | Background llama-server with logging |
| `scripts/pi-unsafe.sh` | Unsandboxed pi wrapper (dev/debug only) |
| `sample-data/wiki.test.raw` | Wikitext-2 corpus for `llama-perplexity` |
| `sample-data/describe-me.jpg` | Image for multimodal testing |
| `sample-data/README.md` | Sample data documentation |
| `sample-data/context-bench/run_benchmark.py` | Long-context recall benchmark runner (prompts models, grades, writes TOML) |
| `sample-data/context-bench/AGENTS.md` | System prompt for the model under test |
| `sample-data/context-bench/config.toml` | Benchmark runner config |
| `sample-data/context-bench/<size>.txt` | Benchmark books (16k–256k) with 20 questions appended |
| `sample-data/context-bench/<size>.answers.txt` | Reference answers with source line numbers |
| `pixi-recipes/llama-cpp-source/build.sh` | Shared CMake build + install + symlink script |
| `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` | CPU build recipe |
| `pixi-recipes/llama-cpp-source/cuda/recipe.yaml` | CUDA build recipe |
| `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml` | Vulkan build recipe |
| `pixi-recipes/llama-cpp-binary/build.sh` | Linux: copy pre-built binaries + create symlinks |
| `pixi-recipes/llama-cpp-binary/build.bat` | Windows: copy pre-built exes + DLLs into `bin` |
| `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml` | CPU binary recipe |
| `pixi-recipes/llama-cpp-binary/vulkan/recipe.yaml` | Vulkan binary recipe |
| `pixi-recipes/llama-cpp-binary/rocm/recipe.yaml` | ROCm binary recipe |
| `pixi-recipes/claude/recipe.yaml` | Claude Code conda package recipe |
| `pixi-recipes/claude/build.sh` | Linux: `npm install --global` into prefix |
| `pixi-recipes/claude/build.bat` | Windows: `npm install --global` into prefix |
| `pixi-recipes/pi-extensions/recipe.yaml` | Packages pi plugin set |
| `pixi-recipes/pi-extensions/build.sh` | Linux: runs `pi install` for each plugin in `PLUGINS` |
| `pixi-recipes/pi-extensions/build.bat` | Windows: runs `pi install` for each plugin in `PLUGINS` |

## Conventions

- All llama-cpp backends share the same `build.sh` — differences are controlled by the `BACKEND` env var and conditional recipe dependencies
- `inject-pi-extensions.sh` merges the pi-extensions `packages` block from `$CONDA_PREFIX/home/.pi/agent/settings.json` into `~/.pi/agent/settings.json` using `node -e` (not `jq`, for Windows compatibility)
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
- **`pi-unsafe.sh` calls `inject-pi-extensions.sh`** to merge pi-extensions packages, symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` (copies on Windows), and always cleans up on exit via `trap`
- **AppArmor is required for bwrap** — run `pixi run install-apparmor` to install and load the profile before running the sandboxed pi agent (works locally with sudo and unattended on GitHub Actions; no-op where unprivileged user namespaces are unrestricted)
- **`pi-extensions` pins plugin versions explicitly** — bump versions in the `PLUGINS` list in `recipe.yaml` (shared by `build.sh` and `build.bat`) and update the `recipe.yaml` package version when adding or upgrading plugins
- **`claude` recipe packages Claude Code from npm** — update `context.version` and `source.sha256` in `pixi-recipes/claude/recipe.yaml` when bumping the version; use the `stable` dist-tag from the npm registry
- **Windows scripts run under MSYS2 bash shipped by the environment** — the default feature pins `m2-bash`, `m2-coreutils`, and `m2-grep` on win-64, because a plain `bash` from PATH on vanilla Windows resolves to WSL, which discards the pixi environment. Don't use `jq` (not packaged for win-64 on conda-forge), `nc`, `pkill`/`pgrep`, or `ln -s` in scripts that must run on Windows; use `node -e` for JSON, `curl` for port checks (Windows ships it in System32), `taskkill` behind an `$OSTYPE == msys*` branch, and `cp -r` (files) or an NTFS junction via `cmd //c 'mklink /J <link> <target>'` (directories; needs no admin rights) instead of symlinks. If a script needs another external command on Windows, add the corresponding `m2-*` package
- **`CLAUDE.md` is a symlink** to `AGENTS.md` for Claude Code compatibility
- **`.claude/skills` is a symlink** to `.agents/skills/` for Claude Code compatibility
