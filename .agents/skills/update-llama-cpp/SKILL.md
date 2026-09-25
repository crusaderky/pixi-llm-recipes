---
name: update-llama-cpp
description: Update both llama-cpp conda recipes (source + binary) to the latest upstream release. Updates BOTH the active fork's version pin and the commented-out mainline variant pin in each recipe.yaml. Use when the user wants to bump llama-cpp to a newer version.
compatibility: Uses `scripts/llama-cpp-changelog.py`, which works from a local commits-only clone cached at `~/.cache/llama-cpp-changelog/<owner>-<repo>.git`. No `gh` CLI or token needed; the PR section is skipped without GitHub auth.
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

**Guardrails — violating any item means STOP, not update:**

> ⚠ **Never change `fork:`.** The user's fork (e.g. `crusaderky/llama.cpp`) is deliberate;
> replacing it discards custom fork state. Only `version:` strings change.

> ⚠ **Use git tags, never branches.** `version:` must be an exact name returned under
> `refs/tags/` by `git ls-remote --tags --refs`. A branch named `v0.4.7` (or any other
> branch) is not a pin and must never be used. Do not use `git ls-remote --heads`, release
> page titles, branch names, or guessed tag names as inputs to an update.

> ⚠ **Never change the tag family/series.** Keep the current family when selecting a
> replacement: `beellama-staging-v0.4.7-rN` may advance only to another
> `beellama-staging-v0.4.7-rM`; it must never become `v0.4.7`, a preview tag, or another
> family. Stable `vX.Y.Z` tags may advance to a newer stable `vA.B.C`, and preview tags
> may advance within the preview family. The sole required cross-family transition is
> `preview-vX.Y.Z`/`vX.Y.Z-preview` → `vX.Y.Z` when that exact stable tag exists.

> ⚠ **Never downgrade.** Candidate must be strictly newer than the current pin. Never
> replace `beellama-staging-v0.4.7-r2` with `beellama-staging-v0.4.7-r1`, and never replace
> `v0.4.7` (including a preview) with `v0.4.6`. If no strictly newer valid tag exists in
> the current family, leave the pin unchanged.

> ⚠ **Compare numerically, not lexically.** `r10` is newer than `r9`; `v0.4.10` is newer
> than `v0.4.9`. `sort -V` alone is not sufficient for the different tag families. Parse
> the numeric components and compare tuple values. A stable `vX.Y.Z` tag is newer than
> either preview spelling for that same numeric version (`vX.Y.Z-preview` and
> `preview-vX.Y.Z`), so use the stable tag whenever it exists.

> ⚠ **Verify the current and candidate tags.** A version is usable only if the exact
> `refs/tags/<version>` exists on that exact fork. If the current value is not a tag, or
> a candidate cannot be verified, stop and report the ambiguity; do not repair it by
> guessing.

## Phase 1 — fetch latest versions

Use `git ls-remote --tags --refs`, not the GitHub REST API (rate-limited without auth).
Read the active `fork:` from each recipe first; do not assume the source and binary recipes
use the same fork. Collect **all** tags for each active fork, then classify them without
consulting branches:

```bash
# Replace FORK with the exact active fork from recipe.yaml. This lists tags only.
git ls-remote --tags --refs "https://github.com/${FORK}.git" \
  | awk '$2 ~ /^refs\/tags\// { sub("^refs/tags/", "", $2); print $2 }'

# Mainline build tags, tags only.
git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
  | awk '$2 ~ /^refs\/tags\/b[0-9]+$/ { sub("^refs/tags/", "", $2); print $2 }'
```

Recognize these exact families before selecting anything:

- `beellama-staging-vMAJOR.MINOR.PATCH-rREV`: staging family; revisions are numeric.
- `vMAJOR.MINOR.PATCH`: stable family.
- `preview-vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-preview`: preview family; stable
  wins over both preview spellings when `vMAJOR.MINOR.PATCH` exists.

Do not treat any other name as a release candidate. In particular, reject names that
appear only as branches, and reject `beellama-staging-v0.4.7-r1` as a candidate for a
stable or preview pin (and vice versa).

## Phase 2 — active pins

Update **only** `version:` in each recipe, leaving `fork:` alone. Read the current
active value, classify it, and select a candidate from that same family:

1. If current is `beellama-staging-vX.Y.Z-rN`, choose the highest `rM` with `M > N`
   from the same exact staging series. If none exists, keep the current tag. Do **not**
   substitute `vX.Y.Z`, even when it is newer by numeric version; it is a different
   series. This rule applies even if the current staging tag is on a fork whose stable
   tags are newer.
2. If current is stable `vX.Y.Z`, choose the highest stable `vA.B.C` that is strictly
   newer. Never choose a preview or staging tag for a stable pin. If no newer stable tag
   exists, keep the current tag.
3. If current is `preview-vX.Y.Z` or `vX.Y.Z-preview`, choose the highest stable tag
   whose numeric version is **strictly newer** than the current preview, if one exists.
   Otherwise choose the newest valid preview in that same preview family. An exact
   stable `vX.Y.Z` always wins over either spelling of its preview because it is newer.
   Never choose a lower numeric version.
4. For any other current value, including a branch, raw commit SHA, or unknown tag
   spelling, stop. Do not silently convert it to a different family or guess a version.

Mandatory examples:

- `beellama-staging-v0.4.7-r1` → `beellama-staging-v0.4.7-r2` when `r2` exists. Never
  `v0.4.7`, `v0.4.6`, or any branch.
- `v0.4.7-preview` or `preview-v0.4.7` → tag `v0.4.7` when that exact tag exists. Never
  downgrade to `v0.4.6`.
- `v0.4.7` → `v0.4.8` only when verified tag `v0.4.8` exists. A branch named `v0.4.8`
  is not a valid target.

The source recipe may use only a stable `vX.Y.Z` when its current pin is stable; the
binary recipe may use a preview tag when its current pin is preview. These rules are
about monotonic tags, not about replacing a branch with a tag. Tags are the only valid
pins.

## Phase 3 — commented mainline pins

In both recipes, set `# version: bNNNN` under `# fork: ggml-org/llama.cpp` to
`LATEST_MAINLINE`, keeping the `#` prefix.

## Phase 4 — verify and report

Checklist before reporting completion:

- [ ] `fork:` unchanged in every context block
- [ ] current and selected `version:` values came from `refs/tags/`, never `refs/heads/`
- [ ] selected tag is strictly newer than current tag; no downgrade
- [ ] staging family stays staging (`-rN` → newer `-rM` with same base)
- [ ] stable/preview family rules followed; exact stable wins over same-version preview
- [ ] every current and candidate tag verified to exist
- [ ] no `preview-v` tag inserted where only a stable tag is valid
- [ ] `pixi lock` run and `pixi r lint` clean

Report all four version strings (source/binary × active/mainline). When nothing moved,
say so explicitly, e.g. "no effective change — latest stable is `v0.4.2`, binary already
at `preview-v0.4.3`; fork pins untouched."
