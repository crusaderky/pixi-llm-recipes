#!/usr/bin/env bash
set -euo pipefail

# Install pre-built llama.cpp binaries into the conda prefix.
# Rattler-build already extracted and flattened the upstream tarball,
# so all files are already in the work directory root.
#
# Strategy: copy actual files into ${PREFIX}/opt/llama, then create
# relative symlinks from ${PREFIX}/bin pointing to opt/llama.

mkdir -p "${PREFIX}/opt/llama" "${PREFIX}/bin"

for f in *; do
    echo "Processing file: $f"
    case "$f" in
        LICENSE|build_env.sh|conda_build.*)
            ;;
        *.so|*.so.*)
            cp -v "$f" "${PREFIX}/opt/llama/"
            ;;
        llama*|rpc-server)
            cp -v "$f" "${PREFIX}/opt/llama/"
            if [ -f "${PREFIX}/opt/llama/$f" ] && [ -x "${PREFIX}/opt/llama/$f" ]; then
                pushd "${PREFIX}/bin" >/dev/null
                ln -svf "../opt/llama/${f}"
                popd
            fi
            ;;
        *)
            echo "Unknown file: $f"
            exit 1
            ;;
    esac
done
