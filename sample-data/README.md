# Sample data

## wiki.test.raw

[Standard benchmark text](https://huggingface.co/datasets/ggml-org/ci/resolve/main/wikitext-2-raw-v1.zip)

- ~1.3m characters
- ~295k tokens
- ~600 llama-perplexity chunks @ `-c 512`

## wiki.train.head-10k.raw

`wiki.train.raw` from the same dataset linked above, first 10k lines

- ~3m characters
- ~674k tokens
- ~20 llama-perplexity chunks @ `-c 32768`

## describe-me.jpg

An arbitrary image to test multi-modal capabilities
