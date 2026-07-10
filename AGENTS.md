# AGENTS.md - Project Guide for Coding Agents

## Project Overview

**pixi-llm-recipes** is a [pixi](https://pixi.sh/) project that serves multiple purposes:

1. **Builds and packages llama.cpp** (with opt-in turboquant KV cache optimizations) as a conda/pixi package using **pixi-build** (rattler-build backend), compiling from source for multiple hardware backends (CPU, CUDA, Vulkan), or using pre-built binaries from upstream releases (CPU, Vulkan, ROCm).
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
- **llama.cpp** — Open-source LLM inference engine by ggml-org (MIT license), built from source with opt-in turboquant KV cache optimizations via [TheTom/llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant)
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
│   ├── diff-llama-cpp-variants.sh    # Compare llama-cpp recipe variants
│   ├── gguf-meta-extract.py          # Header-only GGUF tensor/VRAM inspector (no weight download)
│   ├── inject-pi-extensions.sh       # Merge pi-extensions packages into settings.json
│   ├── inject-claude-extensions.sh    # Deploy packaged Claude Code extensions into ~/.claude
│   ├── install-apparmor.sh           # Install AppArmor profile for bwrap (sudo/CI)
│   ├── kv-kld-report.py                 # Parse perplexity log → HTML/Markdown KLD report
│   ├── kv-perplexity.py              # KLD sweep over cartesian product of K/V quant combos
│   ├── llama-cpp-changelog.py        # Deterministic llama.cpp changelog dumper (tags + PRs + commits)
│   ├── start-server.sh               # Background llama-server with logging
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
    ├── claude-extensions/
    │   ├── recipe.yaml               # Claude Code extensions (herdr integration)
    │   ├── build.sh                  # Linux: downloads herdr, runs herdr integration install claude
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
        │       ├── herdr/SKILL.md     # Skill: control herdr from inside it
        │       └── use-gh-cli/SKILL.md # Skill: use gh CLI instead of web fetch for GitHub
    └── herdr/
            ├── recipe.yaml           # herdr conda recipe (downloads pre-built binary from GitHub releases)
            ├── build.sh              # Linux: download herdr binary to $PREFIX/bin
            └── build.bat             # Windows: download herdr.exe to %PREFIX%\bin
    └── claude-home/
            ├── recipe.yaml           # Claude Code skill directories (copied into prefix)
            ├── build.sh              # Linux: flat copy skills/ into $PREFIX/home/.claude/skills
            ├── build.bat             # Windows: same
            └── skills/               # Skill directories
                └── herdr/SKILL.md     # Skill: control herdr from inside it
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

| Backend  | CMake flag         | Extra build deps                  |
| -------- | ------------------ | --------------------------------- |
| `cpu`    | (none)             | —                                 |
| `cuda`   | `-DGGML_CUDA=ON`   | `cuda-nvcc`, `cuda-version =12.6` |
| `vulkan` | `-DGGML_VULKAN=ON` | `shaderc`                         |

### The Build Recipe (`pixi-recipes/llama-cpp-source/*/recipe.yaml`)

Each recipe has a `context:` block with the active fork pinned and several alternative forks commented out for reference:

| Status        | Fork                          | Notes                                 |
| ------------- | ----------------------------- | ------------------------------------- |
| **Active**    | `ggml-org/llama.cpp` (main)   |                                       |
| Commented out | `TheTom/llama-cpp-turboquant` | Lags behind main several weeks/months |

The `source:` block uses `${{ fork }}` and `${{ version }}` template variables, so swapping forks is a one-line change.

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

| Feature                  | Dependencies                                                                                                                                                                   | Key Tasks                                                                                                                     |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `llamacpp`               | `llama-cpp` (from `pixi-recipes`)                                                                                                                                              | `llama-help`, `llama-version`, `llama-hello`, `llama-list-devices`, `start-server`, `kv-perplexity`                           |
| `llamacpp-source-cpu`    | `llama-cpp` (cpu compiled from sources)                                                                                                                                        | —                                                                                                                             |
| `llamacpp-source-cuda`   | `llama-cpp` (cuda compiled from sources)                                                                                                                                       | —                                                                                                                             |
| `llamacpp-source-vulkan` | `llama-cpp` (vulkan compiled from sources)                                                                                                                                     | —                                                                                                                             |
| `llamacpp-binary-cpu`    | `llama-cpp` (cpu pre-built binary)                                                                                                                                             | —                                                                                                                             |
| `llamacpp-binary-vulkan` | `llama-cpp` (vulkan pre-built binary)                                                                                                                                          | —                                                                                                                             |
| `llamacpp-binary-rocm`   | `llama-cpp` (rocm pre-built binary)                                                                                                                                            | —                                                                                                                             |
| `pi`                     | `pi-coding-agent`, `pi-extensions` (from `pixi-recipes/pi-extensions`), `pi-home` (from `pixi-recipes/pi-home`), `bubblewrap` (Linux only)                                     | `pi` (Linux only), `pi-unsafe`, `pi-export`                                                                                   |
| `claude`                 | `claude` (from `pixi-recipes/claude`), `claude-extensions` (from `pixi-recipes/claude-extensions`), `claude-home` (from `pixi-recipes/claude-home`), `bubblewrap` (Linux only) | `claude` (Linux only), `claude-unsafe`                                                                                        |
| `herdr`                  | `herdr` (from `pixi-recipes/herdr`)                                                                                                                                            | `herdr`                                                                                                                       |
| `git`                    | `git` and `gh` (GitHub CLI from conda-forge)                                                                                                                                   | `git`, `gh`                                                                                                                   |
| `pytools`                | `python =3.14`, `llama-benchy` (PyPI), `huggingface_hub`, `transformers`, `openai`, `tomli-w` etc.                                                                             | `llama-benchy`, `hf`, `context-bench`, `aggregate-context-bench`, `kv-kld-report`, `llama-cpp-changelog`, `gguf-meta-extract` |

| Environment              | Feature(s)                            |
| ------------------------ | ------------------------------------- |
| `llamacpp-source-cpu`    | `llamacpp` + `llamacpp-source-cpu`    |
| `llamacpp-source-cuda`   | `llamacpp` + `llamacpp-source-cuda`   |
| `llamacpp-source-vulkan` | `llamacpp` + `llamacpp-source-vulkan` |
| `llamacpp-binary-cpu`    | `llamacpp` + `llamacpp-binary-cpu`    |
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

### `scripts/kv-perplexity.py` — KV Cache KL-Divergence Sweep

Runs `llama-perplexity` over the cartesian product of K-quant × V-quant combinations, measuring KL divergence against an f16/f16 baseline. Reads a YAML config (common flags, `k_quants`, `v_quants`, `baseline`, optional `include`/`exclude` lists). The baseline run creates a logits dump (`--kl-divergence-base`); all other combos load that dump and append `--kl-divergence`. Completed combos are skipped on re-run (idempotent). Output is appended to a log file (default `perplexity.log`).

```bash
# (Duplicate and) edit kv-perplexity.yaml first, then:
pixi run -e llamacpp-source-cuda kv-perplexity -c kv-perplexity.yaml
```

### `scripts/kv-kld-report.py` — KLD Report Generator

Parses a `perplexity.log` produced by `kv-perplexity.py`, extracts per-chunk KL divergence for each `-ctk`/`-ctv` combo, and generates an HTML report (interactive Chart.js plot) and a Markdown report (static SVG via matplotlib). Embeds Chart.js inline (fetched from CDN; falls back to CDN `<script>` tag on failure).

### `scripts/llama-cpp-changelog.py` — Deterministic llama.cpp Changelog Dumper

Dumps a deterministic markdown changelog between two git refs of `ggml-org/llama.cpp`. Defaults `from` from `pixi-recipes/llama-cpp-source/cpu/recipe.yaml` (`# Last sync with main at bNNNN` comment, else active main `version`, else commented-out `# version: bNNNN`); defaults `to` to the latest upstream release tag. Both overridable via positional or `--from`/`--to` args.

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

To stderr it also prints: dense-vs-routed-expert (`*_exps.*`) weight split, plus activated experts per token on MoE models; a KV-cache VRAM estimate at 256k tokens across cache quants (`f16`, `q8_0`, `kvarn4`, `kvarn3`) derived from hparams (GQA, per-layer head counts, SWA, MLA/latent caches, with a tensor-shape fallback); fixed CUDA + logits overhead; the lightning-indexer key cache (DeepSeek-V3.2 DSA / MiniMax MSA sparse attention); and the `--cpu-moe`/`--n-cpu-moe` expert-offload prefill scratch. Recognises both mainline ggml and ik_llama.cpp/DFlash quant type ids.

```bash
pixi r gguf-meta-extract https://huggingface.co/unsloth/GLM-5.2-GGUF/tree/main/UD-IQ1_S -o glm.csv
```

### `scripts/bwrap-claude.sh` — Claude Code Bubblewrap Sandbox

Wraps Claude Code (`claude`) in a bubblewrap container using the current working directory:

- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir at `/tmp/claude` if `-` is passed)
- Binds `~/.claude`, `~/.claude.json` (Claude Code config/auth)
- Calls `inject-claude-extensions.sh` before the sandbox starts to deploy packaged extensions (herdr hook scripts, settings) from `$CONDA_PREFIX/home/.claude/` into the host's `~/.claude/`
- Binds caches: `~/.cache/{ccache,claude,claude-cli-nodejs,pip,pre-commit,rattler,uv}`
- Binds `$CONDA_PREFIX` read-only (claude binary and Node.js runtime); also binds the pixi root read-only for shared packages
- Uses `--unshare-all --share-net --die-with-parent` for isolation; runs `claude --dangerously-skip-permissions`
- Requires AppArmor profile at `/etc/apparmor.d/bwrap` — same profile used by `bwrap-pi.sh`
- Optional `--with-git` flag: binds `~/.ssh`, `~/.gitconfig`, `~/.config/git`, `~/.git-credentials` (read-only) and `~/.config/gh` (read-write) so that `git push` and the `gh` CLI work inside the sandbox. The SSH agent socket (`SSH_AUTH_SOCK`) is accessible automatically when it lives under `/run/` (gnome-keyring/systemd default); if it lives under `/tmp` it is also bound automatically. The conda-forge `gh` from the `agents` environment is on `PATH` inside the sandbox (via `$CONDA_PREFIX/bin`), shadowing any system-installed snap version.
- Optional `--with-herdr` flag: binds `~/.config/herdr` (which holds `herdr.sock`) read-write so the agent can drive the herdr instance it runs inside. **This is a full sandbox escape**: `herdr.sock` is herdr's full-control JSON-RPC socket and herdr runs outside the sandbox, so an agent with socket access can spawn an unsandboxed host-side pane and run arbitrary commands in it. The socket is therefore not bound by default — pass `--with-herdr` only when you would also be comfortable running with `--no-sandbox`.

### `scripts/bwrap-pi.sh` — Bubblewrap Sandbox

Wraps the pi coding agent in a bubblewrap container:

- Read-only root filesystem; `/tmp`, `/home`, `/root` are `tmpfs`
- Binds the target working directory read-write (or a temp dir if `-` is passed)
- Binds `$CONDA_PREFIX` read-only; mounts `$CONDA_PREFIX/home/.pi` as `~/.pi` inside the sandbox
- Binds caches: `~/.cache/{ccache,pip,pre-commit,rattler,uv}`
- Creates and bind-mounts `~/.pi/agent/sessions`, `auth.json`, `trust.json`, and `settings.json`
- Calls `inject-pi-extensions.sh` to merge pi-extensions packages into `settings.json`
- Bind-mounts `$PIXI_ROOT` (typically `~/.pixi`) read-only
- Unsets all `PIXI_*`, `CONDA_*`, and `INIT_CWD` env vars before exec to isolate the pi agent from the host environment
- Uses `--unshare-all --share-net --die-with-parent` for additional isolation
- Models config file: `models.$PIXI_ENVIRONMENT_NAME.json` (per-environment override; create this file next to `models.ini` if needed)
- Requires AppArmor profile at `/etc/apparmor.d/bwrap` — install it with `pixi run install-apparmor` (see `scripts/install-apparmor.sh`)
- Optional `--with-git` flag: binds `~/.ssh`, `~/.gitconfig`, `~/.config/git`, `~/.git-credentials` (read-only) and `~/.config/gh` (read-write) so that `git push` and the `gh` CLI work inside the sandbox. The SSH agent socket (`SSH_AUTH_SOCK`) is accessible automatically when it lives under `/run/` (gnome-keyring/systemd default); if it lives under `/tmp` it is also bound automatically. The conda-forge `gh` from the `agents` environment is on `PATH` inside the sandbox (via `$CONDA_PREFIX/bin`), shadowing any system-installed snap version.
- Optional `--with-herdr` flag: binds `~/.config/herdr` (which holds `herdr.sock`) read-write so the agent can drive the herdr instance it runs inside. **This is a full sandbox escape**: `herdr.sock` is herdr's full-control JSON-RPC socket and herdr runs outside the sandbox, so an agent with socket access can spawn an unsandboxed host-side pane and run arbitrary commands in it. The socket is therefore not bound by default — pass `--with-herdr` only when you would also be comfortable running with `--no-sandbox`.

### `scripts/pi-unsafe.sh` — Unsandboxed Pi Wrapper

Runs pi with full host access. Calls `inject-pi-extensions.sh` to merge pi-extensions packages into `settings.json`. Symlinks `$CONDA_PREFIX/home/.pi/agent/npm` into `~/.pi/agent/` (copies on Windows, where MSYS bash can't create symlinks; also forces `HOME=%USERPROFILE%` there so bash's `~` matches pi's home dir), then cleans up on exit. Handles `-` argument by creating a temp directory. Unsets all `PIXI_*` and `CONDA_*` env vars plus `INIT_CWD`. Use only for development/debugging.

### `pixi-recipes/pi-extensions` — Pi Plugin Package

Installs a pinned set of pi plugins into `$PREFIX/home/.pi/agent` during the conda build. The plugin pins live in the `PLUGINS` env var in `recipe.yaml`, which is consumed by both `build.sh` (Linux) and `build.bat` (Windows) via the extension-less `script.file: build` mechanism. See `recipe.yaml` for the current plugin list and versions.

Also installs the herdr pi integration by downloading the herdr binary at build time and running `herdr integration install pi`. The output (a TypeScript extension) is deployed to `${PREFIX}/home/.pi/agent/extensions/`.

### `pixi-recipes/claude-extensions` — Claude Code Plugin Package

Installs the herdr Claude Code integration by downloading the herdr binary at build time and running `herdr integration install claude`. The output (hook scripts and settings) is deployed to `${PREFIX}/home/.claude/`.

At runtime, `scripts/inject-claude-extensions.sh` merges these packaged files into the host's `~/.claude/` before the sandbox starts (analogous to `scripts/inject-pi-extensions.sh` for pi).

### `pixi-recipes/claude-home` — Packaged ~/.claude

Packages Claude Code skill directories into `${PREFIX}/home/.claude/skills/`. These are deployed to the host's `~/.claude/skills/` by `scripts/inject-claude-extensions.sh` before the sandbox starts (same pattern as `pixi-recipes/pi-home` for pi).

### `pixi-recipes/pi-home` — Packaged ~/.pi

Uses conda-build to package a fixed

- `~/.pi/skills` (currently `herdr` and `use-gh-cli`)
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

**`-e <env>` is only required when a task name exists in multiple environments.** Tasks that belong to exactly one environment (e.g. `pi`, `claude`, `herdr`, `gh`, `llama-benchy`) can be invoked with plain `pixi run <task>` — pixi selects the environment automatically. The llamacpp tasks (`start-server`, `llama-help`, etc.) exist in all six `llamacpp-*` environments, so `-e` is mandatory there to pick the right backend.

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

1. Create a new directory `pixi-recipes/llama-cpp-source/<name>/` with a `recipe.yaml`
2. The shared `build.sh` reads `BACKEND` env var — add a `case` branch with the relevant `-DGGML_*=ON` flag
3. Add conditional dependencies in the recipe.yaml for `if: backend == "<name>"` blocks

### Version Updates (llama-cpp)

Source builds currently tracks mainline llama.cpp. Binary builds still track mainline releases.

1. **Main branch (active, source builds)**: Check the latest tag on `ggml-org/llama.cpp` releases. Update `version:` under `# Main branch` in all three source recipe.yaml files.
2. **Turboquant fork (commented out, source builds)**: Check the latest tag on `TheTom/llama-cpp-turboquant` branch `feature/turboquant-kv-cache`. Update `context.version` in all three source recipe.yaml files.
3. **Upstream merge**: If the turboquant fork merged upstream main since last update, also update the `# Last sync with main at bNNNN` comment.
4. **Binary builds**: Check the latest tag on `ggml-org/llama.cpp` releases. Update `context.version` in all three binary recipe.yaml files.
5. Run `pixi lock` to regenerate the lockfile.
6. Test all backends.

See the **update-llama-cpp** skill for the detailed step-by-step procedure.

### Version Updates (herdr)

herdr uses one context variable per release channel:

- `version_stable` — plain version string for Linux stable builds (e.g. `"0.7.1"`). Source: `https://herdr.dev/latest.json`. The `v` prefix is added by `build.sh` in the download URL.
- `version_preview` — full preview release tag for Windows (e.g. `preview-2026-06-22-24c7377de01c`). Source: `https://herdr.dev/preview.json`. Windows builds are preview-only.

See the **update-herdr** skill for the detailed step-by-step procedure.

## File Reference

| File                                                       | Purpose                                                                                                                                                 |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AGENTS.md`                                                | Project guide for coding agents                                                                                                                         |
| `README.md`                                                | Project readme                                                                                                                                          |
| `.github/workflows/llamacpp.yml`                           | CI workflow for llama-cpp builds                                                                                                                        |
| `chat-templates/qwen3.6-froggeric-v20.jinja`               | Custom Qwen 3.6 chat template (Jinja)                                                                                                                   |
| `pixi.toml`                                                | Root workspace: features, tasks, environments                                                                                                           |
| `pixi.lock`                                                | Locked dependency versions (binary; never edit)                                                                                                         |
| `kv-perplexity.yaml`                                       | Sample config for `kv-perplexity.py`                                                                                                                    |
| `models.ini`                                               | llama-server multi-model preset config                                                                                                                  |
| `llama-server.log`                                         | Server log (gitignored)                                                                                                                                 |
| `scripts/bwrap-claude.sh`                                  | Bubblewrap sandbox wrapper for Claude Code                                                                                                              |
| `scripts/claude-unsafe.sh`                                 | Unsandboxed Claude Code wrapper (dev/debug only)                                                                                                        |
| `scripts/bwrap-pi.sh`                                      | Bubblewrap sandbox wrapper for pi agent                                                                                                                 |
| `scripts/claude`                                           | Naked `claude` wrapper (installed to ~/.local/bin by `pixi r install`); resolves --bind relative paths against cwd                                      |
| `scripts/diff-llama-cpp-variants.sh`                       | Compare llama-cpp recipe variants                                                                                                                       |
| `scripts/inject-pi-extensions.sh`                          | Merge pi-extensions packages into settings.json                                                                                                         |
| `scripts/inject-claude-extensions.sh`                      | Deploy packaged Claude Code extensions (hooks, settings) into host's ~/.claude                                                                          |
| `scripts/install-apparmor.sh`                              | Install/load AppArmor profile for bwrap (local sudo or CI)                                                                                              |
| `scripts/install-clipboard.sh`                             | Install wl-clipboard (apt) so herdr copy-on-select can write the system clipboard; sudo only if wl-copy missing                                         |
| `scripts/install.sh`                                       | Backs the `install` task; symlinks `scripts/pi`, `scripts/claude`, and `scripts/herdr` into ~/.local/bin                                                |
| `scripts/stop-server.sh`                                   | Graceful llama-server shutdown (SIGTERM → SIGKILL)                                                                                                      |
| `scripts/start-server.sh`                                  | Background llama-server with logging                                                                                                                    |
| `scripts/pi`                                               | Naked `pi` wrapper (installed to ~/.local/bin by `pixi r install`); resolves --bind relative paths against cwd                                          |
| `scripts/pi-unsafe.sh`                                     | Unsandboxed pi wrapper (dev/debug only)                                                                                                                 |
| `scripts/kv-perplexity.py`                                 | KLD sweep over cartesian product of K/V quant combos (`kv-perplexity` task)                                                                             |
| `scripts/kv-kld-report.py`                                 | Parse perplexity log → HTML/Markdown KLD report with plots (`kv-kld-report` task)                                                                       |
| `scripts/llama-cpp-changelog.py`                           | Deterministic llama.cpp changelog dumper: tags + PRs (title/desc/URL) + commits (`llama-cpp-changelog` task)                                            |
| `scripts/gguf-meta-extract.py`                             | Header-only GGUF tensor/VRAM inspector: per-tensor CSV + dense/expert & KV-cache VRAM summary, no weight download (`gguf-meta-extract` task)            |
| `sample-data/wiki.test.raw`                                | Wikitext-2 test corpus for KLD/perplexity benchmarks                                                                                                    |
| `sample-data/wiki.train.head-10k.raw`                      | First 10k lines of wiki.train.raw (~674k tokens; larger KLD baseline)                                                                                   |
| `sample-data/describe-me.jpg`                              | Image for multimodal testing                                                                                                                            |
| `sample-data/README.md`                                    | Sample data documentation                                                                                                                               |
| `sample-data/context-bench/run_benchmark.py`               | Long-context recall benchmark runner (prompts models, grades, writes TOML)                                                                              |
| `sample-data/context-bench/AGENTS.md`                      | System prompt for the model under test                                                                                                                  |
| `sample-data/context-bench/config.toml`                    | Benchmark runner config                                                                                                                                 |
| `sample-data/context-bench/<size>.txt`                     | Benchmark books (16k–256k) with 20 questions appended                                                                                                   |
| `sample-data/context-bench/<size>.answers.txt`             | Reference answers with source line numbers                                                                                                              |
| `pixi-recipes/llama-cpp-source/build.sh`                   | Shared CMake build + install + symlink script                                                                                                           |
| `pixi-recipes/llama-cpp-source/cpu/recipe.yaml`            | CPU build recipe                                                                                                                                        |
| `pixi-recipes/llama-cpp-source/cuda/recipe.yaml`           | CUDA build recipe                                                                                                                                       |
| `pixi-recipes/llama-cpp-source/vulkan/recipe.yaml`         | Vulkan build recipe                                                                                                                                     |
| `pixi-recipes/llama-cpp-binary/build.sh`                   | Linux: copy pre-built binaries + create symlinks                                                                                                        |
| `pixi-recipes/llama-cpp-binary/build.bat`                  | Windows: copy pre-built exes + DLLs into `bin`                                                                                                          |
| `pixi-recipes/llama-cpp-binary/cpu/recipe.yaml`            | CPU binary recipe                                                                                                                                       |
| `pixi-recipes/llama-cpp-binary/vulkan/recipe.yaml`         | Vulkan binary recipe                                                                                                                                    |
| `pixi-recipes/llama-cpp-binary/rocm/recipe.yaml`           | ROCm binary recipe                                                                                                                                      |
| `pixi-recipes/claude/recipe.yaml`                          | Claude Code conda package recipe                                                                                                                        |
| `pixi-recipes/claude/build.sh`                             | Linux: `npm install --global` into prefix                                                                                                               |
| `pixi-recipes/claude/build.bat`                            | Windows: `npm install --global` into prefix                                                                                                             |
| `pixi-recipes/claude-extensions/recipe.yaml`               | Claude Code extensions conda recipe (herdr integration)                                                                                                 |
| `pixi-recipes/claude-extensions/build.sh`                  | Linux: downloads herdr, runs `herdr integration install claude`                                                                                         |
| `pixi-recipes/claude-extensions/build.bat`                 | Windows: same                                                                                                                                           |
| `pixi-recipes/claude-home/recipe.yaml`                     | Packages Claude Code skill directories                                                                                                                  |
| `pixi-recipes/claude-home/build.sh`                        | Linux: copies skills/ into $PREFIX/home/.claude/skills                                                                                                  |
| `pixi-recipes/claude-home/build.bat`                       | Windows: same                                                                                                                                           |
| `pixi-recipes/claude-home/skills/herdr/SKILL.md`           | Skill: control herdr from inside it (pi + claude)                                                                                                       |
| `pixi-recipes/pi-extensions/recipe.yaml`                   | Packages pi plugin set                                                                                                                                  |
| `pixi-recipes/pi-extensions/build.sh`                      | Linux: runs `pi install` for each plugin in `PLUGINS`                                                                                                   |
| `pixi-recipes/pi-extensions/build.bat`                     | Windows: runs `pi install` for each plugin in `PLUGINS`                                                                                                 |
| `pixi-recipes/pi-home/recipe.yaml`                         | Packages pi skill directories                                                                                                                           |
| `pixi-recipes/pi-home/build.sh`                            | Linux: copies skills/ into $PREFIX/home/.pi/agent/skills                                                                                                |
| `pixi-recipes/pi-home/build.bat`                           | Windows: same                                                                                                                                           |
| `pixi-recipes/pi-home/skills/use-gh-cli/SKILL.md`          | Skill: use gh CLI instead of web fetch for GitHub                                                                                                       |
| `pixi-recipes/pi-home/skills/herdr/SKILL.md`               | Skill: control herdr from inside it (pi + claude)                                                                                                       |
| `pixi-recipes/pi-home/AGENTS.md`                           | Global agent instructions for all workspaces                                                                                                            |
| `.agents/skills/*/SKILL.md`                                | Agent skills discovered by pi agent (llama-cpp-changelog, test-git-auth, update-*)                                                                      |
| `.agents/skills/update-herdr/SKILL.md`                     | Skill: update herdr recipe to latest stable+preview releases                                                                                            |
| `pixi-recipes/herdr/recipe.yaml`                           | herdr conda recipe (downloads pre-built binary from GitHub releases)                                                                                    |
| `pixi-recipes/herdr/build.sh`                              | Linux: download herdr binary to `$PREFIX/bin`                                                                                                           |
| `pixi-recipes/herdr/build.bat`                             | Windows: download herdr.exe to `%PREFIX%\bin`                                                                                                           |
| `scripts/herdr`                                            | Naked `herdr` wrapper (installed to ~/.local/bin by `pixi r install`)                                                                                   |
| `scripts/run-herdr.sh`                                     | Task-time launcher: reorders PATH so `~/.local/bin` precedes the conda prefix, otherwise `pi`/`claude` spawned inside herdr bypass the sandbox wrappers |
| `sample-data/context-bench/README.md`                      | Context-bench documentation                                                                                                                             |
| `sample-data/context-bench/aggregate_benchmark_results.py` | Aggregates multiple context-benchmark runs (`aggregate-context-bench` task)                                                                             |

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
- **Never hardcode git revisions** — update `context.version` (the git tag) in all three source recipe.yaml files. The `source:` block uses template variables (`${{ fork }}`, `${{ version }}`), not a hardcoded commit SHA.
- **`build.sh` uses `${PREFIX}`** — set by rattler-build; never reference it outside build scripts
- **Symlinks use relative paths** (`../opt/llama/...`) — required for correct conda prefix portability
- **All workspaces target `linux-64`, `linux-aarch64`, and `win-64`** — cross-platform support requires additional logic
- **`bwrap-pi.sh` unsets all `PIXI_*`/`CONDA_*`/`INIT_CWD` vars** before calling pi — the agent must not see conda internals
- **`start-server.sh` starts llama-server in background** with logging to `llama-server.log`; use `stop-server` to gracefully kill it
- **`stop-server.sh` uses SIGTERM first, then SIGKILL after timeout** — graceful shutdown pattern
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
