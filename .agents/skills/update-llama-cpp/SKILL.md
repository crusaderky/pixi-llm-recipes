---
name: update-llama-cpp
description: Update both llama-cpp conda recipes (source + binary) to the latest upstream release. Updates BOTH the active fork's version pin and the commented-out mainline variant pin in each recipe.yaml. Use when the user wants to bump llama-cpp to a newer version.
compatibility: Uses `scripts/llama-cpp-changelog.py`, which works from a local commits-only clone cached at `~/.cache/llama-cpp-changelog/<repo>.git`. No `gh` CLI or token needed; the PR section is skipped without GitHub auth.
allowed-tools: Bash Read Edit
---

## Context

`pixi-recipes/llama-cpp-source/recipe.yaml` and `pixi-recipes/llama-cpp-binary/recipe.yaml`
each carry several `fork:` / `version:` pairs in their `context:` block, with exactly one
uncommented. The commented ones are reference variants, and the `ggml-org/llama.cpp`
mainline pin (`# version: bNNNN`) is kept current alongside the active one.

The two recipes may pin **different** forks — the source recipe often tracks a personal
fork, while the binary recipe tracks `Anbeeld/beellama.cpp`. The source `source:` block
interpolates `${{ fork }}`/`${{ version }}`; there is no `source.rev`. The binary recipe
also pins `asset_prefix` (`beellama` vs `llama`), which never changes on a version bump.

**Guardrails:**

> ⚠ **Never change `fork:`.** The user's fork (e.g. `crusaderky/llama.cpp`) is deliberate;
> replacing it discards custom fork state. Only `version:` strings change.

> ⚠ **Never conflate stable and preview tags.** Source builds clone at the tag, so they
> need a stable `vX.Y.Z`. Binary builds may use `preview-vX.Y.Z`, since pre-built assets
> ship from preview releases.

> ⚠ **Verify every fetched tag actually exists** on the fork before writing it. A name
> taken from a release page title or from `preview.json` is not necessarily a git tag.

## Phase 1 — fetch latest versions

Use `git ls-remote`, not the GitHub REST API (rate-limited without auth):

```bash
# LATEST_BEELLAMA — stable only, preview-* excluded by the regex
git ls-remote --tags --refs https://github.com/Anbeeld/beellama.cpp.git \
  | grep -oE 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1

# LATEST_MAINLINE
git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
  | grep -oE 'refs/tags/b[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
```

Confirm each resolves (`git ls-remote --tags <url> | grep refs/tags/<tag>`).

## Phase 2 — active pins

Update **only** `version:` in each recipe, leaving `fork:` alone.

- **Source recipe**: must end up on a stable `vX.Y.Z`. If the current `version:` is a raw
  commit SHA with a `# vX.Y.Z` comment, either move to `LATEST_BEELLAMA` when a newer
  stable exists, or otherwise advance the SHA within the same `vX.Y.Z` line.
- **Binary recipe**: if the current pin is stable, compare against `LATEST_BEELLAMA`. If it
  is a `preview-vX.Y.Z`, keep it unless the latest stable is strictly newer than the
  preview's base version — then migrate to the stable tag.

## Phase 3 — commented mainline pins

In both recipes, set `# version: bNNNN` under `# fork: ggml-org/llama.cpp` to
`LATEST_MAINLINE`, keeping the `#` prefix.

## Phase 4 — verify and report

```bash
pixi r llama-cpp-changelog --repo Anbeeld/beellama.cpp <old_v> <LATEST_BEELLAMA>
pixi r llama-cpp-changelog --repo ggml-org/llama.cpp <old_b> <LATEST_MAINLINE>
```

Present the script's output (or a themed summary citing PR numbers + URLs); don't
hand-roll `curl` comparisons. Skip a changelog for a fork that didn't move.

Checklist before reporting completion:

- [ ] `fork:` unchanged in every context block
- [ ] no `preview-v` tag inserted where only a stable tag is valid
- [ ] every fetched tag verified to exist
- [ ] `pixi lock` run and `pixi r lint` clean

Report all four version strings (source/binary × active/mainline). When nothing moved,
say so explicitly, e.g. "no effective change — latest stable is `v0.4.2`, binary already
at `preview-v0.4.3`; fork pins untouched."
