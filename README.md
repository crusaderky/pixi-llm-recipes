# pixi-llm-recipes

My personal setup for running LLMs locally and using them with the [pi](https://pi.dev) coding agent.
Everything lives in [pixi](https://pixi.sh/) environments, so there's no Docker and
nothing to install by hand: clone the repo, `pixi install`, and you're done.
Everything is pinned and 100% reproducible.

## A word of warning

This is tuned for my own machine, which mounts an NVIDIA RTX 3090 with 24 GB of VRAM.
Model sizes, context lengths, and quantization choices all reflect that constraint. If
your GPU is different you'll probably want to adjust `models.ini` and pick different
models.

If you have an RTX 3080 (10 GB), you can find the old `models.ini` from that era at the
[RTX3080 tag](https://github.com/crusaderky/pixi-llm-recipes/blob/RTX3080/models.ini).

## Quickstart

### Linux

```bash
curl -fsSL https://pixi.sh/install.sh | sh  # One-off installation
pixi r install  # One-off installation of several components
pixi r start-server  # Start llama.cpp server for local models
cd /path/to/workspace && pi  # Just like regular pi, but managed by pixi and sandboxed
cd /path/to/workspace && claude
herdr                              # Terminal multiplexer / agent orchestrator
pixi r stop-server
pixi r uninstall
```

### Windows

```bash
powershell -ExecutionPolicy Bypass -c "irm -useb https://pixi.sh/install.ps1 | iex"  # One-off installation
pixi r start-server  # Start llama.cpp server for local models
pixi r pi-unsafe /path/to/workspace  # Just like regular pi, but managed by pixi
pixi r claude-unsafe /path/to/workspace
pixi r stop-server
```

## llama.cpp variants

This project builds [beellama.cpp](https://github.com/Anbeeld/beellama.cpp) by default.
Mainline [llama.cpp](https://github.com/ggml-org/llama.cpp) is available as a
commented-out variant in the recipes (`pixi-recipes/llama-cpp-*/recipe.yaml`); swap the
active/commented `fork:` blocks to go back to upstream.

There are eight pixi environments to choose from: four that compile llama.cpp from
source, and four that just unpack the pre-built binaries from upstream releases:

| Environment              | Build            | Backend      | Linux x64 | Linux ARM | Windows x64 |
| ------------------------ | ---------------- | ------------ | --------- | --------- | ----------- |
| `llamacpp-source-cpu`    | from sources     | CPU only     | ✅        | ✅        | 🔴          |
| `llamacpp-source-cuda`   | from sources     | CPU + CUDA   | ✅        | 🔴        | 🔴          |
| `llamacpp-source-vulkan` | from sources     | CPU + Vulkan | ✅        | ✅        | 🔴          |
| `llamacpp-source-rocm`   | from sources     | CPU + ROCm   | ✅        | 🔴        | 🔴          |
| `llamacpp-binary-cpu`    | pre-built binary | CPU only     | ✅        | ✅        | ✅          |
| `llamacpp-binary-cuda`   | pre-built binary | CPU + CUDA   | ✅        | 🔴        | 🔴          |
| `llamacpp-binary-vulkan` | pre-built binary | CPU + Vulkan | ✅        | 🔴        | ✅          |
| `llamacpp-binary-rocm`   | pre-built binary | CPU + ROCm   | ✅        | 🔴        | 🔴          |

The binary environments are much faster to set up since they skip compilation
entirely. The source environments can be easily adapted to compile PRs and forks.

Unlike the CUDA and Vulkan source builds, `llamacpp-source-rocm` compiles against the
**system** ROCm rather than conda packages, because conda-forge does not ship
hipBLAS/rocBLAS. Ubuntu 26.04 provides a complete ROCm 7.1 in the standard archive
(`hipcc clang-21 libamdhip64-dev librocblas-dev libhipblas-dev rocm-device-libs-21`);
no third-party AMD apt repo is needed. It targets `gfx1150;gfx1151` by default —
override with `LLAMA_GPU_TARGETS=<gfx…> pixi install -e llamacpp-source-rocm` for other
GPUs — and requires a matching system ROCm at runtime.

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

## Forge guardrails proxy

[forge](https://github.com/antoinezambelli/forge) is a transparent reliability layer for
tool-calling: it validates every tool call in the model's response, rescue-parses
malformed ones, and retries inference with corrective feedback when validation fails.

`start-forge-server` starts the llama-server router on port **8081** and the forge
proxy in front of it on port **8080** — the same port a bare `start-server` binds, so
agents and benchmarks pointing at `http://localhost:8080/v1` pick up the guardrails
without any reconfiguration.

```bash
pixi r start-forge-server    # Start llama-server on 8081 + forge on 8080
pixi r stop-forge-server     # Stop both forge and llama-server
pixi r restart-forge-server
```

It is a drop-in replacement for `start-server`, `stop-server`, and `restart-server`.
Nothing else changes.

## Models

Models are defined in `models.ini` (llama-server's native preset format) and are
served on demand. All models were carefully cherry-picked and tuned.

| Model              | Variant            | Size on disk | Context<sup>1</sup> | VRAM<sup>2</sup>    | Prefill<sup>3</sup> | Decode<sup>3</sup> | Notes                                 |
| ------------------ | ------------------ | ------------ | ------------------- | ------------------- | ------------------- | ------------------ | ------------------------------------- |
| Qwen3.8-27B        | IQ4_XS MTP         | 15 GB        | 256k kvarn4         | 20.3 GB             | 1,028 tok/s         | 60 tok/s           |                                       |
| Kat-Coder-V2.5-Dev | APEX I-Compact MTP | 17 GB        | 256k kvarn5 t1024   | 19.8 GB             | 2,168 tok/s         | 150 tok/s          |                                       |
| Gemma4-E2B         | QAT MTP            | 3.5 GB       | 128k q8/q8          | 3.8 GB              | 6,434 tok/s         | 236 tok/s          |                                       |
| Gemma4-31B         | QAT MTP            | 18 GB        | 64k q8/q8           | 21.7 GB             | 836 tok/s           | 75 tok/s           | limited context                       |
| LFM2.5-230M        | Q4_K_M             | 147 MB       | 32k q8/q8           | 712 MB              | 58,917 tok/s        | 678 tok/s          | for smoke testing purposes            |
| LFM2.5-2.6B        | Q8_0 DSpark        | 2.9 GB       | 128k q8/q8          | 6.5 GB              | 8,065 tok/s         | 230 tok/s          |                                       |
| LFM2.5-VL-3B       | Q8_0               | 3.3 GB       | 32k q8/q8           | 4.0 GB              | 11,664 tok/s        | 211 tok/s          |                                       |
| Laguna-S-2.1       | IQ4_XS             | 54 GB        | 256k kvarn5 t1024   | 20.5 GB<sup>4</sup> | 137 tok/s           | 13 tok/s           |                                       |
| DeepSeek-V4-Flash  | IQ2_XXS            | 85 GB        | 256k f16/f16        | 22.0 GB<sup>4</sup> | 64 tok/s            | 11 tok/s           | KV cache quantization is very harmful |
| Muse-Glimmer-30B   | Q4_K_XL DFlash     | 19 GB        | 256k kvarn6 t1024   | 18.7 GB             | 938 tok/s           | 124 tok/s          |                                       |

**Notes:**

- <sup>1</sup>KV cache compression is set per-model via `cache-type-k`/`cache-type-v` in
  `models.ini`. The default fork
  ([beellama.cpp](https://github.com/Anbeeld/beellama.cpp)) adds KVarN low-bit cache
  quants (`kvarn4`, `kvarn3`) on top of upstream's `q8_0`/`q4_0`.
- <sup>2</sup>Process total measured by nvidia-smi. When sizing video card VRAM, you
  must add ~2 GiB for your desktop (unless you're running on an integrated video card
  and your discrete card is detached from the X server)
- <sup>3</sup> Speed measured on the RTX 3090
- <sup>4</sup> Experts partially offloaded to host RAM. Speed is capped by PCIe bandwidth
  for prefill and by host RAM bandwidth for decode.

### Estimating model size and VRAM

`gguf-meta-extract` inspects a GGUF model on Hugging Face **without downloading it**: it
fetches only the header (a few MB, never the multi-GB tensor payload), parses the tensor
table, and writes a per-tensor CSV file (layer, tensor_name, geometry, n_points, quant,
bytes_per_point, total_bytes). It also prints a summary to stderr:

- model weights split into routed-expert (`*_exps.*`), shared-expert (`*_shexp*`), dense
  FFN (`ffn_{gate,up,down}`) and everything-else bytes — empty buckets are omitted, so a
  dense model shows only the last two — plus the fraction of routed-expert weight actually
  activated per token on MoE models;
- a KV-cache size estimate at 256k tokens across several cache quantizations (`f16`,
  `q8_0`, `kvarn4`, `kvarn3`), derived from the model's hyperparameters (handles GQA,
  per-layer head counts, sliding-window attention, and MLA/latent caches);
- various overheads in VRAM

Set `HF_TOKEN` for private/gated repos.

```bash
# Whole quant directory:
pixi r gguf-meta-extract https://huggingface.co/unsloth/GLM-5.2-GGUF/tree/main/UD-IQ1_S
# A single file or * glob pattern, when several variants share one folder:
pixi r gguf-meta-extract 'https://huggingface.co/YanissAmz/Hy3-295B-A21B-GGUF/blob/main/Hy3-UD128*'
```

### Tweaking models

`models.ini` is heavily commented. You should read it and tweak it for your needs.

### Locked-memory limit (`mlock`)

`models.ini` sets `mlock = true` so llama-server locks the model weights into
RAM and the kernel can never page them out. Even in absence of memory pressure
from other applications, this drastically speed prefill of partially spilled MoE
models, as it prevents an extra copy when transferring tensors to VRAM. This
flag needs a high locked-memory limit; the stock Ubuntu default of `ulimit -l`
can be as low as 8192 (8 MiB) which is far too small for multi-GiB models.

`pixi r install` (specifically `pixi r install-memlock`) raises the limit to
unlimited by writing `/etc/security/limits.d/99-memlock.conf` (needs sudo). PAM
only applies that at login, so **log out and back in (or reboot)** afterwards.
Verify with:

```bash
ulimit -l  # should print "unlimited"
```

### Simulating a smaller host (`--host-ram`)

Caps the RAM **and page cache** llama-server gets, to see how a model behaves on
a machine with less RAM than this one (Linux + systemd only; off by default):

```bash
pixi r start-server --host-ram 16G  # cgroup v2 MemoryMax, swap disabled
systemctl --user status llama-server-8080.scope  # prints "Memory: … (max: …)"
```

Use it with `load-mode = mmap`: mlock'd weights can't be evicted, so the server
is OOM-killed instead of paging. Drop the page cache first
(`sudo sysctl -w vm.drop_caches=3`), or weights left warm by a previous run
remain free to the capped one.

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
| [pi-token-speed](https://pi.dev/packages/pi-token-speed)                             | token throughput monitoring                          |
| [pi-usage-extension](https://pi.dev/packages/@tmustier/pi-usage-extension)           | tokens usage tracking                                |
| [rpiv-ask-user-question](https://pi.dev/packages/@juicesharp/rpiv-ask-user-question) | stop and ask the user when in doubt                  |
| [caveman](https://github.com/JuliusBrussee/caveman)                                  | drastically reduce output tokens consumed            |
| [rtk](https://github.com/rtk-ai/rtk)                                                 | drastically reduce input tokens consumed             |

**Note:** The effects of `pi install` will be wiped the next time your pixi environment is regenerated!
You should update `pixi-recipes/pi-extension/recipe.yaml` instead.

### Sandboxed vs. unsandboxed

By default pi runs inside a bubblewrap container: read-only root filesystem and no
access to /home beyond the workspace directory you point it at.
This is the recommended way to run it (Linux only).

```bash
pixi r install                        # One-off
cd /path/to/workspace && pi           # Sandboxed
pi --bind /data                       # Bind extra directories into the sandbox
pi --with-git                         # Enable `git push`, `git` pull/fetch from private repos, and `gh`
```

If you need full host access for development or debugging, or if you are on Windows,
there's an escape hatch:

```bash
pi --no-sandbox                                    # Linux
pixi r pi-unsafe /path/to/workspace                # Windows
pixi r pi-unsafe /path/to/workspace -- -p "Hello"  # Windows; pass arbitrary parameters (note --)
```

## Claude Code

[Claude Code](https://claude.ai/code) is deployed and managed exactly like Pi;
no separate system-wide installation needed.

Linux:

```bash
pixi r install                        # One-off
cd /path/to/workspace && claude       # Sandboxed
claude --no-sandbox                   # Full system access
claude --bind /data                   # Bind extra directories into the sandbox
claude --with-git                     # Enable `git push`, `git` pull/fetch from private repos, and `gh`
```

Windows:

```bash
pixi r claude-unsafe /path/to/workspace              # Full system access
pixi r claude-unsafe                                 # in a temporary directory
pixi r claude-unsafe /path/to/workspace -- --resume  # pass arbitrary parameters to claude after --
```

## herdr

[herdr](https://herdr.dev) is an agent-first terminal multiplexer and coding-agent
orchestrator. It is packaged as a conda recipe in `pixi-recipes/herdr/` and installed
alongside pi and claude.

Linux:

```bash
pixi r install      # One-off
herdr               # Launch herdr
```

After `pixi r install` you can also launch herdr directly from your
Gnome/Unity start menu.

Windows:

```bash
pixi r herdr        # Launch herdr
```

### herdr-file-viewer plugin

herdr comes with [herdr-file-viewer](https://github.com/smarzban/herdr-file-viewer)
pre-installed and pre-configured, bound to the following keyboard shortcuts:

- Ctrl+F: open it in a split pane
- Alt+F: open it in a new tab

`pixi r install` also installs its optional Markdown/terminal renderers (`delta`, `bat`,
`glow`) when missing, so files render nicely inside the viewer.

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
pi --with-git
claude --with-git
```

To verify everything is wired up correctly before starting real work, run either:

- `pi --with-git "run the test-git-auth skill"`
- `claude --with-git "run the test-git-auth skill"`

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

### Model and KV cache quantization quality

`perplexity` runs `llama-perplexity` over a cross-product of arbitrary command-line
options, measuring KL divergence against a single high-precision baseline. The options are
not restricted to the KV cache: one sweep can vary the cache quants
(`cache-type-k`/`cache-type-v`/`kv-tail-tokens`), the model quantization (`hf-repo`), or
both at once. Edit `perplexity.yaml`, then:

```bash
pixi r -e llamacpp-source-cuda perplexity -c perplexity.yaml
pixi r perplexity-report perplexity.log -o perplexity-report
```

`perplexity-report.py` parses the log and generates an HTML report (interactive Chart.js
plot) plus a Markdown report with static SVGs. Each run is labelled with whatever the
sweep varied — `q8_0/q5_1 t1024`, `UD-Q4_K_XL`, `LiquidAI:Q8_0`, or
`UD-Q4_K_XL|q8_0/q5_1 t1024` — and the
Pareto/x-axis cost is total VRAM: the model weights (read off the log's own provenance)
plus the KV cache at the projected context.

The [`perplexity/`](perplexity/README.md) folder holds committed sweeps for
Qwen3.6-35B-A3B and Gemma4-E2B with a summary of the findings — in short: `q8/q8` is
nearly free, `turbo4` is mostly the same as `q4`, the K cache matters more than V (so
asymmetric caches win), and `q4/q4` is acceptable on Qwen but catastrophic on Gemma. See
[`perplexity/README.md`](perplexity/README.md) for the numbers and the per-model
reports.

### Long-context recall

`context-bench` measures how well a model recalls discrete facts scattered through a long
context (16k–256k tokens), and how — or whether — that degrades under pressure such as a
quantized KV cache. With a llama-server running:

```bash
pixi r context-bench sample-data/context-bench/config.toml -o results.toml
```

It's an extractive needle-recall task, which is deliberately robust: it will not show the
KV-cache quantization harm that `perplexity` does, and its run-to-run variance is
large. See [`sample-data/context-bench/README.md`](sample-data/context-bench/README.md)
for why, and how to read the numbers without over-interpreting them.

## Maintenance

There are skills available; you can ask pi to

- _"Summarize changes in the latest llama.cpp"_ (installed version vs. latest upstream)
- _"Update everything"_ (llama.cpp recipes, pi-extensions, Claude Code, herdr, pixi environments)

## Missing features

Only `linux-64`, `linux-aarch64`, and `win-64` are configured. There are no macOS or
Windows CUDA builds simply because I have no hardware to test them on. There are no
Windows source builds because the OS defeated me. PRs welcome if you can validate them.
