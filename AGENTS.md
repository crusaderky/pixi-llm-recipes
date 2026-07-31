# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that serves multiple purposes:

1. **Builds and packages llama.cpp** as a conda/pixi package using **pixi-build** (rattler-build backend), compiling from source for multiple hardware backends (CPU, CUDA, Vulkan, ROCm), or using pre-built binaries from upstream releases (CPU, CUDA, Vulkan, ROCm). The default fork is [beellama.cpp](https://github.com/Anbeeld/beellama.cpp) (KVarN / KV-cache-precision fork); mainline `ggml-org/llama.cpp` is retained as a commented-out recipe variant.
2. **Packages pi-extensions** — a curated set of pi coding agent plugins.
3. **Packages herdr** — an agent-first terminal multiplexer and coding-agent orchestrator.
4. **Runs the pi coding agent** in a bubblewrap sandboxed environment with local LLM inference.
5. **Benchmarks LLM inference** via `llama-benchy`.
6. **Benchmarks long-context recall** (and how it degrades under quantized KV cache) via the `context-bench` harness in `sample-data/context-bench/`.
7. **Analyzes KV cache quantization quality** via `kv-perplexity` (KL-divergence sweep over K/V quant combos) and `kv-kld-report` (HTML/Markdown report with plots) in `scripts/`.
8. **Inspects GGUF model metadata** — estimates on-disk weight size and KV-cache VRAM without downloading the weights — via `gguf-meta-extract` in `scripts/`.

## Key Technologies

- **pixi** — Cross-platform dependency/environment manager (conda-compatible)
- **pixi-build / rattler-build** — Conda recipe building system
- **llama.cpp** — Open-source LLM inference engine by ggml-org (MIT license); default fork: [beellama.cpp](https://github.com/Anbeeld/beellama.cpp)
- **forge-guardrails** — [Tool-calling reliability proxy](https://github.com/antoinezambelli/forge) (PyPI package) that sits in front of llama-server: validates tool calls, rescue-parses malformed ones, retries with corrective feedback
- **bubblewrap (bwrap)** — Containerized sandbox for running the pi agent securely
- **pi-coding-agent** — The pi coding agent framework (installed via npm)
- **pi-extensions** — Curated pi plugins installed via a conda package
- **claude** — Claude Code CLI (`@anthropic-ai/claude-code`) installed via a conda package (fetches from npm)
- **herdr** — Agent-first terminal multiplexer and coding-agent orchestrator (packaged from pre-built GitHub releases)

## Project Structure

```
pixi-llm-recipes/
├── pixi.toml                         # Root workspace: features, environments, tasks
├── pixi.lock                         # Lockfile for reproducible builds
├── .gitignore                        # Excludes .pixi/ (envs) but keeps .pixi/config.toml
├── .gitattributes                    # Marks pixi.lock as binary
├── .github/workflows/                # GitHub Actions workflows
├── README.md                         # Project readme
├── chat-templates/                   # Jinja chat templates for llama-server
│   └── qwen3.6-froggeric-v20.jinja   # Custom Qwen 3.6 chat template
├── kv-perplexity.yaml                # Config for kv-perplexity.py
├── models.ini                        # llama-server preset config (multi-model)
├── scripts/
│   ├── bwrap-claude.sh               # Bubblewrap sandbox wrapper for Claude Code
│   ├── bwrap-pi.sh                   # Bubblewrap sandbox wrapper for pi agent
│   ├── gguf-meta-extract.py          # Header-only GGUF tensor/VRAM inspector (no weight download)
│   ├── inject-pi-extensions.sh       # Merge pi-extensions packages into settings.json
│   ├── inject-claude-extensions.sh    # Deploy packaged Claude Code extensions into ~/.claude
│   ├── install-apparmor.sh           # Install AppArmor profile for bwrap (sudo/CI)
│   ├── install-memlock.sh            # Raise locked-memory ulimit for llama-server mlock (sudo/CI)
│   ├── kv_cache_common.py            # Shared bpw table + KV-cache geometry model (importable)
│   ├── kv-kld-report.py                 # Parse perplexity log → HTML/Markdown KLD report
│   ├── kv-perplexity.py              # KLD sweep over cartesian product of K/V quant combos
│   ├── llama-cpp-changelog.py        # Deterministic llama.cpp changelog dumper (tags + PRs + commits)
│   ├── start-forge-server.sh         # Background forge-proxy (guardrails) in front of llama-server
│   ├── start-server.sh               # Background llama-server with logging
│   ├── stop-forge-server.sh          # Graceful forge-proxy shutdown
│   ├── stop-server.sh                # Graceful llama-server shutdown
│   ├── herdr                         # Naked `herdr` wrapper (installed to ~/.local/bin by `pixi r install`)
│   └── pi-unsafe.sh                  # Unsandboxed pi wrapper (full host access)
├── .agents/skills/                   # Agent skills (discovered by pi agent)
│   ├── llama-cpp-changelog/SKILL.md  # Summarize llama.cpp changelog
│   ├── test-git-auth/SKILL.md        # Verify git push / gh CLI auth
│   ├── update-all/SKILL.md           # Update everything
│   ├── update-claude/SKILL.md        # Update Claude Code recipe
│   ├── update-llama-cpp/SKILL.md     # Update llama.cpp recipes
│   ├── update-pi-extensions/SKILL.md # Update pi-extensions versions
│   └── update-herdr/SKILL.md         # Update herdr recipe
├── perplexity/                       # Historical KLD sweep outputs
├── sample-data/
│   ├── wiki.test.raw                 # Wikitext-2 test corpus for perplexity/KLD
│   ├── wiki.train.head-10k.raw       # First 10k lines of wiki.train.raw (~674k tokens)
│   ├── describe-me.jpg               # Arbitrary image for multimodal testing
│   ├── context-bench/                # Long-context recall benchmark
│   │   ├── AGENTS.md                 # System prompt given to the model under test
│   │   ├── README.md                 # Benchmark documentation
│   │   ├── run_benchmark.py          # Prompts each model, grades answers, writes a TOML report
│   │   ├── aggregate_benchmark_results.py  # Aggregates multiple benchmark runs
│   │   ├── config.toml               # Runner config (one table per model)
│   │   ├── 16k.txt … 256k.txt        # Books sized to fill 16k/32k/64k/128k/256k contexts (20 questions appended)
│   │   └── 16k.answers.txt …         # Reference answers (A1–A20) with source line numbers
│   └── README.md                     # Sample data documentation
└── pixi-recipes/
    ├── llama-cpp-source/
    │   ├── recipe.yaml              # Source build recipe — all backends via `flags`
    │   ├── variants.yaml            # Backend `backend` matrix (cpu/cuda/vulkan/rocm)
    │   ├── build.sh                 # Shared CMake build + install + symlink script
    │   └── patches/                 # Source patches applied by `source.patches`
    ├── llama-cpp-binary/
    │   ├── recipe.yaml              # Binary build recipe — all backends via `flags`
    │   ├── variants.yaml            # Backend `backend` matrix (cpu/cuda/vulkan/rocm)
    │   ├── build.sh                 # Linux: copy files + create symlinks
    │   └── build.bat                # Windows: copy exes + DLLs into bin
    ├── claude/
    │   ├── recipe.yaml               # Claude Code conda package (fetches from npm)
    │   ├── build.sh                  # Linux: npm install --global into prefix
    │   └── build.bat                 # Windows: npm install --global into prefix
    ├── claude-extensions/
    │   ├── recipe.yaml               # Claude Code extensions (rtk integration)
    │   ├── build.sh                  # Linux: runs rtk init for Claude
    │   └── build.bat                 # Windows: same
    ├── pi-extensions/
    │   ├── recipe.yaml               # Packages curated pi plugins (pins in PLUGINS env var)
    │   ├── build.sh                  # Linux: runs `pi install` for each plugin
    │   └── build.bat                 # Windows: runs `pi install` for each plugin
    ├── pi-home/
        │   ├── recipe.yaml           # Bundles pi skill directories (copied into prefix)
        │   ├── build.sh              # Linux: flat copy skills/ into $PREFIX/home/.pi/agent/skills
        │   ├── build.bat             # Windows: same
        │   ├── AGENTS.md             # Global agent instructions for all workspaces
        │   └── skills/               # Skill directories
        │       └── use-gh-cli/SKILL.md # Skill: use gh CLI instead of web fetch for GitHub
    └── herdr/
            ├── recipe.yaml           # herdr conda recipe (downloads pre-built binary from GitHub releases)
            ├── build.sh              # Linux: download herdr binary to $PREFIX/bin
            └── build.bat             # Windows: download herdr.exe to %PREFIX%\bin
    └── herdr-file-viewer/
            ├── recipe.yaml           # herdr-file-viewer plugin recipe (prebuilt binary + manifest/scripts)
            ├── build.sh              # Linux: lay down plugin root under $PREFIX/home/.config/herdr/plugins/herdr-file-viewer
            └── build.bat             # Windows: same (.exe)
    └── claude-home/
            ├── recipe.yaml           # Claude Code skill directories (copied into prefix)
            ├── build.sh              # Linux: flat copy skills/ into $PREFIX/home/.claude/skills
            ├── build.bat             # Windows: same
            └── skills/               # Skill directories
                └── .keep             # Empty
```

## Core Concepts

### Variants and Backends (llama-cpp)

#### Source Builds

Build variants are organized in `pixi-recipes/llama-cpp-source/` as a single recipe whose backends (cpu, cuda, vulkan, rocm) are selected via the `backend` matrix in `variants.yaml` and exposed as build `flags`:

```
llama-cpp-source/
├── recipe.yaml      # Source build recipe — all backends (cpu/cuda/vulkan/rocm) via `flags`
├── variants.yaml    # Backend `backend` matrix
└── build.sh         # Shared CMake build script (reads BACKEND env var)
```

#### Binary Builds

Pre-built binaries from upstream GitHub releases (no build deps needed):

```
llama-cpp-binary/
├── recipe.yaml      # Binary build recipe — all backends (cpu/cuda/vulkan/rocm) via `flags`
├── variants.yaml    # Backend `backend` matrix
├── build.sh         # Linux: copy files + create symlinks
└── build.bat        # Windows: copy exes + DLLs into bin
```

The recipes reference the build script as an extension-less `file: build`:
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

| Backend  | CMake flag         | Extra build deps                          |
| -------- | ------------------ | ----------------------------------------- |
| `cpu`    | (none)             | —                                         |
| `cuda`   | `-DGGML_CUDA=ON`   | `cuda-nvcc`, `cuda-version =13.1` (conda) |
| `vulkan` | `-DGGML_VULKAN=ON` | `shaderc` (conda)                         |
| `rocm`   | `-DGGML_HIP=ON`    | **system** ROCm (see below)               |

**ROCm is the one backend not fully served by conda.** conda-forge does not ship
hipBLAS/rocBLAS (and its HIP toolchain packages are version-fragmented), which are
hard `find_package` requirements for llama.cpp's HIP backend. So the `rocm` source
build links against the **system** ROCm install on the build host instead:

- Ubuntu 26.04 ships a complete ROCm 7.1 in the standard archive (`hipcc`,
  `clang-21`, `libamdhip64-dev`, `librocblas-dev`, `libhipblas-dev`,
  `rocm-device-libs-21`) — no third-party AMD apt repo is needed. `build.sh`
  discovers `HIP_PATH`/`HIPCXX` via `hipconfig`.
- Device codegen targets are set by the `gpu_targets` context var in
  `recipe.yaml` (default `gfx1150;gfx1151` for this project owner's Strix
  Point iGPU), overridable at build time with
  `LLAMA_GPU_TARGETS=gfx1100 pixi install -e llamacpp-source-rocm`.
- The system ROCm libraries (`librocblas.so`, `libhipblas.so`, `libamdhip64.so`,
  …) are resolved at runtime via the system linker path, so they are exempted
  from rattler-build's overlinking check via `build.dynamic_linking` in the
  recipe. This also means the resulting package requires a matching system ROCm
  at **runtime** — it is not self-contained like the conda CUDA/Vulkan builds.

### The Build Recipe (`pixi-recipes/llama-cpp-source/recipe.yaml`)

All four backends (cpu, cuda, vulkan, rocm) live in a **single** recipe. The active backend is a `backend` (defined in `variants.yaml`) and is exposed to consumers as a build `flag` (the `build.flags` field), so the per-environment pixi.toml selects it with e.g. `llama-cpp = { path = "pixi-recipes/llama-cpp-source", flags = ["cuda"] }`. Backend-specific requirements and the ROCm `dynamic_linking` exemption are gated with `if: backend == "..."` selectors; platforms that cannot build a backend `skip` it.

The recipe has a `context:` block with the active fork pinned and several alternative forks commented out for reference:

| Status                                                                                                                  | Fork                        | Notes                                       |
| ----------------------------------------------------------------------------------------------------------------------- | --------------------------- | ------------------------------------------- |
| **Active**                                                                                                              | `Anbeeld/beellama.cpp`      | KVarN / KV-cache fork; stable `vX.Y.Z` tags |
| Commented (kept in sync by update-llama-cpp)                                                                            | `ggml-org/llama.cpp` (main) | `bNNNN` tags                                |
| The `source:` block uses `${{ fork }}` and `${{ version }}` template variables, so swapping forks is a one-line change. |                             |                                             |

- **Build script**: `build.sh` (shared across variants) runs CMake + Ninja
- **Build string**: `${{ backend }}_${{ build_number }}`
- **Output**: Conda package named `llama-cpp`
- **Backends**: selected via `flags = ["cpu"|"cuda"|"vulkan"|"rocm"]` (see `variants.yaml`)

### The Build Script (`pixi-recipes/llama-cpp-source/build.sh`)

1. Runs CMake with `-DCMAKE_INSTALL_LIBDIR=opt/llama` and `-DCMAKE_INSTALL_BINDIR=opt/llama` — executables and shared libraries land in `${PREFIX}/opt/llama`
2. Sets `RPATH=$ORIGIN` so executables find sibling backend DLLs (e.g. `libggml-cuda.so`) at runtime without `LD_LIBRARY_PATH`
3. Enables dynamic backend loading (`-DGGML_BACKEND_DL=ON`), all CPU dispatch variants (`-DGGML_CPU_ALL_VARIANTS=ON`), RPC (`-DGGML_RPC=ON`), and disables tests/examples
4. Symlinks `llama-*` executables and `rpc-server` into `${PREFIX}/bin` via relative paths (`../opt/llama/...`)
5. Compiles through **ccache** (`CMAKE_{C,CXX,CUDA,HIP}_COMPILER_LAUNCHER`), which is a conda build dependency of the recipe — never a system-wide install

**Important**: Executables and DLLs must coexist in `opt/llama` so that `dlopen` can locate optional backend libraries at runtime.

**ccache**: rattler-build compiles in a fresh `.pixi/bld/llama-cpp/<hash>/` tree with `HOME` pointed at its throwaway work dir, so the cache directory is passed in from `recipe.yaml` (`CCACHE_DIR`, default `~/.cache/ccache`, `CCACHE_MAXSIZE` default `20G`; both overridable via the same-named env vars at solve time). `build.sh` then sets `CCACHE_BASEDIR`/`hash_dir=false`/`compiler_check=content` plus the usual conda sloppiness set so that objects still hit once `${SRC_DIR}`, `${PREFIX}` and `${BUILD_PREFIX}` move to a new build tree. Setting the launchers explicitly also short-circuits ggml's own `GGML_CCACHE` autodetection. The per-build hit rate is printed to the build log after `cmake --build`.

### The Binary Build Script (`pixi-recipes/llama-cpp-binary/build.sh`)

Copies pre-built binaries from upstream releases into `${PREFIX}/opt/llama`, then symlinks `llama-*` executables into `${PREFIX}/bin`. The `FORK`, `VERSION`, and `ASSET_PREFIX` env vars (from `context.fork`/`version`/`asset_prefix`) determine which fork's release to fetch and the asset file prefix (`beellama-<tag>-bin-...` vs mainline `llama-<tag>-bin-...`).

On Windows (`build.bat`): executables and DLLs are all copied into `%PREFIX%\bin`, which is on `PATH` in activated pixi environments.

The binary recipe uses `file: build` (extension-less) so rattler-build resolves to `build.sh` on Linux and `build.bat` on Windows.

### Root `pixi.toml` — Features & Environments

| Feature                  | Dependencies                                                                                                                                                                   | Key Tasks                                                                                                                     |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `llamacpp`               | `llama-cpp` (from `pixi-recipes`)                                                                                                                                              | `llama-help`, `llama-version`, `llama-hello`, `llama-list-devices`, `start-server`, `start-forge-server`, `kv-perplexity`     |
| `llamacpp-source-cpu`    | `llama-cpp` (cpu compiled from sources)                                                                                                                                        | —                                                                                                                             |
| `llamacpp-source-cuda`   | `llama-cpp` (cuda compiled from sources)                                                                                                                                       | —                                                                                                                             |
| `llamacpp-source-vulkan` | `llama-cpp` (vulkan compiled from sources)                                                                                                                                     | —                                                                                                                             |
| `llamacpp-source-rocm`   | `llama-cpp` (rocm/HIP compiled from sources against system ROCm)                                                                                                               | —                                                                                                                             |
| `llamacpp-binary-cpu`    | `llama-cpp` (cpu pre-built binary)                                                                                                                                             | —                                                                                                                             |
| `llamacpp-binary-cuda`   | `llama-cpp` (cuda pre-built binary; conda-forge CUDA runtime only)                                                                                                             | —                                                                                                                             |
| `llamacpp-binary-vulkan` | `llama-cpp` (vulkan pre-built binary; linux-64 + win-64 only — beellama ships no arm64 vulkan asset)                                                                           | —                                                                                                                             |
| `llamacpp-binary-rocm`   | `llama-cpp` (rocm pre-built binary)                                                                                                                                            | —                                                                                                                             |
| `pi`                     | `pi-coding-agent`, `pi-extensions` (from `pixi-recipes/pi-extensions`), `pi-home` (from `pixi-recipes/pi-home`), `bubblewrap` (Linux only)                                     | `pi` (Linux only), `pi-unsafe`, `pi-export`                                                                                   |
| `claude`                 | `claude` (from `pixi-recipes/claude`), `claude-extensions` (from `pixi-recipes/claude-extensions`), `claude-home` (from `pixi-recipes/claude-home`), `bubblewrap` (Linux only) | `claude` (Linux only), `claude-unsafe`                                                                                        |
| `herdr`                  | `herdr` (from `pixi-recipes/herdr`), `herdr-file-viewer` (from `pixi-recipes/herdr-file-viewer`, linux-64 + win-64 only)                                                       | `herdr`                                                                                                                       |
| `git`                    | `git` and `gh` (GitHub CLI from conda-forge)                                                                                                                                   | `git`, `gh`                                                                                                                   |
| `pytools`                | `python =3.14`, `llama-benchy` (PyPI), `huggingface_hub`, `transformers`, `openai`, `tomli-w` etc.                                                                             | `llama-benchy`, `hf`, `context-bench`, `aggregate-context-bench`, `kv-kld-report`, `llama-cpp-changelog`, `gguf-meta-extract` |

| Environment              | Feature(s)                            |
| ------------------------ | ------------------------------------- |
| `llamacpp-source-cpu`    | `llamacpp` + `llamacpp-source-cpu`    |
| `llamacpp-source-cuda`   | `llamacpp` + `llamacpp-source-cuda`   |
| `llamacpp-source-vulkan` | `llamacpp` + `llamacpp-source-vulkan` |
| `llamacpp-source-rocm`   | `llamacpp` + `llamacpp-source-rocm`   |
| `llamacpp-binary-cpu`    | `llamacpp` + `llamacpp-binary-cpu`    |
| `llamacpp-binary-cuda`   | `llamacpp` + `llamacpp-binary-cuda`   |
| `llamacpp-binary-vulkan` | `llamacpp` + `llamacpp-binary-vulkan` |
| `llamacpp-binary-rocm`   | `llamacpp` + `llamacpp-binary-rocm`   |
| `agents`                 | `pi` + `claude` + `git` + `pytools`   |
| `herdr`                  | `herdr`                               |

### `models.ini` — llama-server Preset Configuration

The `models.ini` file uses the native llama-server preset format (`--models-preset`). It defines multiple named model profiles served on demand.
See `README.md` for the disk size, VRAM size, and decode speed.
Global settings include Jinja templating, flash attention, KV cache quantization (`q8_0`), and reasoning budgets.

### `sample-data/context-bench/` — Long-Context Recall Benchmark

Measures how well a model recalls facts scattered through a long context, and how that degrades under pressure such as a quantized KV cache. It consists of:

- **Books** `16k.txt`, `32k.txt`, `64k.txt`, `128k.txt`, `256k.txt` — public-domain Project Gutenberg books, each sized so its text plus questions comfortably fills the named context window. The PG license boilerplate is stripped; 20 questions about strict, unambiguous facts (drawn from paragraphs spread evenly through the book) are appended under a `QUESTIONS` section.
- **Answer keys** `<size>.answers.txt` — reference answers `A1`–`A20`, each with the original source line number(s) in `[brackets]`.
- **`AGENTS.md`** — the system prompt handed to the model under test (no tools): answer `A1`–`A20` from the supplied text only, in the requested format, leaving an answer blank if unknown.
- **`run_benchmark.py`** — the runner (see below).

The books are deliberately obscure, recently-digitised titles so that answers must come from the context, not the model's training data. When bumping/replacing a book, keep it well under its target window (the existing ones fill ~70–90%) and regenerate the matching `.answers.txt`.

#### `run_benchmark.py`

Reads a Pydantic-validated TOML config (one `[model tag]` table each: `url` defaulting to localhost, optional `api_key`/`api_key_env`/`model_name`, required `ctx-size` — a list of book sizes to run, each `65536`/`64k`/`1M` and matching an available book — and optional `temperature`/`max_tokens`/`timeout`). An optional `[*]` table supplies defaults applied to every model (per-model values override it). For every model it sends `AGENTS.md` (system) + each book (user) via the `openai` client, for **each book listed in `ctx-size`**. Answers are graded with normalized string matching (case-insensitive; ignores articles, currency symbols, separators and spacing; maps number-words to digits, e.g. `nine`→`9`). It writes a TOML report keyed `[model tag.context size]` with `raw_answers` (verbatim), `outcomes` (20× `PASS`/`NO ANSWER`/`WRONG`) and `grade` = `(#PASS − #WRONG)/20` in `[-1, 1]`. Grading is deterministic, so a correct-but-off-format answer can score `WRONG`; `raw_answers` is preserved for inspection.

### `scripts/kv_cache_common.py` — Shared KV-Cache Sizing Primitives

Imported by `kv-perplexity.py`, `kv-kld-report.py` and `gguf-meta-extract.py` (whose own file names are hyphenated and therefore not importable; they are run as `python scripts/<name>.py`, so `sys.path[0]` is `scripts/`). It holds:

- **`BPW`** — bits-per-weight per quant name, including block/tile overhead (`q8_0` = 8.5, `kvarn4` = 4.375, …), and **`resolve_bpw(name)`**, which warns and falls back to 32.0 on an unknown name.
- **`ModelKV`** — a model's KV-cache geometry (full-attention and sliding-window layer groups, per-group KV-head counts, window size, key/value head dims, loop count, plus any `compressed` side caches) and the sizing model for beellama v0.4.1's persistent allocation (quant body + per-sequence f16 exact-tail overlay; bodyless exact ring when the tail covers the SWA window). `get_total_kv_cache_size()` returns bytes at a given context / tail / n_parallel; `cache_breakdown()` returns the per-group derivation as `CacheGroupSize` rows (each carrying its own layers / KV heads / key+value dims / note / bytes, so no caller has to reach back into the spec); `elems_per_token` is the quant-independent per-token width; `compressed_rows()` / `compressed_note()` are the equivalents for a side cache; `full_attn_layers_all` / `sliding_window_layers_all` are the loop-expanded layer counts. Only `layers * kv_heads` enters the arithmetic, so a group with per-layer-varying head counts is described by its (possibly fractional) average; `value_dim = 0` expresses an MLA / fused-latent / K-only cache; `n_loops > 1` is a looped / recursive transformer, whose `block_count` blocks each get one cache layer **per pass**.
- **`CompressedKV`** — a cache group that is _not_ one row per token, allocated **in addition** to the token groups (so the same layer is counted in both). `ratio` is tokens per stored row: `4` / `128` for DeepSeek-V4's CSA / HCA compressed caches, `1` for a plain full-context side cache such as the DeepSeek-V3.2 / GLM-DSA lightning-indexer key cache, `0` for a context-independent buffer of `fixed_rows` rows (DSV4's f32 compressor ring state, hence `elem_bpw = 32`). `pad` rounds the row count up (`GGML_PAD(…, 256)` for DSV4); `per_seq = False` marks a single shared allocation rather than one set of rows per sequence.
- **`KVARN_FALLBACK`** — the plain ggml type each `kvarnN` pseudo-type falls back to (`kvarn_fallback_cache_type` in `common/arg.cpp`), which is what a cache that never receives the KVarN params actually stores.
- **`MODEL_KV`** — hand-curated geometries for the models pinned in `models.ini` (read from their GGUF headers via `gguf-meta-extract.py`), and **`resolve_model(ref)`**, which case-insensitively substring-matches a `-hf`/`-m` command-line reference against those names.

**All KV-cache arithmetic belongs in `ModelKV` — never in a caller.** The module exists so that the three scripts cannot disagree, and that only holds if every field of a `ModelKV` is a hparam transcribed **verbatim from the GGUF header** and every derived quantity is a `ModelKV` method or property. Concretely:

- A field must never be pre-multiplied, pre-summed, or otherwise "helpfully" folded before construction — `full_attn_layers` is `block_count`, not `block_count * num_loops`.
- A caller that needs a derived number (total bytes, elems/token, effective layer count, per-group split) calls the method. If the number it wants does not exist yet, **add it to `ModelKV`**; do not compute it locally, not even for a log line, because a display value computed the wrong way is indistinguishable from a sizing bug.
- Whatever `gguf-meta-extract.py` reads out of a header, a curator must be able to type straight into `MODEL_KV` and get the same bytes. Any transformation applied on only one of those two paths is the bug.

This is not hypothetical: `num_loops` was originally applied by expanding the layer count inside `gguf-meta-extract.py`, which left `MODEL_KV` (and therefore the KLD report's "Context (MiB)" column) sizing looped models at half their real cache. The multiplication now lives in `ModelKV.full_attn_layers_all`, so both paths and all three scripts get it for free.

### `scripts/kv-perplexity.py` — KV Cache KL-Divergence Sweep

Runs `llama-perplexity` over the cartesian product of K-quant × V-quant × `kv-tail-tokens` combinations, measuring KL divergence against an f16/f16 baseline. Reads a YAML config (common flags, `k_quants`, `v_quants`, `kv-tail-tokens`, `baseline`, optional `include`/`exclude` lists). Each combo is a `{cache-type-k, cache-type-v, kv-tail-tokens}` object (`kv-tail-tokens` defaults to 0 and is never emitted on the command line, keeping it mainline-llama.cpp compatible; unknown keys are rejected). Asymmetric KVarN combos are dropped automatically (KVarN is symmetric-only). The baseline run creates a logits dump (`--kl-divergence-base`); all other combos load that dump and append `--kl-divergence`. Completed combos are skipped on re-run (idempotent). Output is appended to a log file (default `perplexity.log`).

```bash
# (Duplicate and) edit kv-perplexity.yaml first, then:
pixi run -e llamacpp-source-cuda kv-perplexity -c kv-perplexity.yaml
```

### `scripts/kv-kld-report.py` — KLD Report Generator

Parses a `perplexity.log` produced by `kv-perplexity.py`, extracts per-chunk KL divergence for each `-ctk`/`-ctv` combo, and generates an HTML report (interactive Chart.js plot) and a Markdown report (static SVG via matplotlib). Embeds Chart.js inline (fetched from CDN; falls back to CDN `<script>` tag on failure). The "Context (MiB)" column comes from `kv_cache_common.MODEL_KV`/`ModelKV`, evaluated at `--ctx-size` (default: the run's own context from the log) and `--n-parallel` (default 4).

### `scripts/llama-cpp-changelog.py` — Deterministic llama.cpp Changelog Dumper

Dumps a deterministic markdown changelog between two git refs of any llama.cpp fork. The repo defaults to the active (uncommented) `fork:` in `pixi-recipes/llama-cpp-source/recipe.yaml` (override with `--repo owner/name`, e.g. `--repo ggml-org/llama.cpp` for the retained mainline variant). Defaults `from` from the recipe (`# Last sync with main at <tag>` comment, else the active `version`, else the commented-out `# version:` variant of the selected repo); defaults `to` to the repo's latest stable release tag (pre-releases like `preview-vX.Y.Z` are skipped). Handles both upstream `bNNNN` and beellama `vX.Y.Z` tags; refs overridable via positional or `--from`/`--to` args. The git fallback cache is per-fork (`~/.cache/llama-cpp-changelog/<repo>.git`).

Output sections: header (refs, dates, counts), tags in range with release dates + URLs, PRs merged in range (number, title, URL, body excerpt up to 1200 chars, filtered by merge-commit SHA), and direct commits with no PR (short hash, subject, URL). PRs are fetched via GraphQL and require authenticated `gh` CLI (or `GITHUB_TOKEN`/`GH_TOKEN`); tags/commits work unauthenticated but are rate-limited. Used by the `llama-cpp-changelog` and `update-llama-cpp` skills.

```bash
pixi r llama-cpp-changelog [from] [to]
pixi r llama-cpp-changelog --from b9688 --to b9789
```

```bash
pixi run kv-kld-report perplexity.log -o kv-kld-report.html
```

### `scripts/gguf-meta-extract.py` — GGUF Header Inspector / VRAM Estimator

Points at a Hugging Face GGUF repo directory, a single `.gguf` blob/resolve URL, or a glob pattern, and dumps per-tensor metadata to CSV **without downloading the weights**. It fetches only the header of each `.gguf` via HTTP Range requests (starts at 8 MiB, grows until the tensor table parses, capped at 512 MiB), parsing the GGUF header directly rather than via `gguf.GGUFReader` (which eagerly materialises tensor data and has no header-only mode). The CSV has one row per tensor: `layer, tensor_name, geometry, n_points, quant, bytes_per_point, total_bytes`. Split GGUFs are merged (split 0's hparams win); output defaults to `gguf_tensors.csv` (gitignored); `--token`/`$HF_TOKEN` for private/gated repos.

To stderr it also prints: dense-vs-routed-expert (`*_exps.*`) weight split, plus activated experts per token on MoE models; a KV-cache VRAM estimate at 256k tokens across cache quants, each paired with the `--kv-tail-tokens` exact tail that makes it usable in practice (`f16`, `q8_0`, `kvarn5 t128`, `kvarn4 t1024`, `kvarn3 t2048` — the `KV_QUANTS` list at the top of the script); fixed CUDA + logits overhead; the in-KV f32 lightning-indexer key stream of a MiniMax-M3-style MSA model; and the `--cpu-moe`/`--n-cpu-moe` expert-offload prefill scratch. Recognises both mainline ggml and ik_llama.cpp/DFlash quant type ids.

The KV-cache figure is built by deriving a `kv_cache_common.ModelKV` from the GGUF's own hparams — `n_embd_k_gqa(il) = key_length * head_count_kv(il)` is llama.cpp's own formula, and the hparams are the only source that knows which layers of a hybrid cache anything at all (`head_count_kv == 0` marks the conv / recurrent / linear-attention blocks). MLA models go through the same path: `attention.key_length` is already the cached latent width (`kv_lora_rank + rope`), and llama.cpp allocates **no V cache** for them (`has_v = !is_mla` in `llama-kv-cache.cpp`), so the V side is zeroed — `attention.value_length` is dead weight there and must not be counted. `attention.key_length_mla` / `value_length_mla` are the inner per-head dims used by the attention math, never by the cache; their presence is exactly `llama_hparams::is_mla()`. Per-layer tensor shapes are the fallback for GGUFs whose hparams are missing (MLA checked first there, since a hybrid MLA model also carries `attn_k`/`attn_v` — on its non-caching linear-attention layers). The result is sized with `kv_cache_common.resolve_bpw`. It therefore shares the bpw table and the cache model with the KLD report, but — unlike the report — never consults the hand-curated `MODEL_KV` table. It sizes a single sequence (`KV_N_PARALLEL` at the top of the script); the per-quant exact tail comes from `KV_QUANTS`.

**Looped / recursive transformers** (`{arch}.num_loops`, e.g. `nanbeige`) run their `block_count` physical blocks several times, and **every pass gets its own KV-cache layer** — the arch loader tiles the per-layer hparam arrays and sets `n_layer_all = block_count * num_loops` (`src/models/nanbeige.cpp`). Without it such a model is sized short by exactly the loop factor. `_n_loops()` only _reads_ the key; the expansion itself is `ModelKV.n_loops` (see the shared-module rule above), so the layer groups here are split over the **physical** blocks and the SWA pattern is derived on the physical count — which is also what llama.cpp does, since the generic `set_swa_pattern` runs before the arch loader tiles. Tiling the arrays here would give the same answer (tiling preserves both the per-group ratio and the `_group_kv_heads` average) but would move the arithmetic out of the shared module.

**Sparse-attention side caches.** Four architectures ship `.indexer.` tensors, and each caches the lightning indexer's keys differently — the GGUF says nothing about which, so four small arch tables (all keyed by `general.architecture`, all mirroring llama.cpp) carry it:

- **`_ARCH_INDEXER_KV`** — the indexer keys live in the **main** KV cache, as an extra f32 stream of `n_embd_k_idx(il)` next to K/V (`hparams.indexer_kv = true`). Only `minimax-m3`; reported as its own additive line.
- **`_compressed_groups()`** — everything else puts them in a side cache of its own, at the run's **K quant, not f32**: `deepseek32` / `glm-dsa` get one full-context K-only cache with `head_count_kv` forced to 1 and the head dim replaced by `attention.indexer.key_length` (`llama_kv_cache_dsa`), while `deepseek4` gets a compressed one (below). These become `CompressedKV` groups, i.e. part of the headline KV-cache figure. Charging any of them the f32 full-context stream instead overstates the width ~4x and leaves it out of the headline.
- **`_ARCH_K_ONLY_CACHE`** — architectures whose loader forces K-only storage **without** declaring MLA head dims: `deepseek4` fakes `is_mla()` on its cache hparams (`dsv4_make_k_only`) purely to get `has_v = false`, so its `attention.value_length` is never allocated and must be zeroed exactly as for a real MLA model.
- **`_ARCH_NO_KVARN`** — architectures that branch to a bespoke cache **before** llama-model.cpp's `params.kvarn.type != DISABLED` test and so never receive the KVarN params (`deepseek32`, `glm-dsa`, `deepseek4`). For them `-ctk kvarnN` silently stores the plain `KVARN_FALLBACK` type, which the report substitutes and labels (`kvarn4 unsupported -> q4_0`).

**DeepSeek-V4 (`deepseek4`)** is the one architecture whose token KV cache is _not_ where its context lives. Every layer is sliding-window at a 128-token window (`set_swa_pattern(0)`), and the history sits in three side caches that `llama_kv_cache_dsv4` allocates next to it, each a plain `llama_kv_cache` of `GGML_PAD(ceil(n_ctx / ratio), 256)` K-only cells **per sequence**, tail-less: CSA over the `attention.compress_ratios == 4` layers, HCA over the `== 128` layers, and the lightning-indexer LID over the CSA layers at `attention.indexer.key_length` dims with one KV head. Each also owns a context-independent f32 compressor ring state. Without them the model is sized at ~1% of its real cache — the 128-token window alone.

**The `tN` column does not mean what it means elsewhere on these architectures**, so `_kv_tail_caveats()` prints what the exact tail can actually reach, read off the spec: a side cache is constructed with `tail_tokens = 0` (llama.cpp hands it no tail arguments at all), and `llama-context.cpp` clamps the sliding-window tail to the window itself (`kv_tail_tokens_swa = min(N, n_swa)`). For DeepSeek-V4, whose every layer is sliding-window at 128 tokens, that makes `t128`/`t1024`/`t2048` one and the same allocation, protecting only the ~5 MiB raw window and never the compressed caches. The byte figures were already right — `_swa_group_bytes` treats `tail >= window` as a bodyless exact ring and compressed groups ignore the tail — but the labels overstated the knob.

**`_ARCH_SWA_PATTERN`** mirrors the `hparams.set_swa_pattern(period, dense_first)` call in each architecture's loader in llama.cpp (`src/models/<arch>.cpp`), keyed by `general.architecture`. It is not redundant with the GGUF: `attention.sliding_window_pattern` is optional and, when present, overrides only the _period_ — the dense-first phase lives solely in the arch code, as does the period itself for the many GGUFs that omit the key (every Laguna and Gemma-3 GGUF, for two). Without it such a model is sized as if every layer were full-attention, overstating the cache several-fold. Architectures that store the pattern as a per-layer bool array (`gemma4`, `lfm2`, `mimo2`, …) need no entry. When a GGUF declares a window and neither source supplies a pattern, the script warns and reports an explicit upper bound. Keep the table in sync when llama.cpp gains an SWA architecture.

```bash
pixi r gguf-meta-extract https://huggingface.co/unsloth/GLM-5.2-GGUF/tree/main/UD-IQ1_S -o glm.csv
```

### `scripts/bwrap-claude.sh` — Claude Code Bubblewrap Sandbox

Wraps Claude Code (`claude`) in a bubblewrap container using the current working directory:

- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir at `/tmp/claude` if `-` is passed)
- Binds `~/.claude`, `~/.claude.json` (Claude Code config/auth)
- Calls `inject-claude-extensions.sh` before the sandbox starts to deploy packaged extensions (rtk hook scripts, settings) from `$CONDA_PREFIX/home/.claude/` into the host's `~/.claude/`
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
- Binds caches: `~/.cache/{ccache,pip,pre-commit,rattler,uv}`
- Creates and bind-mounts `~/.pi/agent/sessions`, `auth.json`, `trust.json`, and `settings.json`
- Mounts a fresh `tmpfs` at `~/.pi/agent/intercom` so the pi-intercom broker, its unix socket, and all of its runtime state stay private to the sandbox. A pi session and its pi-subagents children (subprocesses in the same sandbox) can message each other; independent sandboxes and the host cannot be reached — no sandbox escape. Parallel sandboxes don't conflict: each runs its own broker on its own private socket (filesystem path, no TCP ports). Without this mount the extension would write shared runtime state into `$CONDA_PREFIX/home/.pi/agent/intercom` through the rw `~/.pi` bind.
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

### `pixi-recipes/claude-extensions` — Claude Code Plugin Package

Installs the rtk integration for Claude Code, which generates `CLAUDE.md`, `RTK.md`, and patches `settings.json`. The output is deployed to `${PREFIX}/home/.claude/`.

At runtime, `scripts/inject-claude-extensions.sh` merges these packaged files into the host's `~/.claude/` before the sandbox starts (analogous to `scripts/inject-pi-extensions.sh` for pi).

### `pixi-recipes/claude-home` — Packaged ~/.claude

Packages Claude Code skill directories into `${PREFIX}/home/.claude/skills/`. These are deployed to the host's `~/.claude/skills/` by `scripts/inject-claude-extensions.sh` before the sandbox starts (same pattern as `pixi-recipes/pi-home` for pi).

### `pixi-recipes/pi-home` — Packaged ~/.pi

Uses conda-build to package a fixed

- `~/.pi/skills` (currently `use-gh-cli`)
- `~/.pi/AGENTS.md`
- `~/.pi/keybindings.json`
- `~/.pi/agents` (for the `pi-subagents` extension)

These files are deployed to `$CONDA_PREFIX/home/.pi` and are deployed on the fly to
`~/.pi` by `scripts/bwrap-pi.sh` and `scripts/pi-unsafe.sh` (with symlinks on Linux and
`cp` on Linux). When pi exits, any changed performed from inside pi itself are rsync'ed
back to `pixi-recipes/pi-home` so that they can be reviewed and committed to git. Note:
rsync is configured not to change any timestamps when contents don't change, so that
pixi-build won't rebuild the recipe at every launch.

### `pixi-recipes/herdr` — herdr Terminal Multiplexer

Packages [herdr](https://herdr.dev) from pre-built GitHub release binaries.

- **Linux**: `build.sh` downloads the stable release binary from `https://herdr.dev/latest.json` (tag `v{version_stable}`) into `${PREFIX}/bin/herdr`.
- **Windows**: `build.bat` downloads the preview release binary from `https://herdr.dev/preview.json` (tag `preview-{build_id}`) into `%PREFIX%\bin\herdr.exe`. Windows builds are preview-only.

The recipe stores:

- `version_stable` — plain version string without `v` prefix (e.g. `"0.7.1"`) for Linux. The `v` prefix is added by `build.sh` in the download URL.
- `sha256_stable_x86_64` / `sha256_stable_aarch64` — expected sha256 for each Linux arch binary. Verified by `build.sh` after download.
- `version_preview` — full preview release tag (e.g. `preview-2026-06-22-24c7377de01c`) for Windows.
- `sha256_preview_win_64` — expected sha256 for the Windows binary. Verified by `build.bat` after download.

See the **update-herdr** skill for the update procedure.

### `pixi-recipes/herdr-file-viewer` — herdr file-viewer plugin

Packages the [herdr-file-viewer](https://github.com/smarzban/herdr-file-viewer) herdr plugin (a git-aware, read-only file viewer TUI) from the upstream GitHub release into a herdr plugin root under `${PREFIX}/home/.config/herdr/plugins/herdr-file-viewer/`: the manifest (`herdr-plugin.toml`), the launcher scripts, the example config, and the prebuilt viewer binary at `target/release/`. No software is written into `~/` — only the registry file and the user's per-plugin `config.toml` live there.

- **Linux** (`build.sh`): downloads the `x86_64-unknown-linux-musl` prebuilt, verifies its sha256, and fetches the manifest/scripts/example config from the tagged source on `raw.githubusercontent.com`.
- **Windows** (`build.bat`): same for the `x86_64-pc-windows-msvc` `.exe` (sha256 via `certutil`).
- Upstream ships no Linux aarch64 prebuilt, so the dependency is gated to `linux-64` + `win-64` in `pixi.toml`.
- A portable `entry.json` (version, `min_herdr_version`, description — no absolute paths) is written next to the manifest; `scripts/inject-herdr-file-viewer.sh` reads it and merges the plugin entry into `~/.config/herdr/plugins.json` with `manifest_path`/`plugin_root` filled from `$CONDA_PREFIX` and `source = { kind = "local" }`. The inject runs from `scripts/run-herdr.sh` (before the PIXI/CONDA env is stripped), so herdr discovers and loads the plugin on launch.

The recipe context pins `version` and the two sha256 values; bump them (and refresh `entry.json` ships automatically from `$VERSION`) to update. The pane command and launchers are read directly from the shipped manifest, so a new release's manifest is fetched on rebuild.

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

**`-e <env>` is only required when a task name exists in multiple environments.** Tasks that belong to exactly one environment (e.g. `pi`, `claude`, `herdr`, `gh`, `llama-benchy`) can be invoked with plain `pixi run <task>` — pixi selects the environment automatically. The llamacpp tasks (`start-server`, `start-forge-server`, `llama-help`, etc.) exist in all seven `llamacpp-*` environments, so `-e` is mandatory there to pick the right backend.

### Building Packages

```bash
# Build from root (builds all backends as separate flag variants)
pixi build

# Build a specific backend flag
pixi build --path pixi-recipes/llama-cpp-source
```

Each backend is selected via its build `flag` (`cpu`, `cuda`, `vulkan`, `rocm`),
e.g. `pixi install -e llamacpp-source-cuda` pulls `llama-cpp` built with `flags = ["cuda"]`.

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

With the [forge](https://github.com/antoinezambelli/forge) guardrails proxy in front (llama-server moves to port 8081; forge serves clients on port 8080, so consumers of `http://localhost:8080/v1` need no reconfiguration):

```bash
pixi run -e llamacpp-source-cuda start-forge-server    # llama-server on 8081 + forge-proxy on 8080 (logs to forge-proxy.log)
pixi run -e llamacpp-source-cuda stop-forge-server     # Stop both forge-proxy and llama-server
pixi run -e llamacpp-source-cuda restart-forge-server  # Stop + start in one command
```

**Note**: Binary environments (`llamacpp-*-binary`) skip compilation entirely, making them much faster to set up. They provide pre-built binaries from beellama.cpp releases for CPU, CUDA (linux-64 only), Vulkan (linux-64 + win-64), and ROCm (linux-64) backends. The CUDA variants resolve their runtime from conda-forge packages only, via the `linux-64-cuda` virtual platform in `[workspace].platforms` (`{ name = "linux-64-cuda", platform = "linux-64", cuda = "13" }`), so GPU-less hosts and CI can solve and install them.

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

**Naked wrapper:** `pixi r install` deploys `scripts/pi` to `~/.local/bin`. After that you can
run `cd <workspace> && pi <args>` from any directory. The wrapper resolves `--bind` relative
paths against your cwd before forwarding (the sandbox runs with the repo as its cwd). Pass
`--no-sandbox` to route to the `pi-unsafe` task instead (full host access).

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

**Naked wrapper:** `pixi r install` deploys `scripts/claude` to `~/.local/bin`. After that you can
run `cd <workspace> && claude <args>` from any directory. The wrapper resolves `--bind` relative
paths against your cwd before forwarding (the sandbox runs with the repo as its cwd). Pass
`--no-sandbox` to route to the `claude-unsafe` task instead (full host access).

### Running herdr

```bash
# Launch herdr (no sandbox needed — it's a terminal multiplexer)
pixi run herdr
```

**Naked wrapper:** `pixi r install` deploys `scripts/herdr` to `~/.local/bin`. After that you can
run `herdr` from any directory.

The `herdr-file-viewer` plugin (git-aware read-only file viewer in a herdr pane) is registered
automatically on launch by `scripts/inject-herdr-file-viewer.sh` (run from `run-herdr.sh`); its
binary and scripts live in `$CONDA_PREFIX`, only the registry entry and your per-plugin
`config.toml` live in `~/.config/herdr`. To summon it, bind a key in `~/.config/herdr/config.toml`
(e.g. `prefix+f` → `herdr plugin action invoke open-file-viewer --plugin herdr-file-viewer`) and
run `herdr server reload-config`. Optional renderers: `glow` / `delta` / `bat`.

### Running Benchmarks

```bash
# KL-divergence sweep over K/V quant combos (edit kv-perplexity.yaml first)
pixi run -e llamacpp-source-cuda kv-perplexity -c kv-perplexity.yaml

# Generate HTML/Markdown KLD report from the sweep log
pixi run kv-kld-report perplexity.log -o kv-kld-report.html

# Throughput benchmark
pixi run llama-benchy

# Long-context recall benchmark (edit the config first; one table per model endpoint)
pixi run context-bench sample-data/context-bench/config.toml -o results.toml
```

### Adding a New Backend (llama-cpp)

1. Add the new backend name to the `backend:` list in `pixi-recipes/llama-cpp-source/variants.yaml` (and `pixi-recipes/llama-cpp-binary/variants.yaml` if it also ships pre-built binaries).
2. The shared `build.sh` reads `BACKEND` env var — add a `case` branch with the relevant `-DGGML_*=ON` flag.
3. Add conditional dependencies in `recipe.yaml` for `if: backend == "<name>"` blocks, plus a `skip` entry if the backend cannot build on some platforms.
4. Add a `feature.llamacpp-source-<name>` + environment in `pixi.toml` that selects it with `llama-cpp = { path = "pixi-recipes/llama-cpp-source", flags = ["<name>"] }`, run `pixi lock`, then `pixi install -e llamacpp-source-<name>` to build and test.

### Version Updates (llama-cpp)

Both source and binary builds track **beellama.cpp** as the active fork; mainline `ggml-org/llama.cpp` is retained as a commented-out variant that must be kept current.

1. **Active fork (beellama)**: Check the latest stable tag (`vX.Y.Z`; ignore `preview-*`) on `Anbeeld/beellama.cpp` releases. Update the uncommented `version:` in both `pixi-recipes/llama-cpp-source/recipe.yaml` and `pixi-recipes/llama-cpp-binary/recipe.yaml`.
2. **Commented mainline variant**: Check the latest `bNNNN` tag on `ggml-org/llama.cpp` releases. Update the commented `# version:` line under `# fork: ggml-org/llama.cpp` in both recipes.
3. Run `pixi lock` to regenerate the lockfile.
4. Test all backends.

See the **update-llama-cpp** skill for the detailed step-by-step procedure.

### Version Updates (herdr)

herdr uses one context variable per release channel:

- `version_stable` — plain version string for Linux stable builds (e.g. `"0.7.1"`). Source: `https://herdr.dev/latest.json`. The `v` prefix is added by `build.sh` in the download URL.
- `version_preview` — full preview release tag for Windows (e.g. `preview-2026-06-22-24c7377de01c`). Source: `https://herdr.dev/preview.json`. Windows builds are preview-only.

See the **update-herdr** skill for the detailed step-by-step procedure.

## File Reference

| File                                              | Purpose                                                                                                                                            |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENTS.md`                                       | Project guide for coding agents                                                                                                                    |
| `README.md`                                       | Project readme                                                                                                                                     |
| `.github/workflows/llamacpp.yml`                  | CI workflow for llama-cpp builds                                                                                                                   |
| `chat-templates/qwen3.6-froggeric-v20.jinja`      | Custom Qwen 3.6 chat template (Jinja)                                                                                                              |
| `pixi.toml`                                       | Root workspace: features, tasks, environments                                                                                                      |
| `pixi.lock`                                       | Locked dependency versions (binary; never edit)                                                                                                    |
| `kv-perplexity.yaml`                              | Sample config for `kv-perplexity.py`                                                                                                               |
| `models.ini`                                      | llama-server multi-model preset config                                                                                                             |
| `llama-server.log`                                | Server log (gitignored)                                                                                                                            |
| `forge-proxy.log`                                 | forge-proxy log (gitignored)                                                                                                                       |
| `scripts/bwrap-claude.sh`                         | Bubblewrap sandbox wrapper for Claude Code                                                                                                         |
| `scripts/claude-unsafe.sh`                        | Unsandboxed Claude Code wrapper (dev/debug only)                                                                                                   |
| `scripts/bwrap-pi.sh`                             | Bubblewrap sandbox wrapper for pi agent                                                                                                            |
| `scripts/claude`                                  | Naked `claude` wrapper (installed to ~/.local/bin by `pixi r install`); resolves --bind relative paths against cwd                                 |
| `scripts/inject-pi-extensions.sh`                 | Merge pi-extensions packages into settings.json                                                                                                    |
| `scripts/inject-claude-extensions.sh`             | Deploy packaged Claude Code extensions (hooks, settings) into host's ~/.claude                                                                     |
| `scripts/inject-herdr-file-viewer.sh`             | Register the conda-packaged herdr-file-viewer plugin in `~/.config/herdr/plugins.json` (run from `run-herdr.sh`)                                   |
| `scripts/install-apparmor.sh`                     | Install/load AppArmor profile for bwrap (local sudo or CI)                                                                                         |
| `scripts/install-clipboard.sh`                    | Install wl-clipboard (apt) so herdr copy-on-select can write the system clipboard; sudo only if wl-copy missing                                    |
| `scripts/install-memlock.sh`                      | Raise the locked-memory ulimit (`/etc/security/limits.d/99-memlock.conf`) so llama-server `--mlock` can lock multi-GiB weights; sudo, idempotent   |
| `scripts/install.sh`                              | Backs the `install` task; symlinks `scripts/pi`, `scripts/claude`, and `scripts/herdr` into ~/.local/bin                                           |
| `scripts/stop-server.sh`                          | Graceful llama-server shutdown (SIGTERM → SIGKILL)                                                                                                 |
| `scripts/start-server.sh`                         | Background llama-server with logging (`--port` to override the default 8080; other args forwarded to llama-server)                                 |
| `scripts/stop-forge-server.sh`                    | Graceful forge-proxy shutdown (SIGTERM → SIGKILL)                                                                                                  |
| `scripts/start-forge-server.sh`                   | Background forge-proxy on port 8080 forwarding to llama-server on port 8081                                                                        |
| `scripts/pi`                                      | Naked `pi` wrapper (installed to ~/.local/bin by `pixi r install`); resolves --bind relative paths against cwd                                     |
| `scripts/pi-unsafe.sh`                            | Unsandboxed pi wrapper (dev/debug only)                                                                                                            |
| `scripts/kv_cache_common.py`                      | Shared bpw table (`BPW`/`resolve_bpw`) + KV-cache geometry model (`ModelKV`, `MODEL_KV`, `resolve_model`)                                          |
| `scripts/kv-perplexity.py`                        | KLD sweep over cartesian product of K/V quant combos (`kv-perplexity` task)                                                                        |
| `scripts/kv-kld-report.py`                        | Parse perplexity log → HTML/Markdown KLD report with plots (`kv-kld-report` task)                                                                  |
| `scripts/llama-cpp-changelog.py`                  | Deterministic llama.cpp changelog dumper: tags + PRs (title/desc/URL) + commits (`llama-cpp-changelog` task)                                       |
| `scripts/gguf-meta-extract.py`                    | Header-only GGUF tensor/VRAM inspector: per-tensor CSV + dense/expert & KV-cache VRAM summary, no weight download (`gguf-meta-extract` task)       |
| `sample-data/wiki.test.raw`                       | Wikitext-2 test corpus for KLD/perplexity benchmarks                                                                                               |
| `sample-data/wiki.train.head-10k.raw`             | First 10k lines of wiki.train.raw (~674k tokens; larger KLD baseline)                                                                              |
| `sample-data/describe-me.jpg`                     | Image for multimodal testing                                                                                                                       |
| `sample-data/README.md`                           | Sample data documentation                                                                                                                          |
| `sample-data/context-bench/run_benchmark.py`      | Long-context recall benchmark runner (prompts models, grades, writes TOML)                                                                         |
| `sample-data/context-bench/AGENTS.md`             | System prompt for the model under test                                                                                                             |
| `sample-data/context-bench/config.toml`           | Benchmark runner config                                                                                                                            |
| `sample-data/context-bench/<size>.txt`            | Benchmark books (16k–256k) with 20 questions appended                                                                                              |
| `sample-data/context-bench/<size>.answers.txt`    | Reference answers with source line numbers                                                                                                         |
| `pixi-recipes/llama-cpp-source/build.sh`          | Shared CMake build + install + symlink script                                                                                                      |
| `pixi-recipes/llama-cpp-source/recipe.yaml`       | Source build recipe — all backends (cpu/cuda/vulkan/rocm) via `flags`                                                                              |
| `pixi-recipes/llama-cpp-source/variants.yaml`     | Backend `backend` matrix for the source recipe                                                                                                     |
| `pixi-recipes/llama-cpp-source/build.sh`          | Shared CMake build + install + symlink script (reads `BACKEND`/`GPU_TARGETS` env)                                                                  |
| `pixi-recipes/llama-cpp-source/patches/*.patch`   | Source patches applied by `source.patches`; currently guards the glibc-2.29 `posix_spawn_file_actions_addchdir_np` call in vendored `subprocess.h` |
| `pixi-recipes/llama-cpp-binary/build.sh`          | Linux: copy pre-built binaries + create symlinks                                                                                                   |
| `pixi-recipes/llama-cpp-binary/build.bat`         | Windows: copy pre-built exes + DLLs into `bin`                                                                                                     |
| `pixi-recipes/llama-cpp-binary/recipe.yaml`       | Binary build recipe — all backends (cpu/cuda/vulkan/rocm) via `flags`                                                                              |
| `pixi-recipes/llama-cpp-binary/variants.yaml`     | Backend `backend` matrix for the binary recipe                                                                                                     |
| `pixi-recipes/claude/recipe.yaml`                 | Claude Code conda package recipe                                                                                                                   |
| `pixi-recipes/claude/build.sh`                    | Linux: `npm install --global` into prefix                                                                                                          |
| `pixi-recipes/claude/build.bat`                   | Windows: `npm install --global` into prefix                                                                                                        |
| `pixi-recipes/claude-extensions/recipe.yaml`      | Claude Code extensions conda recipe (rtk integration)                                                                                              |
| `pixi-recipes/claude-extensions/build.sh`         | Linux: runs `rtk init -g --auto-patch` for Claude Code                                                                                             |
| `pixi-recipes/claude-extensions/build.bat`        | Windows: runs `rtk init -g --auto-patch` for Claude Code                                                                                           |
| `pixi-recipes/claude-home/recipe.yaml`            | Packages Claude Code skill directories                                                                                                             |
| `pixi-recipes/claude-home/build.sh`               | Linux: copies skills/ into $PREFIX/home/.claude/skills                                                                                             |
| `pixi-recipes/claude-home/build.bat`              | Windows: same                                                                                                                                      |
| `pixi-recipes/claude-home/skills/.keep`           | Preserves empty directory after removing herdr-pi integration skill                                                                                |
| `pixi-recipes/pi-extensions/recipe.yaml`          | Packages pi plugin set                                                                                                                             |
| `pixi-recipes/pi-extensions/build.sh`             | Linux: runs `pi install` for each plugin in `PLUGINS`                                                                                              |
| `pixi-recipes/pi-extensions/build.bat`            | Windows: runs `pi install` for each plugin in `PLUGINS`                                                                                            |
| `pixi-recipes/pi-home/recipe.yaml`                | Packages pi skill directories                                                                                                                      |
| `pixi-recipes/pi-home/build.sh`                   | Linux: copies skills/ into $PREFIX/home/.pi/agent/skills                                                                                           |
| `pixi-recipes/pi-home/build.bat`                  | Windows: same                                                                                                                                      |
| `pixi-recipes/pi-home/skills/use-gh-cli/SKILL.md` | Skill: use gh CLI instead of web fetch for GitHub                                                                                                  |

| `pixi-recipes/pi-home/AGENTS.md` | Global agent instructions for all workspaces |
| `.agents/skills/*/SKILL.md` | Agent skills discovered by pi agent (llama-cpp-changelog, test-git-auth, update-*) |
| `.agents/skills/update-herdr/SKILL.md` | Skill: update herdr recipe to latest stable+preview releases |
| `pixi-recipes/herdr/recipe.yaml` | herdr conda recipe (downloads pre-built binary from GitHub releases) |
| `pixi-recipes/herdr/build.sh` | Linux: download herdr binary to `$PREFIX/bin` |
| `pixi-recipes/herdr/build.bat` | Windows: download herdr.exe to `%PREFIX%\bin` |
| `pixi-recipes/herdr-file-viewer/recipe.yaml` | herdr-file-viewer plugin conda recipe (prebuilt binary + manifest/scripts from the tagged source release) |
| `pixi-recipes/herdr-file-viewer/build.sh` | Linux: lay down the plugin root (binary + manifest + scripts + `entry.json`) under `$PREFIX/home/.config/herdr/plugins/herdr-file-viewer` |
| `pixi-recipes/herdr-file-viewer/build.bat` | Windows: same plugin root layout (`.exe`) |
| `scripts/herdr` | Naked `herdr` wrapper (installed to ~/.local/bin by `pixi r install`) |
| `scripts/run-herdr.sh` | Task-time launcher: reorders PATH so `~/.local/bin` precedes the conda prefix, otherwise `pi`/`claude` spawned inside herdr bypass the sandbox wrappers |
| `sample-data/context-bench/README.md` | Context-bench documentation |
| `sample-data/context-bench/aggregate_benchmark_results.py` | Aggregates multiple context-benchmark runs (`aggregate-context-bench` task) |

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
- **Never hardcode git revisions** — update `context.version` (the git tag) in the single `pixi-recipes/llama-cpp-source/recipe.yaml`. The `source:` block uses template variables (`${{ fork }}`, `${{ version }}`), not a hardcoded commit SHA.
- **`build.sh` uses `${PREFIX}`** — set by rattler-build; never reference it outside build scripts
- **Symlinks use relative paths** (`../opt/llama/...`) — required for correct conda prefix portability
- **All workspaces target `linux-64`, `linux-aarch64`, and `win-64`** — cross-platform support requires additional logic
- **`bwrap-pi.sh` unsets all `PIXI_*`/`CONDA_*`/`INIT_CWD` vars** before calling pi — the agent must not see conda internals
- **`start-server.sh` starts llama-server in background** with logging to `llama-server.log`; use `stop-server` to gracefully kill it. It accepts `--port` (default 8080) and forwards any other argument verbatim to llama-server
- **`stop-server.sh` uses SIGTERM first, then SIGKILL after timeout** — graceful shutdown pattern
- **`start-forge-server` (llamacpp feature) starts llama-server on port 8081 and the forge-proxy on port 8080** — the dependency task `start-server --port 8081` runs first, then `start-forge-server.sh` launches `python -m forge.proxy` logging to `forge-proxy.log`. `stop-forge-server` stops both; `restart-forge-server` chains the two.
- **Models file per environment**: the sandbox looks for `models.$PIXI_ENVIRONMENT_NAME.json`; if absent it falls back to nothing — create it when running a non-default pi environment
- **`pi-unsafe.sh` calls `inject-pi-extensions.sh`** to merge pi-extensions packages, symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` (copies on Windows), and always cleans up on exit via `trap`
- **AppArmor is required for bwrap** — run `pixi run install-apparmor` to install and load the profile before running the sandboxed pi agent (works locally with sudo and unattended on GitHub Actions; no-op where unprivileged user namespaces are unrestricted)
- **`pixi r install` deploys naked `pi`/`claude`/`herdr` wrappers to `~/.local/bin`** — `scripts/install.sh` symlinks `scripts/pi`, `scripts/claude`, and `scripts/herdr` into `~/.local/bin`; re-run after moving the repo so the symlinks stay correct. The wrappers resolve `--bind` relative paths against the caller's cwd (the sandbox task runs with the repo as cwd) and accept `--no-sandbox` to route to the `*-unsafe` task (full host access).
- **`pixi r uninstall` removes the `pi`/`claude`/`herdr` symlinks from `~/.local/bin`.
- **`pi-extensions` pins plugin versions explicitly** — bump versions in the `PLUGINS` list in `recipe.yaml` (shared by `build.sh` and `build.bat`) and update the `recipe.yaml` package version when adding or upgrading plugins
- **`claude` recipe packages Claude Code from npm** — update `context.version` and `source.sha256` in `pixi-recipes/claude/recipe.yaml` when bumping the version; use the `stable` dist-tag from the npm registry
- **Windows scripts run under MSYS2 bash shipped by the environment** — the default feature pins `m2-bash`, `m2-coreutils`, and `m2-grep` on win-64, because a plain `bash` from PATH on vanilla Windows resolves to WSL, which discards the pixi environment. Don't use `jq` (not packaged for win-64 on conda-forge), `nc`, `pkill`/`pgrep`, or `ln -s` in scripts that must run on Windows; use `node -e` for JSON, `curl` for port checks (Windows ships it in System32), `taskkill` behind an `$OSTYPE == msys*` branch, and `cp -r` (files) or an NTFS junction via `cmd //c 'mklink /J <link> <target>'` (directories; needs no admin rights) instead of symlinks. If a script needs another external command on Windows, add the corresponding `m2-*` package
- **`CLAUDE.md` is a symlink** to `AGENTS.md` for Claude Code compatibility
- **`.claude/skills` is a symlink** to `.agents/skills/` for Claude Code compatibility
- **Always run `pixi r lint` after changing files** — fix any resulting issues immediately. This invalidates your memory of file contents; re-read from disk before making further changes.
- **After changing anything under `pixi-recipes/`, test by running `pixi install -e <env>`** for every environment that uses the changed recipe. Resolve errors like "no candidates were found" or "invalid value" before calling the work done. A green lint does not mean the recipe builds.
