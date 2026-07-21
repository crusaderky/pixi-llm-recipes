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

- **Active fork** (uncommented) — `fork: Anbeeld/beellama.cpp`,
  `version: vX.Y.Z` (stable releases only; ignore `preview-vX.Y.Z`).
- **Mainline variant** (commented out) — `# fork: ggml-org/llama.cpp`,
  `# version: bNNNN` — a ready-to-switch fallback that must be kept current.

Every update run bumps **both** pins in **both** recipes (four version
strings total), so the commented mainline variant never goes stale.

The source recipe's `source:` block uses `${{ fork }}` and `${{ version }}` —
there is NO separate `source.rev` / commit SHA field. The binary recipe
additionally pins `asset_prefix` (`beellama` vs `llama`); it never changes on
a version bump.

## Steps

### Phase 1 — Fetch latest versions

Do NOT use the GitHub REST API for tag discovery (rate-limited without auth).

1. **Latest beellama stable tag** (ignore `preview-*`):
   ```bash
   git ls-remote --tags --refs https://github.com/Anbeeld/beellama.cpp.git \
     | grep -oE 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
   ```
   → `LATEST_BEELLAMA`.

2. **Latest mainline llama.cpp tag**:
   ```bash
   git ls-remote --tags --refs https://github.com/ggml-org/llama.cpp.git \
     | grep -oE 'refs/tags/b[0-9]+$' | sed 's|refs/tags/||' | sort -V | tail -1
   ```
   → `LATEST_MAINLINE`.

### Phase 2 — Update the active (beellama) pins

3. Read `pixi-recipes/llama-cpp-source/recipe.yaml`; note the uncommented
   `version:` under `fork: Anbeeld/beellama.cpp`. If it already equals
   `LATEST_BEELLAMA`, report "Source beellama version already up to date";
   else update it. Report `Source beellama: vOLD → vNEW`.

4. Same for the uncommented `version:` in
   `pixi-recipes/llama-cpp-binary/recipe.yaml`. Report
   `Binary beellama: vOLD → vNEW`.

### Phase 3 — Update the commented (mainline) variant pins

5. In each recipe, update the commented `# version: bNNNN` line that sits
   under `# fork: ggml-org/llama.cpp` to `LATEST_MAINLINE` (keep the `#`
   comment prefix). If already current, say so. Report
   `Source mainline variant: bOLD → bNEW` and `Binary mainline variant: bOLD → bNEW`.

### Phase 4 — Verify & report

6. Run `bash scripts/diff-llama-cpp-variants.sh` and show the output: the
   active source and binary fork+version must agree, and the commented
   mainline variants must agree.

7. **Show both changelogs** for the ranges being updated:
   ```bash
   pixi r llama-cpp-changelog --repo Anbeeld/beellama.cpp <old_v> <LATEST_BEELLAMA>
   pixi r llama-cpp-changelog --repo ggml-org/llama.cpp <old_b> <LATEST_MAINLINE>
   ```
   Present the script output (or a themed summary, citing PR numbers + URLs).
   Do not hand-roll `curl`/compare calls — the script is the canonical dumper.
   Skip a changelog when that fork did not change.

8. **Report all changes** (source/binary × beellama/mainline).

9. Run `pixi lock` to regenerate the lockfile, then `pixi r lint` and fix any
   issues.
