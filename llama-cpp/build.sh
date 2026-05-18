#!/usr/bin/env bash
set -euo pipefail

mkdir -p "${PREFIX}/bin" "${PREFIX}/lib"

for f in *; do
    name=$(basename "$f")
    case "$name" in
        LICENSE|build_env.sh|conda_build.*)
            ;;
        *.so|*.so.*)
            mv -v "$f" "${PREFIX}/lib/"
            ;;
        llama-*|rpc-server)
            if [ -f "$f" ] && [ -x "$f" ]; then
                mv -v "$f" "${PREFIX}/bin/"
            fi
            ;;
        *)
            echo "Unknown file: $f"
            exit 1
            ;;        
    esac
done

