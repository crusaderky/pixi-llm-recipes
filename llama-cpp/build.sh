#!/usr/bin/env bash
set -euo pipefail

# Note: if you split DLLs and executables into bin/ and lib/,
# then llama.cpp will no longer be able to find its optional DLLs
# that serve backends, such as libggml-vulkan.so.
mkdir -p "${PREFIX}/bin" "${PREFIX}/opt/llama"

for f in *; do
    name=$(basename "$f")
    echo "Processing file: $f"
    case "$name" in
        LICENSE|build_env.sh|conda_build.*)
            ;;
        *.so|*.so.*)
            mv -v "$f" "${PREFIX}/opt/llama"
            ;;
        llama*|rpc-server)
            mv -v "$f" "${PREFIX}/opt/llama/"
            if [ -f "${PREFIX}/opt/llama/$f" ] && [ -x "${PREFIX}/opt/llama/$f" ]; then
                pushd "${PREFIX}/bin"
                ln -sv "../opt/llama/$name"
                popd
            fi
            ;;
        *)
            echo "Unknown file: $f"
            exit 1
            ;;        
    esac
done
