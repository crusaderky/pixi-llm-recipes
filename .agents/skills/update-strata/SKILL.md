---
name: update-strata
description: Update the strata conda recipe to the latest Strata release — bump the `version` pin, re-sync the llama.cpp commit its engine builds against, refresh the run requirements from the tagged setup.py, rebuild the env and smoke-test the server. Use when asked to "update strata", "bump strata", or "update the strata recipe".
compatibility: Requires network access to github.com (git ls-remote, gh api) and an NVIDIA host with conda's CUDA 13.1 for the rebuild. Designed for the pixi-llm-recipes project.
allowed-tools: Bash Read Edit
---

`pixi-recipes/strata/` packages the Strata app tree **and** compiles its C++/CUDA engine, so a
version bump is not just a string: the engine is rebuilt for that tag, and the llama.cpp commit
the recipe vendors must move with it.

## What a bump touches

| pin                  | where                                           | new value from                                                        |
| -------------------- | ----------------------------------------------- | --------------------------------------------------------------------- |
| `version`            | `context:` in `pixi-recipes/strata/recipe.yaml` | the release tag, without the `v` (`source:` builds `v${{ version }}`) |
| `llama_cpp_commit`   | same `context:` block                           | `LLAMA_CPP_COMMIT` in that tag's `setup.py`                           |
| `requirements.run`   | same `recipe.yaml`                              | `PY_PACKAGES` + `requirements.txt` of that tag's `setup.py`           |
| `cuda_architectures` | same `context:` block                           | **never** (`86` = RTX 30 series)                                      |

Hugging Face pins are **not** ours: `HF_REVISIONS` (`setup.py`) and `PINNED_REVISION`
(`tools/mtp_fetch.py`) live upstream and `scripts/strata-run.py` reads them out of the installed
`setup.py` at run time, so a revision bump needs no edit here. It does mean the first start after
such a bump fetches that revision's files into `~/.cache/huggingface/hub` again (the old
revision's blobs stay until `hf cache delete`).

**Guardrails — violating any item means STOP, not update:**

> ⚠ **Tags only, from `refs/tags/`.** Never a branch, never a commit SHA, never a tag guessed from
> a release title. The candidate must be strictly newer than the current pin (never a downgrade),
> and `prerelease`/`draft` releases are skipped unless the current pin is itself one.

> ⚠ **`llama_cpp_commit` is upstream's own value, copied verbatim.** Not a commit of your
> choosing, not the fork the llama-cpp recipes pin. The engine links that ggml and the runtime
> tools (`iq_pack.py`, `pack_index.py`, the tokenizer builder) read GGUFs through that commit's
> `gguf-py`.

> ⚠ **A version bump changes pins, not code.** Do not touch `build.sh`, `scripts/strata-run.py`
> or the lifecycle scripts unless Phase 5 reports a real interface break — and if it does, report
> it before rewriting anything.

## Phase 1 — the target version

```bash
git ls-remote --tags --refs https://github.com/Niko1221/Strata.git \
  | awk '{sub("^refs/tags/", "", $2); print $2}' | sort -V | tail -8
gh api repos/Niko1221/Strata/releases --jq '.[0:6] | .[] | "\(.tag_name) prerelease=\(.prerelease) \(.published_at)"'
```

Read the release bodies of **every** version between the current pin and the target — that is
where a pack-format change ("re-prepare"), a new engine argument or a new `.venv` requirement is
announced. Phase 4 needs that answer.

## Phase 2 — read the new tag

```bash
V=v0.1.40   # the validated tag
gh api "repos/Niko1221/Strata/contents/setup.py?ref=$V" --jq .content | base64 -d > /tmp/strata-setup.py
gh api "repos/Niko1221/Strata/contents/requirements.txt?ref=$V" --jq .content | base64 -d > /tmp/strata-req.txt
grep -n "LLAMA_CPP_COMMIT\|^PY_PACKAGES" /tmp/strata-setup.py; cat /tmp/strata-req.txt
```

Diff the package names against the recipe's `requirements.run`. If a name is missing there, add
it — these are the packages `scripts/strata-run.py` checks for instead of letting setup.py pip
into the prefix. Requirements carrying a marker that does not apply on Linux (e.g.
`colorama; sys_platform == "win32"`) need **no** conda counterpart: the wrapper evaluates markers
before checking, so it never reports them missing.

## Phase 3 — edit the recipe

`version:` (no `v`), `llama_cpp_commit:`, and the `run:` list if it changed. Leave `build.number`
at `0`, and leave the `skip:` list, `dynamic_linking`, and the CUDA requirements alone.

## Phase 4 — rebuild and smoke-test

```bash
pixi install -e strata              # ~5-9 min: compiles the engine + image encoder for the new sources
pixi run -e strata strata-install   # re-prepares: re-verifies the model, rewrites the run config
pixi run -e strata start-strata     # ~40 s warm, then /health says "loaded": true
curl -s http://127.0.0.1:8080/health
pixi run -e strata stop-strata
```

- `pixi install -e strata` **is safe from the bwrap sandbox**, unlike `-e agents`: the strata
  prefix is not the bind-mounted one, so the EBUSY env-sync failure described in `update-all`
  cannot happen. The compile is the slow part; it does not touch the pack or the model.
- Run `strata-install` after a bump even though the model is already prepared: the run config
  (`$CONDA_PREFIX/opt/strata/strata-<tag>.json`) is what carries the engine arguments and the
  context size, and a plain `start-strata` reuses an existing config untouched. `strata-install`
  is also the cheapest interface test — it exercises the `FAMILIES`/`MODELS`/`--gguf-dir` paths
  in the wrapper (~2 min, no download).
- The pack (`strata-data/packs/<tag>`) and the MTP layer (`strata-data/mtp/rt`) carry **no
  version stamp**; setup.py only checks that their files exist. So if the release notes mention a
  pack or `rt` format change, or the smoke test fails, or drafting is dead (`draft_n_accepted: 0`),
  rebuild both from scratch:

  ```bash
  rm -rf "${CONDA_PREFIX}/strata-data/packs" "${CONDA_PREFIX}/strata-data/mtp"
  pixi run -e strata strata-install   # pack ~2 min; re-fetches the ~5 GB of MTP tensors
  ```

  The re-fetch is expected: `strata-run.py` trims the MTP build inputs after every successful
  prep (they are ~6 GB and never read again).
- Evidence lives in `strata.log` (repo root, the start/stop messages) and
  `$CONDA_PREFIX/opt/strata/strata-<tag>.log` (the engine's own load log — the `strata mtp: draft
  layer loaded` and `expert cache` lines).

## Phase 5 — interface checklist

An exact pin is not enough: `build.sh` and `scripts/strata-run.py` call into upstream's `setup.py`
and tools **by name**. After `pixi install -e strata`, anything printed below is a real break:

```bash
APP=.pixi/envs/strata/opt/strata
while read -r file pattern; do
  grep -q -- "$pattern" "$APP/$file" 2>/dev/null || echo "MISSING: $file <- $pattern"
done <<'EOF'
setup.py find_nvcc
setup.py cmake_build
setup.py ENGINE_SOURCES
setup.py VISION_SOURCES
setup.py source_version
setup.py source_hash
setup.py LLAMA_CPP_COMMIT
setup.py def pip_install
setup.py def req_name
setup.py def _installed
setup.py ^FAMILIES
setup.py ^MODELS
setup.py def model_file
setup.py def model_shards
setup.py STRATA_EXECV
setup.py "--gguf-dir"
setup.py "--no-start"
setup.py "--data-dir"
setup.py "--draft-vocab"
setup.py "--no-browser"
setup.py "--yes"
CMakeLists.txt STRATA_ENABLE_CUDA
tools/vision/CMakeLists.txt STRATA_VISION_CUDA
tools/mtp_fetch.py def verify
tools/mtp_pack.py --experts
tools/mtp_rt.py --gguf
serve/server.py "loaded": self.loaded()
EOF
for d in serve tools data src include engine third_party/ggml third_party/llama.cpp/gguf-py; do
  [ -e "$APP/$d" ] || echo "MISSING: $d/"
done
```

The engine binaries matter too: `engine/strata` and `engine/strata-vision` must exist and be
executable (Phase 4's smoke test proves they run — `strata-run.py` also depends on setup.py's
`EXE`/`VEXE` names matching what `build.sh` copied).

## Phase 6 — report

```
## Strata updated
- version: 0.1.39 → 0.1.40          (tag v0.1.40, confirmed in refs/tags)
- llama_cpp_commit: <old> → <new>   (from the tag's setup.py LLAMA_CPP_COMMIT)
- requirements.run: <added, or "unchanged">
- engine rebuilt for sm_86; STRATA_CUDA_ARCHITECTURES untouched
- smoke test: /health "loaded": true, decode <n> tok/s, MTP draft accepted <n>/<n>
- interface touch points: all present | MISSING <list>
- pack/MTP re-prepared: no | yes (why)
```

When nothing moved, say so explicitly: "already at latest — v0.1.40 is a prerelease and the pin
is v0.1.39; no effective change". Finish with `pixi r lint`.
