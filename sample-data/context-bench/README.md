# context-bench — long-context extractive needle recall benchmark

Measures how well a model recalls discrete facts scattered through a long
context, and how (or whether) that degrades under pressure such as a quantized
KV cache.

## What's here

| File | Purpose |
|------|---------|
| `run_benchmark.py` | Runner: prompts each model, grades answers, writes a TOML report |
| `aggregate_benchmark_results.py` | Aggregates several result TOMLs into a CSV |
| `config.toml` | Runner config — one table per model endpoint, plus a `[*]` defaults table |
| `AGENTS.md` | System prompt handed to the model under test |
| `<size>.txt` | Books (`16k`–`256k`) — public-domain text sized to fill the named window, with 20 questions appended |
| `<size>.answers.txt` | Reference answers `A1`–`A20`, each with source line number(s) in `[brackets]` |

The books are deliberately obscure, recently-digitised Project Gutenberg titles
so that answers must come from the supplied context, not the model's training
data. Each is sized to fill ~70–90% of its named window once the questions are
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
papers measuring schemes like turboquant's `turbo4` — will tell you q4 keys are
catastrophic. Those measurements are almost always **perplexity**.

Perplexity and this benchmark measure different things:

- **Perplexity** is the geometric mean of the inverse probability assigned to
  *every* token of a held-out corpus. It is sensitive to *small* shifts across
  the *entire* next-token distribution, including the tail. It is, by design, a
  maximally sensitive detector of distributional perturbation — which is exactly
  why KV-cache quantization moves it.
- **This benchmark** is a thresholded, argmax-style task: retrieve ~20 discrete
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
KV quant bites hardest on tasks that *aggregate over many similarly-weighted
tokens* (counting, summarization, multi-hop reasoning where errors compound).
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
*increase* the model-collapse events that already dominate the variance. In llama.cpp,
`temperature ≤ 0` also short-circuits most of the sampler chain, disabling the `min-p`
and `dry-*` anti-repetition samplers configured in `models.ini` precisely to prevent
those loops. **Don't reach for `temperature=0` to make runs reproducible.**

Instead, **pin the `seed`** (a per-request sampling parameter — see below).
A fixed seed cuts run-to-run sampling jitter while keeping the model on its
recommended sampling distribution. Caveats:

- Same seed across the q4/q8 arms does **not** produce identical output — the
  cache dtype changes the *logits* the RNG draws act on. That's exactly what you
  want: in a paired run the only difference between arms is the quantization's
  effect on the distribution.
- A fixed seed makes sampling reproducible *given identical logits*, but
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
