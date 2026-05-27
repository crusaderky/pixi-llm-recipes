#!/usr/bin/env bash
set -euo pipefail

# Note: if you split DLLs and executables into bin/ and lib/,
# then llama.cpp will no longer be able to find its optional DLLs
# that serve backends, such as libggml-vulkan.so.
mkdir -p "${PREFIX}/bin" "${PREFIX}/opt/llama"

for f in *; do
    echo "Processing file: $f"
    case "$f" in
        LICENSE|build_env.sh|conda_build.*)
            ;;
        *.so|*.so.*)
            ln -vf "$f" "${PREFIX}/opt/llama/"
            ;;
        llama*|rpc-server)
            ln -vf "$f" "${PREFIX}/opt/llama/"
            if [ -f "${PREFIX}/opt/llama/$f" ] && [ -x "${PREFIX}/opt/llama/$f" ]; then
                pushd "${PREFIX}/bin"
                ln -svf "../opt/llama/$f"
                popd
            fi
            ;;
        *)
            echo "Unknown file: $f"
            exit 1
            ;;        
    esac
done
