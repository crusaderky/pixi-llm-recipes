---
name: update-llama-cpp
description: Update both llama-cpp conda recipes (source + binary) to the newest usable release of whatever fork each one pins. The source recipe follows the newest git tag; the binary recipe follows the newest tag that actually publishes release assets. Advances the active `version:` and any commented-out mainline `bNNNN` reference pin; use when the user wants to bump llama-cpp to a newer version.
compatibility: Uses `scripts/llama-cpp-changelog.py`, which works from a local commits-only clone cached at `~/.cache/llama-cpp-changelog/<owner>-<repo>.git`. No `gh` CLI or token needed; the PR section is skipped without GitHub auth.
allowed-tools: Bash Read Edit
---

## Context

`pixi-recipes/llama-cpp-source/recipe.yaml` and `pixi-recipes/llama-cpp-binary/recipe.yaml`
each carry several `fork:` / `version:` pairs in their `context:` block, with exactly one
uncommented. The commented ones are reference variants. Both recipes currently pin
`ggml-org/llama.cpp` as the active fork, with beellama and the personal patch fork kept as
comments; they can pin different `version:` values because the binary recipe needs release
assets (currently source `b12635`, binary `b12633`). The source `source:` block
interpolates `${{ fork }}` / `${{ version }}`; the binary recipe also interpolates
`asset_prefix` (`llama` for mainline, `beellama` for beellama). There is no commit-SHA field.

**Guardrails — violating any item means STOP, not update:**

> ⚠ **Never change `fork:` or `asset_prefix` in this skill.** They are deliberate choices.
> Switching forks is a separate, explicit user request; a version bump only edits `version:`
> strings. Each recipe may pin its own fork; leave both pins as they are.

> ⚠ **Use git tags, never branches.** `version:` must be an exact name returned under
> `refs/tags/` by `git ls-remote --tags --refs`. A branch named `v0.4.7` is not a pin.
> Do not use `git ls-remote --heads`, release page titles, or guessed tag names.

> ⚠ **Binary pins need release assets, not just a tag.** `pixi-recipes/llama-cpp-binary`
> downloads `ASSET_PREFIX-<version>-bin-*` archives from the tag's GitHub release. A tag
> with no release (mainline often tags before publishing assets, and some tags never get
> any) is unusable there even though `git ls-remote` lists it. Keep the binary pin at the
> newest tag that has those assets; the source recipe is not subject to this check.

> ⚠ **Never change the tag family/series.** Keep the current family when selecting a
> replacement: `beellama-staging-v0.5.0-r3` advances only to another `-rN` of the same
> base; `bNNNN` only to a higher `bNNNN`; `vX.Y.Z` only to another stable semver.

> ⚠ **Never downgrade a recipe relative to its own current pin; compare numerically, not
> lexically.** `r10` > `r9`, `b12613` > `b9999`, `v0.4.10` > `v0.4.9`. A stable `vX.Y.Z`
> wins over either preview spelling for the same numeric version. If no strictly newer
> usable tag exists, keep the pin. The binary pin may legitimately trail the source pin
> because its tag must also publish release assets.

> ⚠ **Verify every current and candidate tag** exists under `refs/tags/` on that exact
> fork. If the current value is not a tag, or a candidate cannot be verified, stop and
> report the ambiguity; do not repair it by guessing.

## Phase 1 — fetch latest versions

Use `git ls-remote --tags --refs`, not the GitHub REST API (rate-limited without auth).
Read the active `fork:` from each recipe first:

```bash
# FORK = the exact active fork from recipe.yaml; tags only, no branches.
git ls-remote --tags --refs "https://github.com/${FORK}.git" \
  | awk '$2 ~ /^refs\/tags\// { sub("^refs/tags/", "", $2); print $2 }'

# Mainline build tags, for the commented-out reference pin.
git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
  | awk '$2 ~ /^refs\/tags\/b[0-9]+$/ { sub("^refs/tags/", "", $2); print $2 }'
```

The tag list is only the candidate pool. For the **binary** recipe, walk it newest-first and
stop at the first tag whose GitHub release actually carries the archives `build.sh` /
`build.bat` download. `ASSET_PREFIX` comes from that recipe's active context block (`llama`
for mainline, `beellama` for beellama):

```bash
# Example: FORK=ggml-org/llama.cpp, ASSET_PREFIX=llama.
git ls-remote --tags --refs "https://github.com/${FORK}.git" \
  | awk '$2 ~ /^refs\/tags\/b[0-9]+$/ { sub("^refs/tags/", "", $2); print $2 }' \
  | sort -Vr | while read -r tag; do
      if curl -fsSL "https://api.github.com/repos/${FORK}/releases/tags/${tag}" \
          | grep -q "\"name\": \"${ASSET_PREFIX}-${tag}-bin-ubuntu-x64.tar.gz\""; then
          echo "$tag"; break
      fi
    done
```

A 404 from `/releases/tags/<tag>` means that tag has no release at all; skip it. If the API
is rate-limited, use `gh api "repos/${FORK}/releases/tags/${tag}"` when authenticated, or
check a few candidates directly with `curl -sIL` on the asset URL. Do **not** fall back to
the newest tag with no assets — that is how the binary recipe got pinned to a tag whose
binaries do not exist.

Asset postfixes also drift between releases (upstream renamed the ROCm archive from
`ubuntu-rocm-7.2-x64` to `ubuntu-rocm-10.0-x64`). Compare the names in the release with the
postfixes in `build.sh` / `build.bat`; a renamed or removed archive is an interface break to
report, not a reason to move the version pin.

Recognize these families before selecting anything:

- `bNNNN` — mainline llama.cpp build tags.
- `beellama-staging-vMAJOR.MINOR.PATCH-rREV` — staging family; revisions are numeric.
- `vMAJOR.MINOR.PATCH` — stable semver.
- `preview-vMAJOR.MINOR.PATCH` or `vMAJOR.MINOR.PATCH-preview` — preview semver; stable
  wins over both spellings when `vMAJOR.MINOR.PATCH` exists.

The sole allowed cross-family transition is preview → the exact stable tag with the same
numeric version. Reject names that exist only as branches and unrelated tag spellings.

## Phase 2 — active pins

Update **only** `version:` for each recipe's active fork:

1. `bNNNN` → highest `bNNNN` strictly greater than the current number, subject to the binary
   asset check above. The source and binary recipes may therefore land on different numbers;
   leave a valid binary pin alone when only later tag-only releases appeared.
2. `beellama-staging-vX.Y.Z-rN` → highest `rM > rN` in the same staging series. Never
   substitute `vX.Y.Z`, even when numerically newer.
3. Stable `vX.Y.Z` → highest stable `vA.B.C` that is strictly newer.
4. Preview → exact stable `vX.Y.Z` when it exists, else the newest valid preview with a
   strictly higher numeric version.
5. Any other spelling (branch, SHA, unknown pattern) → stop, do not guess.

## Phase 3 — commented mainline reference

If the recipe has a `# fork: ggml-org/llama.cpp` / `# version: bNNNN` pair and that fork
is not the active one, update the commented `bNNNN` to the latest mainline tag, keeping
the `#` prefix. When mainline is already active (the current state), there is no separate
reference pin to bump: say so instead of inventing a duplicate comment.

## Phase 4 — verify and report

Checklist before reporting completion:

- [ ] `fork:` and `asset_prefix` unchanged in every context block
- [ ] current and selected `version:` values came from `refs/tags/`, never `refs/heads/`
- [ ] binary pin's release actually contains the `ASSET_PREFIX-<version>-bin-*` archives; a later tag with no assets was not used
- [ ] selected tag is strictly newer for that recipe; no downgrade or family change
- [ ] every current and candidate tag verified to exist
- [ ] commented mainline reference updated only when separate from the active pin
- [ ] `pixi lock` run and `pixi r lint` clean

Report each recipe's active version and its mainline reference (or "active = mainline").
When nothing moved, say so explicitly, e.g. "no effective change — both recipes already
pin their newest usable mainline tags; forks untouched." Include the binary asset check in
the report even when the pin did not move.
