# LFM3-1.7B quantization report

This report measures various quants of [LFM3-1.7B](https://www.liquid.ai/blog/lfm3), together with KV cache quantization, to determine the least possible degradation at each size in VRAM.

## Highlights

- **This model fits in 3.6 GiB VRAM** (1.4 GiB weights + 1.1 GiB context + 1.1 GiB
  scratch buffers), with 256k context and no discernible degradation from the
  unquantized version.
- **It can be crammed down to 2.2 GiB VRAM** with contained degradation and a 128k
  context. Beyond that, quality falls off a cliff.
- I tested quants from [LiquidAI](https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF),
  [Bartowski](https://huggingface.co/bartowski/LFM3-1.7B-GGUF),
  [AtomicChat](https://huggingface.co/AtomicChat/LFM3-1.7B-GGUF) and
  [NANI-Nithin](https://huggingface.co/NANI-Nithin/LFM3-1.7B-GGUF). Bartowski turned out
  to be better, at all rungs, than the other quants.
- LiquidAI's QAD-Q4_0 quant shows metrics that are strictly worse than Bartowski's
  Q4_K_S, which in turn is already off the edge of the quality cliff.
- KV cache quantisation resists nicely until `k=q5_0 v=q4_0`, after which it falls off a
  cliff.
- [BeeLLaMA.cpp](https://github.com/Anbeeld/beellama.cpp) allows pushing it further to
  `k=q4_0 v=q3_0`, but only after enabling a generous exact tail (`--kv-tail-tokens
  1024`). This suffers from a worst-case scenario, not measurable by KLD, where one
  needs to retrieve important information that's older than the latest tokens. It also
  suffers from very substantial performance degradation (3x slowdown on prefill on CUDA
  RTX 3090). KVarN is not supported due to the model's geometry.

## Best weights/KV cache quants

| Rung | KV (bpw) | Weights (MiB) | KV cache (MiB) @256k | VRAM (MiB) @256k<sup>1</sup> | Mean KLD | 99.9% KLD | Top-1 (%) | Same sampled (%) | Perplexity (%) |
|---|---|---|---|---|---|---|---|---|---|
| LiquidAI:BF16\|f16 | 32.00 | 3,306 | 3,075 | 6,381 | 0.000000 | 0.000048 | 99.993 ± 0.003 | 36.390 ± 0.111 | 99.98 |
| LiquidAI:Q8_0\|q8_0 | 17.00 | 1,761 | 1,634 | 3,395 | 0.006607 | 0.746117 | 96.855 ± 0.061 | 36.381 ± 0.111 | 99.85 |
| bartowski:Q6_K_S\|q8_0/q5_0 | 14.00 | 1,389 | 1,346 | 2,735 | 0.023014 | 2.702538 | 93.921 ± 0.083 | 36.368 ± 0.111 | 99.55 |
| bartowski:Q6_K_S\|q6_0<sup>2</sup> | 13.00 | 1,389 | 1,250 | 2,639 | 0.019834 | 2.143442 | 94.378 ± 0.080 | 36.352 ± 0.111 | 99.63 |
| bartowski:Q6_K_S\|q6_0/q5_0<sup>2</sup> | 12.00 | 1,389 | 1,154 | 2,543 | 0.023287 | 2.678904 | 93.938 ± 0.083 | 36.338 ± 0.111 | 99.55 |
| bartowski:Q6_K_S\|q5_0 | 11.00 | 1,389 | 1,057 | 2,447 | 0.032119 | 3.286549 | 92.730 ± 0.091 | 36.275 ± 0.111 | 99.41 |
| bartowski:Q6_K_S\|q5_0/q4_0 | 10.00 | 1,389 | 961 | 2,351 | 0.040071 | 3.511644 | 91.779 ± 0.096 | 36.191 ± 0.111 | 99.28 |
| bartowski:Q5_K_M\|q5_0/q4_0 | 10.00 | 1,290 | 961 | 2,251 | 0.051227 | 3.926834 | 90.226 ± 0.104 | 35.980 ± 0.111 | 99.09 |
| bartowski:Q5_K_S\|q5_0/q4_0 | 10.00 | 1,209 | 961 | 2,171 | 0.065467 | 4.323977 | 88.816 ± 0.110 | 35.774 ± 0.111 | 98.85 |
| bartowski:Q4_K_M\|q5_0/q4_0 | 10.00 | 1,064 | 961 | 2,025 | 0.102118 | 6.292465 | 85.962 ± 0.121 | 35.692 ± 0.111 | 98.25 |
| bartowski:Q4_K_M\|q4_0/q3_0 t1024<sup>2</sup> | 8.00 | 1,064 | 783 | 1,847 | 0.105738 | 6.428101 | 85.724 ± 0.122 | 35.666 ± 0.111 | 98.16 |

<sup>1</sup>Does not include scratch buffers

<sup>2</sup>Requires [BeeLLaMA.cpp](https://github.com/Anbeeld/beellama.cpp)

## Plots

[Click here for an interactive HTML
report](https://htmlpreview.github.io/?https://raw.githubusercontent.com/crusaderky/pixi-llm-recipes/main/perplexity/LFM3-1.7B/report.html)
with the full results. The plots below only highlight the best quants for the sake of
readability.

The Pareto frontier highlights the lowest Mean KLD (across all plots). A few observations:

- all measures are affected by noise, whether they have error bars or not;
- highlighting the Mean KLD frontier on other plots highlights non-linearity on other measures, e.g. 99% KLD;
- The Pareto frontier runs lower than the cherry-picked "best" quants. This is because
  it selects the points with an exact tail (`--kv-tail-tokens 1024`), which however are
  not recommended except in extreme cases, due to poor performance, as explained  above.

![Mean KLD](report.mean-kld.png)

![99.9% KLD](report.p999-kld.png)

![Top-1 (%)](report.top1.png)

**Same sampled token** is my favourite measure. It is from [Quesma's
blog](https://quesma.com/blog/qwen-quantization-quality/), which is conceptually similar
to Top-1, but at temperature=1 instead of temperature=0. It shows a much steeper cliff
and incorporates behaviour seen in both the Mean KLD and the 99% KLD plots.

![Same sampled (%)](report.same-sampled.png)

![Cor(ln(PPL(Q)), ln(PPL(base)))](report.ppl.png)

## Presets

Indistinguishable from the unquantized model; 3.6 GiB VRAM:
```ini

version = 1

[*]
jinja = true
parallel = 1

[LFM3-1.7B]
hf = bartowski/LFM3-1.7B-GGUF:Q6_K_S

flash-attn = on
kv-unified = true
ctx-size = 262144
cache-type-k = q6_0  # q8_0 if BeeLLaMA.cpp is not an option
cache-type-v = q5_0

temperature = 0.1
top-k = 50
repeat-penalty = 1.1
```

Bare minimum quality before the cliff edge, half context; 2.2 GiB VRAM (slower; requires
BeeLLaMA.cpp):

```ini

version = 1

[*]
jinja = true
parallel = 1

[LFM3-1.7B]
hf = bartowski/LFM3-1.7B-GGUF:Q4_K_M

flash-attn = on
kv-unified = true
ctx-size = 131072
cache-type-k = q4_0
cache-type-v = q3_0
kv-tail-tokens = 1024

temperature = 0.1
top-k = 50
repeat-penalty = 1.1
```
