# pixi-llm-recipes

My personal setup for running LLMs locally and using them with the [pi](https://pi.dev) coding agent.
Everything lives in [pixi](https://pixi.sh/) environments, so there's no Docker and
nothing to install by hand: clone the repo, `pixi install`, and you're done.
Everything is pinned and 100% reproducible.

## A word of warning

This is tuned for my own machine, which mounts an NVIDIA RTX 3080 with 10 GB of VRAM.
Model sizes, context lengths, and quantization choices all reflect that constraint. If
your GPU is different you'll probably want to adjust `models.ini` and pick different
models.

## Quickstart

### Linux

```bash
curl -fsSL https://pixi.sh/install.sh | sh  # One-off installation
pixi r install-apparmor  # One-off installation
pixi r start-server
pixi r pi /path/to/workspace
pixi r stop-server
```

### Windows

```bash
powershell -ExecutionPolicy Bypass -c "irm -useb https://pixi.sh/install.ps1 | iex"  # One-off installation
pixi r start-server
pixi r pi-unsafe /path/to/workspace
pixi r stop-server
```

## llama.cpp variants

There are six pixi environments to choose from: three that compile llama.cpp from
source, and three that just unpack the pre-built binaries from upstream releases.

| Environment              | Build            | Backend      | Linux x64 | Linux ARM | Windows x64 |
| ------------------------ | ---------------- | ------------ | --------- | --------- | ----------- |
| `llamacpp-source-cpu`    | from sources     | CPU only     | ✅        | ✅        | 🔴          |
| `llamacpp-source-cuda`   | from sources     | CPU + CUDA   | ✅        | 🔴        | 🔴          |
| `llamacpp-source-vulkan` | from sources     | CPU + Vulkan | ✅        | ✅        | 🔴          |
| `llamacpp-binary-cpu`    | pre-built binary | CPU only     | ✅        | ✅        | ✅          |
| `llamacpp-binary-vulkan` | pre-built binary | CPU + Vulkan | ✅        | ✅        | ✅          |
| `llamacpp-binary-rocm`   | pre-built binary | CPU + ROCm   | ✅        | 🔴        | 🔴          |

The binary environments are much faster to set up since they skip compilation entirely.
The source environments are the only option to get CUDA (which on my hardware is faster
than Vulkan) and can be easily adapted to compile PRs and forks. By default they build
from the main branch, but you can trivially change the recipes to switch to the
[TheTom/llama-cpp-turboquant](https://github.com/TheTom/llama-cpp-turboquant) fork with
KV cache optimizations for improved long-context compression.

To start llama-cpp interactively (you will be asked on which environment you want to run):

```bash
pixi r llama-version       # llama-server --version
pixi r llama-list-devices  # llama-server --list-devices (output changes with backend!)
pixi r llama-hello         # Download a model, load it, prompt "Hello world" and exit
pixi r start-server        # Start the llama-server router in the background on port 8080
pixi r stop-server 
pixi r restart-server
```

Alternatively, you can select an environment non-interactively:

```bash
pixi r -e llamacpp-source-cuda start-server
```

## Models

Models are defined in `models.ini` (llama-server's native preset format) and are
served on demand. All models were carefully cherry-picked and tuned.

| Model           | Variant                      | Size on disk | Context<sup>1</sup> | VRAM<sup>2</sup> | Speed<sup>3</sup> | Notes                                                                                                                   |
| --------------- | ---------------------------- | ------------ | ------------------- | ---------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Qwen3.6-35B-A3B | ByteShape MTP IQ4_XS-3.97bpw | 18 GB        | 256k q8/q8          | 7.4 GB           | ~56 tok/s         | best quality that performs well; the daily driver                                                                       |
| Qwen3.6-27B     | Unsloth Q4_K_M MTP           | 18 GB        | 256k q8/q8          | ~28 GB           | ~2 tok/s          | doesn't fit                                                                                                             |
| Qwen3.5-9B      | Unsloth Q4_K_M MTP           | 6.4 GB       | 64k q8/q8           | 8.2 GB           | 163 tok/s         | limited context in VRAM                                                                                                 |
| MiniCPM5-1B     | Q4_K_M                       | 0.7 GB       | 128k q8/q8          | 2.7 GB q8/q8     | ~455 tok/s        | [tool calls don't work yet](https://github.com/ggml-org/llama.cpp/pulls?q=MiniCPM5)                                     |
| Gemma4-E2B      | Unsloth QAT MTP              | 3.5 GB       | 128k q8/q8          | 3.8 GB           | ~290 tok/s        |                                                                                                                         |
| Gemma4-E4B      | Unsloth QAT                  | 5.0 GB       | 128k q8/q8          | 5.8 GB           | ~143 tok/s        | [MTP doesn't support quantized V-cache](https://huggingface.co/unsloth/gemma-4-E4B-it-qat-GGUF/blob/main/MTP/README.md) |
|                 | Unsloth QAT MTP              | 5.0 GB       | 64k f16/f16         | 7.2 GB           | ~221 tok/s        | full unquantized context doesn't fit                                                                                    |
| Gemma4-12B      | Unsloth QAT MTP              | 6.7 GB       | 256k q8/q8          | 8.0 GB           | ~19 tok/s         | full context in host RAM                                                                                                |
|                 | Unsloth QAT MTP              | 6.7 GB       | 32k q8/q8           | 8.2 GB           | ~104 tok/s        | limited context in VRAM                                                                                                 |
| Gemma4-26B-A4B  | Unsloth QAT MTP              | 15 GB        | 256k q8/q8          | 8.2 GB           | ~32 tok/s         |                                                                                                                         |
| Gemma4-31B      | Unsloth QAT MTP              | 18 GB        | 256k q8/q8          | ~31 GB           | ~2 tok/s          | doesn't fit                                                                                                             |

**Notes:**

- <sup>1</sup>[Turboquant](https://github.com/TheTom/llama-cpp-turboquant) cache compression is not
  available in the main branch and not necessary with the above models/VRAM
  configuration. [turbo4 is not particularly better than q4_0](perplexity/README.md).
  You can enable Turboquant by uncommenting it in
  `pixi-recipes/llama-cpp-source/*/recipe.yaml`.
- <sup>2</sup>When sizing video card VRAM, you must add ~2 GiB for your desktop (unless you're
  running on an integrated video card and your discrete card is detached from the X
  server)
- <sup>3</sup> Decode speed measured on the RTX 3080

### Tweaking models

`models.ini` is heavily commented. You should read it and tweak it for your needs.

## The pi coding agent

[pi](https://pi.dev) is configured so that only configuration lives in `~/.pi`.
All extensions are installed ephimerally in your pixi environment.

### Extensions

The `pi-extensions` conda package installs a pinned selection of pi plugins, so the
agent setup is versioned and reproducible:

| Extension                                                                            | Purpose                                              |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------- |
| [pi-autoresearch](https://pi.dev/packages/pi-autoresearch)                           | autonomous experiment loops for optimization         |
| [pi-btw](https://pi.dev/packages/pi-btw)                                             | build-time workspace tooling                         |
| [pi-llama-cpp](https://pi.dev/packages/pi-llama-cpp)                                 | zero-config llama.cpp integration                    |
| [pi-ollama-cloud](https://pi.dev/packages/pi-ollama-cloud)                           | Ollama cloud model provider + web search / web fetch |
| [rpiv-ask-user-question](https://pi.dev/packages/@juicesharp/rpiv-ask-user-question) | stop and ask the user when in doubt                  |
| [pi-token-speed](https://pi.dev/packages/pi-token-speed)                             | token throughput monitoring                          |
| [pi-usage-extension](https://pi.dev/packages/@tmustier/pi-usage-extension)           | tokens usage tracking                                |
| [caveman](https://github.com/JuliusBrussee/caveman)                                  | drastically reduce output tokens consumed            |
| [rtk](https://github.com/rtk-ai/rtk)                                                 | drastically reduce input tokens consumed             |
| [@tintinweb/pi-subagents](https://github.com/tintinweb/pi-subagents)                 | spawn sub-agents for complex tasks _(tweaked)_       |

### Sandboxed vs. unsandboxed

By default pi runs inside a bubblewrap container: read-only root filesystem and no
access to /home beyond the workspace directory you point it at.
This is the recommended way to run it (Linux only).

```bash
pixi r install-apparmor                     # one-off: install AppArmor profile for BubbleWrap
pixi r pi /path/to/workspace                # sandboxed
pixi r pi                                   # sandboxed in a temporary directory (just for chatting)
pixi r pi /path/to/workspace -- -p "Hello"  # Pass arbitrary parameters
pixi r pi - -- -p "Hello"                   # In a temporary directory; pass arbitrary parameters
pixi r pi /workspace -- --bind /data --bind /models  # Bind extra directories into the sandbox
```

If you need full host access for development or debugging, or if you are on Windows,
there's an escape hatch:

```bash
pixi r pi-unsafe /path/to/workspace
pixi r pi-unsafe                                   # In a temporary directory
                                                   # (useful to run with no AGENTS.md)
pixi r pi-unsafe /path/to/workspace -- -p "Hello"  # Pass arbitrary parameters
pixi r pi-unsafe - -- -p "Hello"                   # In a temporary directory; pass arbitrary parameters
```

## Claude Code

[Claude Code](https://claude.ai/code) is installed as part of the `agents` pixi
environment — no separate system-wide installation needed. Just `pixi install` and run.

### Sandboxed usage

```bash
pixi r claude /path/to/workspace                   # sandboxed
pixi r claude                                      # sandboxed in a temporary directory
pixi r claude /path/to/workspace -- --with-git     # also allow git push and gh CLI
pixi r claude /path/to/workspace -- --bind /data   # bind an extra directory read-write
pixi r claude /path/to/workspace -- --resume       # pass arbitrary parameters to claude after --
```

The sandbox uses the same AppArmor profile as Pi. Install it once with:

```bash
pixi r install-apparmor
```

If you need full host access for development or debugging, or if you are on Windows,
there's an escape hatch:

```bash
pixi r claude-unsafe /path/to/workspace
pixi r claude-unsafe                                      # in a temporary directory
pixi r claude-unsafe /path/to/workspace -- --resume       # pass arbitrary parameters to claude after --
```

## git and GitHub CLI

The `agents` environment bundles `git` and the [GitHub CLI](https://cli.github.com/)
(`gh`) from conda-forge. They are available as plain commands in the `agents` environment
and are also on `PATH` inside both sandboxes:

```bash
pixi r git status
pixi r gh pr list
```

### git push and gh inside the agent sandbox

By default the `pixi r pi` and `pixi r claude` sandboxes block `git push` on public
accounts, all remote git commands on private accounts, and the `gh` CLI (to read/run CI,
open and interact on PRs, etc). Pass `--with-git` to allow the agent to act as you on
your GitHub account.

```bash
pixi r pi /path/to/workspace -- --with-git
pixi r claude /path/to/workspace -- --with-git
```

To verify everything is wired up correctly before starting real work, run either:

- `pixi r pi . -- --with-git "run the test-git-auth skill"`
- `pixi r claude . -- --with-git "run the test-git-auth skill"`

## Benchmarking

`llama-benchy` measures tok/s throughput against a live llama-server, so start one first
(e.g. `pixi r -e llamacpp-source-cuda start-server`). Then:

```bash
pixi r llama-benchy --model <model alias>
```

Where `<model alias>` is one defined in `models.ini`.
To get the list, you can just run:

```bash
pixi r llama-benchy
```

### KV cache quantization quality

`kv-perplexity` runs `llama-perplexity` over the cartesian product of K-quant × V-quant
combinations, measuring KL divergence against an f16/f16 baseline. Edit
`scripts/kv-perplexity.yaml` to set the model and quant lists, then:

```bash
pixi r -e llamacpp-source-cuda kv-perplexity -c scripts/kv-perplexity.yaml
pixi r kv-kld-report perplexity.log -o kv-kld-report.html
```

`kv-kld-report.py` parses the log and generates an HTML report (interactive Chart.js plot)
plus a Markdown report with a static SVG.

The [`perplexity/`](perplexity/README.md) folder holds committed sweeps for Qwen3.6-35B-A3B
and Gemma4-E2B with a summary of the findings — in short: `q8/q8` is nearly free, `turbo4`
is not the "almost q8" the internet claims, the K cache matters more than V (so asymmetric
caches win), and `q4/q4` is acceptable on Qwen but catastrophic on Gemma. See
[`perplexity/README.md`](perplexity/README.md) for the numbers and the per-model reports.

### Long-context recall

`context-bench` measures how well a model recalls discrete facts scattered through a long
context (16k–256k tokens), and how — or whether — that degrades under pressure such as a
quantized KV cache. With a llama-server running:

```bash
pixi r context-bench sample-data/context-bench/config.toml -o results.toml
```

It's an extractive needle-recall task, which is deliberately robust: it will not show the
KV-cache quantization harm that `kv-perplexity` does, and its run-to-run variance is
large. See [`sample-data/context-bench/README.md`](sample-data/context-bench/README.md)
for why, and how to read the numbers without over-interpreting them.

## Maintenance

There are skills available; you can ask pi to

- _"Summarize changes in the latest llama.cpp"_ (installed version vs. latest upstream)
- _"Update everything"_ (llama.cpp recipes, pixi extensions recipes, pixi environments)

## Missing features

Only `linux-64`, `linux-aarch64`, and `win-64` are configured. There are no macOS or
Windows CUDA builds simply because I have no hardware to test them on. There are no
Windows source builds because the OS defeated me. PRs welcome if you can validate them.
