# KV cache quantization quality

KL-divergence sweeps over every K-quant × V-quant combination, measured against an
f16/f16 baseline with `perplexity` and rendered with `perplexity-report` (see the
[benchmarking section](../README.md#model-and-kv-cache-quantization-quality) of the main
README).
Lower KLD = closer to full-precision output. "Size" is bytes/parameter of the KV cache
(K + V); the `f16/f16` baseline is 4.0.

| Model | Markdown | Interactive | Plot |
| --- | --- | --- | --- |
| Qwen3.6-35B-A3B | [Static report](KV-KLD.Qwen36-35B-A3B.md) | [Interactive HTML](https://htmlpreview.github.io/?https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/main/perplexity/KV-KLD.Qwen36-35B-A3B.html) | [SVG](https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/refs/heads/main/perplexity/KV-KLD.Qwen36-35B-A3B.svg) |
| Gemma4-E2B QAT | [Static report](KV-KLD.Gemma4-E2B.md) | [Interactive HTML](https://htmlpreview.github.io/?https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/main/perplexity/KV-KLD.Gemma4-E2B.html) | [SVG](https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/refs/heads/main/perplexity/KV-KLD.Gemma4-E2B.svg) |
| Ternary-Bonsai-27B | [Static report](KV-KLD.Ternary-Bonsai-27B.md) | [Interactive HTML](https://htmlpreview.github.io/?https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/main/perplexity/KV-KLD.Ternary-Bonsai-27B.html) | [SVG](https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/refs/heads/main/perplexity/KV-KLD.Ternary-Bonsai-27B.svg) |

## Headline numbers (mean KLD, symmetric K=V)

| K/V | Size | Qwen3.6 | Gemma4-E2B | Ternary-Bonsai-27B |
| --- | --- | --- | --- | --- |
| f16/f16 | 4.00 | 0 | 0 | 0 |
| q8_0/q8_0 | 2.12 | 0.0052 | 0.0028 | 0.0003 |
| q4_0/q4_0 | 1.12 | 0.0130 | 0.0504 | 0.0027 |
| turbo4/turbo4 | 1.06 | 0.0157 | 0.0635 | n/a |

## Findings

1. **q8/q8 is nearly free** on all models — negligible KLD for half the cache size. It's
   the default in [`models.ini`](../models.ini) and a safe choice everywhere.

2. **turbo4 is *not* "almost q8".** Internet wisdom oversells it. On both models a true
   symmetric `turbo4/turbo4` is 3× (Qwen) to ~23× (Gemma) worse than `q8_0/q8_0`, and is
   even slightly *worse* than plain `q4_0/q4_0` for marginally less space. `q8_0/turbo4`
   is pretty much the same as `q8_0/q4_0` (although faster). turbo's value is in the
   lower bitrates (turbo3/turbo2), not as a q8 substitute.

3. **K is *typically* more sensitive than V, but sometimes less.** Quantizing the K
   cache most times sometimes hurts more than quantizing V, but in some cases it is the
   other way around.

   ⚠️ The turboquant fork **silently upgrades a turbo K cache to q8_0** on high-GQA
   models ("auto-asymmetric"), so a requested `turbo4/turbo4` actually runs as
   `q8_0/turbo4` with just a warning in the llama.cpp log. These sweeps set
   `TURBO_AUTO_ASYMMETRIC=0` (in `pixi.toml`) so the numbers reflect what was actually
   requested.

4. **q4/q4 is great on Ternary Bonsai, fine on Qwen, and disastrous on Gemma.**
   Dropping from q8/q8 to q4/q4 halves the
   cache; on Qwen mean KLD only ~2.5×'s (0.0052 → 0.0130), but on Gemma it explodes ~18×
   (0.0028 → 0.0504, with the 99.9% tail going 0.11 → 1.37). Gemma's tiny KV-head count
   makes it far less tolerant of cache quantization.

5. **Speed is noisy and combo-dependent.** some K/V quant combos lack a fused
   flash-attention kernel and run anywhere between 2× and 20x slower than `q8/q8`. This
   data is noisy so you see a 20% degradation you should run a separate `llama-bench`
   run.

6. 99.9% KLD is mostly parallel with mean KLD in both cases, which makes it quite
   redundant.

## Important caveats

1. These benchmarks are for a *very specific* Gemma model, E2B QAT, and at most one
   could expect similar results on E4B QAT. "Gemma" is just a commercial name: regular
   and QAT models are completely distinct; it's been reported that regular models
   tolerate KV cache quantization worse than QAT (*unconfirmed*). Gemma4 31B has very
   little in common with E2B. If you're arrived here trying to figure out whether you
   can run Gemma4 31B on 24GB VRAM, the answer is: (a) do not trust these numbers, (b)
   **do not trust people on the internet**, (c) test yourself. It's fast and cheap.
2. Likewise you should not assume how Qwen3.6 27B reacts to quantizastion.
3. KLD results from the interaction of the model with the algorithm used to quantize the
   cache. *Speed*, on the other hand, is all about the implementation in the inference
   engine, so future versions of llama.cpp could bring more quants to speed parity with
   `q8_0/q8_0`.

## Methodology

Qwen and Gemma measures were acquired with the project as of 2026-06-22:

- `TheTom/llama-cpp-turboquant` tag `feature-turboquant-kv-cache-b9905-4595fff`
  (last sync with main as of `b8533`)
- `byteshape/Qwen3.6-35B-A3B-MTP-GGUF:Qwen3.6-35B-A3B-IQ4_XS-3.97bpw`
- `unsloth/gemma-4-E2B-it-qat-GGUF:UD-Q4_K_XL`

You can run your own with `pixi r perplexity` and `pixi r perplexity-report`.
