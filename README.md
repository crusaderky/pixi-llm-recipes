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
pixi r install  # One-off installation (apparmor, memlock limit, ~/.local/bin, herdr desktop launcher)
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

| Model           | Variant            | Size on disk | Context<sup>1</sup> | VRAM<sup>2</sup>   | Prefill<sup>3</sup>   | Decode<sup>3</sup>   | Notes                                                                                                                   |
| --------------- | ------------------ | ------------ | ------------------- | ------------------ | --------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Qwen3.6-35B-A3B | IQ4_XS MTP         | 18 GB        | 256k q8/q8          | 7.4 GB<sup>4</sup> | 458 tok/s<sup>4</sup> | 57 tok/s<sup>4</sup> | best quality that performs well; the daily driver                                                                       |
| Qwen3.6-27B     | Q4_K_M MTP         | 18 GB        | 256k q8/q8          | ~28 GB             |                       | 2 tok/s              | doesn't fit                                                                                                             |
| Qwen3.5-9B      | Q4_K_M MTP         | 6.4 GB       | 64k q8/q8           | 8.2 GB             |                       | 163 tok/s            | limited context in VRAM                                                                                                 |
| Ornith-1.0-35B  | APEX I-Compact MTP | 17 GB        | 256k q8/q8          | 7.5 GB<sup>4</sup> | 458 tok/s<sup>4</sup> | 55 tok/s<sup>4</sup> |                                                                                                                         |
| Ornith-1.0-9B   | Q4_K_M MTP         | 5.4 GB       | 64k q8/q8           | 7.9 GB             | 2,634 tok/s           | 165 tok/s            | limited context in VRAM                                                                                                 |
| Gemma4-E2B      | QAT MTP            | 3.5 GB       | 128k q8/q8          | 3.8 GB             | 5,958 tok/s           | 279 tok/s            |                                                                                                                         |
| Gemma4-E4B      | QAT                | 5.0 GB       | 128k q8/q8          | 5.8 GB             |                       | 143 tok/s            | [MTP doesn't support quantized V-cache](https://huggingface.co/unsloth/gemma-4-E4B-it-qat-GGUF/blob/main/MTP/README.md) |
|                 | QAT MTP            | 5.0 GB       | 64k f16/f16         | 7.2 GB             | 2,786 tok/s           | 216 tok/s            | full unquantized context doesn't fit                                                                                    |
| Gemma4-12B      | QAT MTP            | 6.7 GB       | 256k q8/q8          | 8.0 GB             |                       | 19 tok/s             | full context in host RAM                                                                                                |
|                 | QAT MTP            | 6.7 GB       | 32k q8/q8           | 8.2 GB             |                       | 104 tok/s            | limited context in VRAM                                                                                                 |
| Gemma4-26B-A4B  | QAT MTP            | 15 GB        | 256k q8/q8          | 8.2 GB<sup>4</sup> | <sup>4</sup>          | 32 tok/s<sup>4</sup> |                                                                                                                         |
| Gemma4-31B      | QAT MTP            | 18 GB        | 256k q8/q8          | ~31 GB             |                       | 2 tok/s              | doesn't fit                                                                                                             |
| LFM2.5-230M     | Q4_K_M             | 147 MB       | 32k q8/q8           | 712 MB             | 42,495 tok/s          | 700 tok/s            | for smoke testing purposes                                                                                              |
| LFM2.5-8B-A1B   | APEX I-Quality     | 5.7 GB       | 128k q8/q8          | 7.3 GB             | 9,444 tok/s           | 256 tok/s            |                                                                                                                         |
|                 | APEX I-Quality     | 5.7 GB       | 128k q8/q8          | 2.1 GB             | 1,104 tok/s           | 37 tok/s             | cpu-moe; best model that fits on 4GB VRAM desktops                                                                      |

**Notes:**

- <sup>1</sup>[Turboquant](https://github.com/TheTom/llama-cpp-turboquant) cache compression is not
  available in the main branch and not necessary with the above models/VRAM
  configuration. [turbo4 is not particularly better than q4_0](perplexity/README.md).
  You can enable Turboquant by uncommenting it in
  `pixi-recipes/llama-cpp-source/*/recipe.yaml`.
- <sup>2</sup>Process total measured by nvidia-smi. When sizing video card VRAM, you
  must add ~2 GiB for your desktop (unless you're running on an integrated video card
  and your discrete card is detached from the X server)
- <sup>3</sup> Speed measured on the RTX 3080
- <sup>4</sup> Experts offloaded to host RAM. Speed is capped by PCIe bandwidth for prefill and by host RAM bandwidth for decode.

### Estimating model size and VRAM

`gguf-meta-extract` inspects a GGUF model on Hugging Face **without downloading it**: it
fetches only the header (a few MB, never the multi-GB tensor payload), parses the tensor
table, and writes a per-tensor CSV file (layer, tensor_name, geometry, n_points, quant,
bytes_per_point, total_bytes). It also prints a summary to stderr:

- model weights split into dense vs. routed-expert (`*_exps.*`) bytes, plus the fraction
  of expert weight actually activated per token on MoE models;
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
| [pi-subagents](https://github.com/nicobailon/pi-subagents)                           | spawn sub-agents for complex tasks                   |
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
pi --with-herdr                       # Let the agent drive the herdr it runs inside (SANDBOX ESCAPE — see below)
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
claude --with-herdr                   # Let the agent drive the herdr it runs inside (SANDBOX ESCAPE — see below)
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

### herdr integration in pi and claude

pi and claude know how to control the herdr they run inside of. This requires the
`--with-herdr` flag, which binds herdr's control socket into the sandbox. Try:

```bash
herdr     # or pixi r herdr
```

Then from the terminal inside herdr:

```bash
pi --with-herdr "split panes and echo 'hello world' in the new pane"
```

### ⚠️ Security: `--with-herdr` is a sandbox escape ⚠️

`~/.config/herdr/herdr.sock` is herdr's full-control JSON-RPC socket, and herdr itself
runs **outside** the sandbox. An agent that can reach the socket can ask herdr to spawn
a new **host-side** (unsandboxed) pane and run arbitrary commands in it — i.e. full
access to your machine. This means it's still harder to break things _from the main pane_,
but anything on a separate pane has unlimited access.

The socket is therefore **not** bound by default. Only pass `--with-herdr` when you
would also be comfortable running the agent with `--no-sandbox`. Without the flag,
the herdr skill detects the missing socket and the agent cannot drive herdr.

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
- _"Update everything"_ (llama.cpp recipes, pi-extensions, Claude Code, herdr, pixi environments)

## Missing features

Only `linux-64`, `linux-aarch64`, and `win-64` are configured. There are no macOS or
Windows CUDA builds simply because I have no hardware to test them on. There are no
Windows source builds because the OS defeated me. PRs welcome if you can validate them.
