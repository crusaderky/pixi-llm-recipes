# context-bench — long-context extractive needle recall benchmark

Measures how well a model recalls discrete facts scattered through a long
context, and how (or whether) that degrades under pressure such as a quantized
KV cache.

## What's here

| File                             | Purpose                                                                                              |
| -------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `run_benchmark.py`               | Runner: prompts each model, grades answers, writes a TOML report                                     |
| `aggregate_benchmark_results.py` | Aggregates several result TOMLs into a CSV                                                           |
| `config.toml`                    | Runner config — one table per model endpoint, plus a `[*]` defaults table                            |
| `AGENTS.md`                      | System prompt handed to the model under test                                                         |
| `<size>.txt`                     | Books (`16k`–`256k`) — public-domain text sized to fill the named window, with 20 questions appended |
| `<size>.answers.txt`             | Reference answers `A1`–`A20`, each with source line number(s) in `[brackets]`                        |

The books are deliberately obscure, recently-digitised Project Gutenberg titles
so that answers must come from the supplied context, not the model's training
data. Each is sized to fill \~70–90% of its named window once the questions are
appended.

## How it works

For each model and each book size, the runner sends `AGENTS.md` as the system
prompt and the book (text + 20 questions) as the user message, over the
OpenAI-compatible API. It collects the 20 answers and grades them with
normalized string matching (case-insensitive; ignores articles, currency
symbols, separators and spacing; maps number-words to digits, `nine`→`9`).

Each answer is scored `PASS` / `NO ANSWER` / `WRONG`, and the run's
`grade = (#PASS − #WRONG) / 20` lands in `[−1, 1]`. Grading is deterministic, so
a correct-but-off-format answer can score `WRONG`; the verbatim `raw_answers`
are preserved in the report for inspection.

```bash
# llama-server must be running on :8080 (see ../../models.ini)
pixi run context-bench sample-data/context-bench/config.toml -o results.toml
```

## Interpreting the results — read this before drawing conclusions

This benchmark is a blunt instrument by design, and several effects make it easy
to over-read. The nuances below all came out of trying to use it to detect
KV-cache quantization harm.

### 1. This is not perplexity, and it should not behave like perplexity

The most common reason to run this is to ask "does quantizing the KV cache
(e.g. `cache-type-k/v = q4_0`) hurt long-context performance?" The internet — and
papers measuring schemes like Turboquant's `turbo4` — will tell you q4 keys are
catastrophic. Those measurements are almost always **perplexity**.

Perplexity and this benchmark measure different things:

- **Perplexity** is the geometric mean of the inverse probability assigned to
  _every_ token of a held-out corpus. It is sensitive to _small_ shifts across
  the _entire_ next-token distribution, including the tail. It is, by design, a
  maximally sensitive detector of distributional perturbation — which is exactly
  why KV-cache quantization moves it.
- **This benchmark** is a thresholded, argmax-style task: retrieve \~20 discrete
  facts and emit them parseably. The model only needs its attention to land on
  roughly the right region and copy a token. A small logit wobble doesn't change
  the answer unless it flips the argmax. The noise perplexity amplifies is
  precisely the noise this grader rounds away.

So a quantization scheme can measurably worsen perplexity while showing **no**
effect here. That is the expected, well-documented relationship between
quantization, perplexity, and downstream task accuracy — not a contradiction.
Don't use this benchmark to refute a perplexity result, or vice versa; they
answer different questions.

Also note that **extractive needle recall is the easiest case for quantized
attention.** Rare, distinctive needle tokens (a proper name, a number) produce
sharp, high-magnitude attention scores that survive coarse quantization.
KV quant bites hardest on tasks that _aggregate over many similarly-weighted
tokens_ (counting, summarization, multi-hop reasoning where errors compound).
A near-zero result here does **not** license "q4 KV is fine for everything."

### 2. The variance is large

Grades here are noisy. With 20 questions and only a handful of runs per cell,
the per-cell standard error is roughly ±5–15 points — you cannot resolve a 2–3
point effect by eyeballing averages. Symptoms in real data:

- Grades are **non-monotonic** in context length (a model can score lower at
  32k than at 64k) — that's run-to-run noise and book-specific difficulty, not a
  clean "recall decays with length" signal.
- A **model-collapse** run (the inner monologue falls into a repetition loop and
  never emits answers) produces a grade of 0 in `run_benchmark.py`, but is then
  filtered out by the aggregation script as it is not a meaningful measure of
  the health of the context.
- When an apparent q4-vs-q8 difference **flips sign** across cells, you're
  measuring noise centered on zero, not a quantization effect.

### 3. Sampling settings: reproducibility vs. quality

Both Gemma and Qwen are reasoning models, and their authors warn against **greedy
decoding** (`temperature = 0`): Qwen explicitly says it causes "performance degradation
and endless repetitions", and Gemma/Unsloth recommend `temperature = 1.0`. Greedy
decoding is prone to the classic neural-text-degeneration repetition loop — i.e. it can
_increase_ the model-collapse events that already dominate the variance. In llama.cpp,
`temperature ≤ 0` also short-circuits most of the sampler chain, disabling the `min-p`
and `dry-*` anti-repetition samplers configured in `models.ini` precisely to prevent
those loops. **Don't reach for `temperature=0` to make runs reproducible.**

Instead, **pin the `seed`** (a per-request sampling parameter — see below).
A fixed seed cuts run-to-run sampling jitter while keeping the model on its
recommended sampling distribution. Caveats:

- Same seed across the q4/q8 arms does **not** produce identical output — the
  cache dtype changes the _logits_ the RNG draws act on. That's exactly what you
  want: in a paired run the only difference between arms is the quantization's
  effect on the distribution.
- A fixed seed makes sampling reproducible _given identical logits_, but
  llama.cpp logits aren't bit-reproducible across runs (floating-point
  non-associativity, continuous batching, GPU kernel non-determinism). The seed
  tightens variance; it doesn't zero it. Still run multiple repetitions and
  report the median.

## Config reference

One TOML table per model ("model tag"); an optional `[*]` table supplies
defaults merged into every model (per-model values win). Only `ctx-size` is
required.

```toml
[*]
url = "http://localhost:8080/v1"   # OpenAI endpoint; default localhost:8080
# seed = 42                        # optional; default: server picks a random seed per request
max_tokens = 9000                  # cap total decode tokens (reasoning + output)

[my-model]
ctx-size = ["16k", "64k", "256k"]  # required; books to run (int, or k/M suffix)
# model_name = "..."               # optional; defaults to the table name
# api_key = "sk-..."               # optional literal key
# api_key_env = "OPENAI_API_KEY"   # optional; name of env var holding the key
# temperature = 0.6
# timeout = 1800                   # request timeout in seconds
```

Run `python run_benchmark.py --help` for the full docstring.

## Benchmark results

Results are with `models.ini` as of 2026-06-18; it may have changed since then.

### Findings

- All results below are with `--cache-type-k q8_0 --cache-type-v q8_0`. The test was
  rerun with `--cache-type-k q4_0 --cache-type-v q4_0`, which produced **no degradation
  above noise levels**. Please read above in this document to understand why this does
  not mean you should just use q4/q4 in production!
- **Qwen3.6-35B-A3B achieved near-perfect scores even at 256k context**. Its token usage
  grows mildly with context size and is very predictable throughout the board. I
  would trust it to extract data from documents of any size (but note that it still
  hallucinates 3% of the time).
- **Performance of all Gemma models falls down a cliff past 32k context**, with
  hallucinations shooting up in the 25% territory. This is likely due to their
  [sliding window](https://ai.google.dev/gemma/docs/core/model_card_4#models_overview)
  attention design.
- **Gemma 4 E2B and E4B proved competent** for their size - at least up to 32k context.
  32k context converts to 22\~25k words in English; a research paper is typically 4k\~9k
  words. I would _cautiously_ use these models to extract data from anything shorter
  than a novel, if I could not afford to run Qwen (e.g. I need to run on a phone).
  Emphasis on _cautiously_ - these models were still **confidently wrong 15% of the
  times**.
- **Gemma 4 12B and 26B A4B proved astonishingly poor** for their size - somewhat better
  grades than their smaller siblings, but unusable in practice. They routinely went into
  infinite thought loops, which were rescued by the `reasoning-budget = 8192` plus
  `reasoning-budget-message` settings in `models.ini`. Even when rescued, lots of time
  was wasted reaching the ceiling of the reasoning budget, making them effectively
  vastly slower than the other models. Despite the reasoning budget cap, Gemma 4 12B and
  26B A4B routinely went into complete model collapse, often repeating incoherent output
  to infinity, and were hard-terminated by `max_tokens = 9000` in `config.toml`. No
  amount of tweaking to their parameters fixed this behaviour. Note that **model collapse
  produces survivor bias in the other scores of the model**.

  [forge](https://github.com/antoinezambelli/forge) may be the solution to always get a
  response (untested) - however it would not prevent the model from burning through 9k
  tokens two or three times to reach the response.

### Grading

`grade` is calculated as follows:

- pass (correct answer) = +1
- no answer = 0
- wrong (hallucinated answer) = -1

Repeat the test 5 times, sum everything up, and normalize to obtain grade in the
`[-100%, +100%]` range.

"Model collapse" is when the model went into an infinite loop and did not produce any
answers. It is not accounted for in the normalization of `grade`, `pass`, `no answer`,
and `wrong`.

### Detailed results

| model           | context | runs | grade | pass  | no answer | wrong | tokens/prompt | exceeded reasoning budget | model collapse |
| --------------- | ------- | ---- | ----- | ----- | --------- | ----- | ------------- | ------------------------- | -------------- |
| Qwen3.6-35B-A3B | 16k     | 5    | 96%   | 98%   | 0%        | 2%    | 2.3k ± 0.2k   | 0%                        | 0%             |
|                 | 32k     | 5    | 100%  | 100%  | 0%        | 0%    | 2.8k ± 0.3k   | 0%                        | 0%             |
|                 | 64k     | 5    | 100%  | 100%  | 0%        | 0%    | 3.1k ± 0.3k   | 0%                        | 0%             |
|                 | 128k    | 5    | 100%  | 100%  | 0%        | 0%    | 3.3k ± 0.4k   | 0%                        | 0%             |
|                 | 256k    | 5    | 94%   | 97%   | 0%        | 3%    | 3.1k ± 0.7k   | 0%                        | 0%             |
| Gemma4-E2B      | 16k     | 5    | 60%   | 75%   | 10%       | 15%   | 1.9k ± 0.5k   | 0%                        | 0%             |
|                 | 32k     | 5    | 72%   | 86%   | 0%        | 14%   | 2.0k ± 0.2k   | 0%                        | 0%             |
|                 | 64k     | 5    | 35%   | 67%   | 1%        | 32%   | 2.7k ± 0.4k   | 0%                        | 0%             |
|                 | 128k    | 5    | 10%   | 41%   | 28%       | 31%   | 2.7k ± 0.8k   | 0%                        | 0%             |
| Gemma4-E4B      | 16k     | 5    | 83%   | 88%   | 7%        | 5%    | 2.1k ± 0.4k   | 0%                        | 0%             |
|                 | 32k     | 5    | 72%   | 86%   | 0%        | 14%   | 1.9k ± 0.5k   | 0%                        | 0%             |
|                 | 64k     | 5    | 10%   | 52%   | 6%        | 42%   | 2.9k ± 1.5k   | 0%                        | 0%             |
|                 | 128k    | 5    | 27%   | 52%   | 23%       | 25%   | 3.1k ± 1.0k   | 0%                        | 0%             |
| Gemma4-12B      | 16k     | 5    | 91%   | 95%   | 1%        | 4%    | 5.2k ± 1.2k   | 0%                        | 0%             |
|                 | 32k     | 5    | 92%   | 96%   | 0%        | 4%    | 4.8k ± 1.6k   | 0%                        | 0%             |
|                 | 64k     | 5    | 58%   | 79%   | 0%        | 21%   | 8.4k ± 0.0k   | 100%                      | 0%             |
|                 | 128k    | 5    | 78%   | 87%   | 4%        | 9%    | 8.2k ± 0.4k   | 80%                       | 0%             |
|                 | 256k    | 5    | 82%   | 91%   | 0%        | 9%    | 5.5k ± 3.2k   | 20%                       | 0%             |
| Gemma4-26B-A4B  | 16k     | 5    | 99%   | 99%   | 1%        | 0%    | 5.3k ± 1.8k   | 20%                       | 0%             |
|                 | 32k     | 5    | 100%* | 100%* | 0%*       | 0%*   | 3.1k ± 1.3k   | 0%                        | 40%            |
|                 | 64k     | 5    | 45%*  | 70%*  | 5%*       | 25%*  | 8.4k ± 0.0k   | 100%                      | 60%            |
|                 | 128k    | 5    | 44%   | 66    | 12%       | 22%   | 7.0k ± 2.7k   | 80%                       | 0%             |
|                 | 256k    | 5    | 66%*  | 74%*  | 19*       | 8%*   | 4.7k ± 2.9k   | 20%                       | 20%            |

\* Runs that suffered from model collapse were disregarded.
