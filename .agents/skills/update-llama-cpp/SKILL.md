---
name: update-llama-cpp
description: Update both llama-cpp conda recipes (source + binary) to the latest upstream release. Updates BOTH the active fork pin (beellama) and the commented-out mainline variant pin in each recipe.yaml. Use when the user wants to bump llama-cpp to a newer version.
compatibility: Uses `scripts/llama-cpp-changelog.py` for changelog/merge detection. No `gh` CLI or GitHub token needed — the script works from a local commits-only git clone (cached per-fork at `~/.cache/llama-cpp-changelog/<repo>.git`). The PR section is skipped without GitHub auth; tags/commits/dates come from git.
allowed-tools: Bash Read Edit
---

## Context

Both `pixi-recipes/llama-cpp-source/recipe.yaml` and
`pixi-recipes/llama-cpp-binary/recipe.yaml` pin **two** forks in their
`context:` blocks:

- **Active beellama fork** — `fork:` + `version:` (stable `vX.Y.Z` OR preview
  `preview-vX.Y.Z`, depending on which recipe and which version you are targeting)
- **Mainline variant** — `# fork: ggml-org/llama.cpp`, `# version: bNNNN`
- Other experimental forks (source only)

**CRITICAL GUARDRAILS:**

> ⚠ **Never change `fork:`.** The user's fork (e.g. `crusaderky/llama.cpp`) is a deliberate choice and must be preserved. Only update `version:` strings. Upgrading `fork:` is a bug — it discards custom fork state and was a failure in the last `update everything` run.

> ⚠ **Never conflate stable and preview tags.** Source builds require **stable** git tags (`vX.Y.Z`). Binary builds may use **preview** tags (`preview-vX.Y.Z`) since pre-built binaries are published from preview releases. Fetching "the latest beellama stable tag" must return a `vX.Y.Z` tag, not a `preview-vX.Y.Z`. Using a preview tag in a source recipe is incorrect — source builds clone at the tag, and preview tags are pre-release.

> ⚠ **Always verify the fetched tag exists** on the fork before applying it. A tag name from a release page title or preview.json may not be a valid git tag. Query the API/refs to confirm the tag exists and resolves.

The uncommented block may differ between binary and source recipes. The update bumps
the `Anbeeld/beellama.cpp` pin **and** `ggml-org/llama.cpp` (four version strings
total across two recipes), so the commented variants stay current. It does NOT
touch other forks.

The source recipe's `source:` block uses `${{ fork }}` and `${{ version }}` —
there is NO separate `source.rev` / commit SHA field. The binary recipe
additionally pins `asset_prefix` (`beellama` vs `llama`); it never changes on
a version bump.

## Steps

### Phase 1 — Fetch latest versions (verify existence!)

**Do NOT use the GitHub REST API for tag discovery** (rate-limited without auth). Use `git ls-remote` instead, and **verify each tag exists** by checking the repo's tags endpoint or the fetched commit data.

1. **Latest beellama stable tag** (ignore `preview-*`):
   ```bash
   git ls-remote --tags --refs https://github.com/Anbeeld/beellama.cpp.git \
     | grep -oE 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
   ```
   → `LATEST_BEELLAMA` (this is a **stable** `vX.Y.Z` tag, NOT `preview-vX.Y.Z`)

   **Verification**: Confirm `LATEST_BEELLAMA` exists as a real tag by checking
   `https://api.github.com/repos/Anbeeld/beellama.cpp/tags?per_page=100` or
   `git ls-remote --tags https://github.com/Anbeeld/beellama.cpp.git | grep refs/tags/$LATEST_BEELLAMA`.

2. **Latest mainline llama.cpp tag**:
   ```bash
   git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
     | grep -oE 'refs/tags/b[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
   ```
   → `LATEST_MAINLINE`

   **Verification**: Same existence check as above for `ggml-org/llama.cpp`.

### Phase 2 — Update the active (beellama) pins

3. Read `pixi-recipes/llama-cpp-source/recipe.yaml`; note the uncommented
   `version:` under `fork:` (whatever fork is currently active in the source
   recipe). **Do NOT touch `fork:`.** If it already equals `LATEST_BEELLAMA`,
   report "Source beellama version already up to date"; else update **only**
   `version:` to `LATEST_BEELLAMA`. Report
   `Source beellama: vOLD → vNEW`.

   **If the active fork is `crusaderky/llama.cpp`** (your fork): preserve it.
   Only update `version:`. Do not replace `fork:` with `Anbeeld/beellama.cpp`.

4. Same for the uncommented `version:` in
   `pixi-recipes/llama-cpp-binary/recipe.yaml`. Report
   `Binary beellama: vOLD → vNEW`.

   **Stable vs preview distinction for binary recipe:**
   - If the binary recipe's active `version:` is a **stable** `vX.Y.Z`, compare
     against `LATEST_BEELLAMA`. If equal, no change.
   - If the binary recipe's active `version:` is a **preview** `preview-vX.Y.Z`,
     keep it as-is UNLESS the latest stable `vX.Y.Z` is strictly greater than
     the preview's base version — then migrate to the stable tag. Otherwise,
     keep the preview (binary builds often use preview pre-built assets).

   **Never use `preview-vX.Y.Z` as a replacement for a stable `vX.Y.Z` in a
   context where only stable tags are valid** (e.g. source recipe's `git:` tag
   field). Preview tags are pre-release and not suitable for source cloning.

5. **Handle git-hash source versions** (source recipe only):
   If the source recipe's `version:` is a raw commit SHA with a `# vX.Y.Z`
   comment (e.g. `4a834dd7...  # v0.4.2 preview`), then:
   a. Is there a stable version newer than `vX.Y.Z`? If so, replace the hash and
      version with `LATEST_BEELLAMA`.
   b. Otherwise, search the same branch for a more recent commit SHA that still
      corresponds to `vX.Y.Z`. Update the hash only.

### Phase 3 — Update the commented (mainline) variant pins

6. In each recipe, update the commented `# version: bNNNN` line under
   `# fork: ggml-org/llama.cpp` to `LATEST_MAINLINE` (keep the `#` prefix). If
   already current, say so. Report
   `Source mainline variant: bOLD → bNEW` and `Binary mainline variant: bOLD → bNEW`.

### Phase 4 — Verify & report

7. **Show both changelogs** for the ranges being updated:
   ```bash
   pixi r llama-cpp-changelog --repo Anbeeld/beellama.cpp <old_v> <LATEST_BEELLAMA>
   pixi r llama-cpp-changelog --repo ggml-org/llama.cpp <old_b> <LATEST_MAINLINE>
   ```
   Present the script output (or a themed summary, citing PR numbers + URLs).
   Do not hand-roll `curl`/compare calls — the script is the canonical dumper.
   Skip a changelog when that fork did not change.

8. **Final verification checklist** (run before reporting completion):
   - [ ] `fork:` unchanged in all recipe context blocks (user's fork preserved)
   - [ ] No `preview-v` tag was inserted where only stable `v` tags are valid
   - [ ] All fetched tags verified as existing on their respective repos
   - [ ] `pixi lock` run and `pixi r lint` clean

9. **Report all changes** (source/binary × beellama/mainline). Include a line
   like: "llama.cpp: no effective version change — latest stable is `v0.4.2`,
   binary already at `preview-v0.4.3`; fork pins untouched." when no versions
   actually moved.
