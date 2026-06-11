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

| Environment | Build | Backend | Linux x64 | Linux ARM | Windows x64 |
|---|---|---|---|---|---|
| `llamacpp-source-cpu` | from sources | CPU only | ✅ | ✅ | 🔴 |
| `llamacpp-source-cuda` | from sources | CPU + CUDA | ✅ | 🔴 | 🔴 |
| `llamacpp-source-vulkan` | from sources | CPU + Vulkan | ✅ | ✅ | 🔴 |
| `llamacpp-binary-cpu` | pre-built binary | CPU only | ✅ | ✅ | ✅ |
| `llamacpp-binary-vulkan` | pre-built binary | CPU + Vulkan | ✅ | ✅ | ✅ |
| `llamacpp-binary-rocm` | pre-built binary | CPU + ROCm | ✅ | 🔴 | 🔴 |

The binary environments are much faster to set up since they skip compilation entirely.
The source environments are the only option to get CUDA (which on my hardware is faster
than Vulkan) and can be easily adapted to compile PRs and forks.

To start llama-cpp interactively (you will be asked on which environment you want to run):

```bash
pixi r llama-version       # llama-server --version
pixi r llama-list-devices  # llama-server --list-devices (output changes with backend!)
pixi r llama-hello         # Download a model, load it, prompt "Hello world" and exit
pixi r start-server        # Start the llama-server router in the background on port 8080
pixi r stop-server 
pixi r restart-server
pixi r llama-perplexity    # Run llama-perplexity with standard wiki data
```

Alternatively, you can select an environment non-interactively:

```bash
pixi r -e llamacpp-source-cuda start-server
```

## Models

Models are defined in `models.ini` (llama-server's native preset format) and are
served on demand. All models were carefully cherry-picked and tuned.

| Model | Variant | Size on disk | VRAM<sup>1</sup> | Speed<sup>2</sup> | Notes |
|---|---|---|---|---|---|
| Qwen3.6-35B-A3B | ByteShape MTP IQ4_XS-3.97bpw | 18 GB | 7.4 GB | ~56 tok/s | best quality that performs well; the daily driver |
| MiniCPM5-1B | Q4_K_M | 0.7 GB | 2.7 GB | ~455 tok/s | tool-use doesn't work yet |
| Gemma4-E2B | Unsloth QAT + MTP | 3.5 GB | 4.0 GB | ~290 tok/s | multimodal |
| Gemma4-E4B | Unsloth QAT | 5.0 GB | 5.8 GB | ~137 tok/s | multimodal |
| Gemma4-12B | Unsloth QAT + MTP | 6.7 GB | 8.7 GB<sup>3</sup> | ~120 tok/s | multimodal; barely fits in 10 GiB VRAM |
| Gemma4-26B-A4B | Unsloth QAT + MTP | 15 GB | 8.2 GB | ~32 tok/s | multimodal |
| Gemma4-31B | Unsloth QAT + MTP | 18 GB | ~31 GB | ~2 tok/s | multimodal; doesn't fit |

**Notes:**

- <sup>1</sup>When sizing video card VRAM, you must add 1~2 GiB for your desktop (unless you're
  running on an integrated video card and your discrete card is detached from the X
  server)
- <sup>2</sup> As measured on the RTX 3080
- <sup>3</sup> Context limited to 64k
- No Qwen3.6-27B yet.
- KV cache quantized to `q8_0` for all models.
- No turboquant as it would preclude getting the latest releases from the main branch.

### Tweaking models

`models.ini` is heavily commented. You should read it and tweak it for your needs.

## The pi coding agent

[pi](https://pi.dev) is configured so that only configuration lives in `~/.pi`.
All extensions are installed ephimerally in your pixi environment.

### Extensions

The `pi-extensions` conda package installs a pinned selection of pi plugins, so the
agent setup is versioned and reproducible:

| Extension | Purpose |
|---|---|
| [pi-autoresearch](https://pi.dev/packages/pi-autoresearch) | autonomous experiment loops for optimization |
| [pi-btw](https://pi.dev/packages/pi-btw) | build-time workspace tooling |
| [pi-llama-cpp](https://pi.dev/packages/pi-llama-cpp) | zero-config llama.cpp integration |
| [pi-ollama-cloud](https://pi.dev/packages/pi-ollama-cloud) | Ollama cloud model provider + web search / web fetch |
| [rpiv-advisor](https://pi.dev/packages/@juicesharp/rpiv-advisor) | your local model can ask a larger datacenter model when in trouble |
| [rpiv-ask-user-question](https://pi.dev/packages/@juicesharp/rpiv-ask-user-question) | stop and ask the user when in doubt |
| [pi-token-speed](https://pi.dev/packages/pi-token-speed) | token throughput monitoring |
| [pi-usage-extension](https://pi.dev/packages/@tmustier/pi-usage-extension) | tokens usage tracking |
| [caveman](https://github.com/JuliusBrussee/caveman) | drastically reduce output tokens consumed |
| [rtk](https://github.com/rtk-ai/rtk) | drastically reduce input tokens consumed |

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

## Benchmarking

`llama-benchy` measures tok/s throughput against a live llama-server, so start one first
(e.g. `pixi run -e llamacpp-source-cuda start-server`). Then:

```bash
pixi r llama-benchy --model <model alias>
```

Where `<model alias>` is one defined in `models.ini`.
To get the list, you can just run:

```bash
pixi r llama-benchy
```

## Maintenance

There are skills available; you can ask pi to

- _"Summarize changes in the latest llama.cpp"_ (installed version vs. latest upstream)
- _"Update everything"_ (llama.cpp recipes, pixi extensions recipes, pixi environments)

## Missing features

Only `linux-64`, `linux-aarch64`, and `win-64` are configured. There are no macOS or
Windows CUDA builds simply because I have no hardware to test them on. There are no
Windows source builds because the OS defeated me. PRs welcome if you can validate them.
