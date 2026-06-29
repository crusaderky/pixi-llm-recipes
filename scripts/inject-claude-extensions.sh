#!/usr/bin/env bash
# Inject conda-packaged Claude Code extensions from $CONDA_PREFIX/home/.claude
# into the host's ~/.claude. Only copies files that don't already exist, so
# user overrides are preserved.
set -euo pipefail

SRC="${CONDA_PREFIX}/home/.claude"
DST="${HOME}/.claude"

if [ ! -d "$SRC" ]; then
    exit 0
fi

mkdir -p "$DST"

# Copy hook files from the package if they don't exist on the host.
# Managed by herdr — if the hook file exists, assume the user has a newer
# version and skip.
if [ -d "$SRC/hooks" ]; then
    mkdir -p "$DST/hooks"
    for f in "$SRC/hooks/"*; do
        [ -e "$f" ] || continue
        name="$(basename "$f")"
        if [ ! -e "$DST/hooks/$name" ]; then
            cp -a "$f" "$DST/hooks/$name"
        fi
    done
fi

# Copy skill files from the package if they don't exist on the host.
if [ -d "$SRC/skills" ]; then
    mkdir -p "$DST/skills"
    for skill_dir in "$SRC/skills/"*; do
        [ -d "$skill_dir" ] || continue
        name="$(basename "$skill_dir")"
        if [ ! -d "$DST/skills/$name" ]; then
            cp -a "$skill_dir" "$DST/skills/$name"
        fi
    done
fi
# Copy rtk's command reference (RTK.md) from the package if it doesn't exist.
if [ -f "$SRC/RTK.md" ] && [ ! -f "$DST/RTK.md" ]; then
    cp "$SRC/RTK.md" "$DST/RTK.md"
fi

# Ensure ~/.claude/CLAUDE.md pulls in RTK.md via rtk's @RTK.md include. Don't
# clobber an existing host CLAUDE.md — append the reference only if it's missing.
if [ -f "$SRC/CLAUDE.md" ]; then
    if [ ! -f "$DST/CLAUDE.md" ]; then
        cp "$SRC/CLAUDE.md" "$DST/CLAUDE.md"
    elif ! grep -qF '@RTK.md' "$DST/CLAUDE.md"; then
        printf '\n@RTK.md\n' >> "$DST/CLAUDE.md"
    fi
fi

if [ -f "$SRC/settings.json" ]; then
    if [ ! -f "$DST/settings.json" ]; then
        cp "$SRC/settings.json" "$DST/settings.json"
    else
        # Use node to deep-merge: host settings override package defaults
        node -e "
            const src = JSON.parse(require('fs').readFileSync('$SRC/settings.json', 'utf8'));
            const dst = JSON.parse(require('fs').readFileSync('$DST/settings.json', 'utf8'));
            function merge(base, over) {
                for (const k of Object.keys(over)) {
                    if (over[k] && typeof over[k] === 'object' && !Array.isArray(over[k]) && base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) {
                        merge(base[k], over[k]);
                    } else {
                        base[k] = over[k];
                    }
                }
            }
            merge(src, dst);
            require('fs').writeFileSync('$DST/settings.json', JSON.stringify(src, null, 2) + '\n');
        "
    fi
fi
