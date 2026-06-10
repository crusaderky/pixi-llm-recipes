#!/usr/bin/env bash
set -euo pipefail

# Install pre-built llama.cpp binaries into the conda prefix.
# Rattler-build extracts the linux-64 archive (used as the default source).
# For other platforms, we download and swap in the correct archive.
#
# Strategy:
#   1. Detect target platform
#   2. If not linux-64, download the correct archive and extract it over the work dir
#   3. Copy files into ${PREFIX}/opt/llama, create symlinks (or copies) in ${PREFIX}/bin

VERSION="b9587"

# Detect target platform
UNAME_S=$(uname -s)
UNAME_M=$(uname -m)
if [[ "$UNAME_S" == MINGW* ]] || [[ "$UNAME_S" == MSYS* ]] || [[ "$UNAME_S" == CYGWIN* ]]; then
    TARGET_PLATFORM="win-64"
elif [[ "$UNAME_M" == aarch64 ]] || [[ "$UNAME_M" == arm64 ]]; then
    TARGET_PLATFORM="linux-aarch64"
else
    TARGET_PLATFORM="linux-64"
fi
echo "Detected target platform: $TARGET_PLATFORM"

# Download and unpack binary archive
case "$TARGET_PLATFORM-$BACKEND" in
    linux-64-cpu)
        ARCHIVE_POSTFIX=ubuntu-x64.tar.gz
        ;;
    linux-64-vulkan)
        ARCHIVE_POSTFIX=ubuntu-vulkan-x64.tar.gz
        ;;
    linux-64-rocm)
        ARCHIVE_POSTFIX=ubuntu-rocm-7.2-x64.tar.gz
        ;;
    linux-aarch64-cpu)
        ARCHIVE_POSTFIX=ubuntu-arm64.tar.gz
        ;;
    linux-aarch64-vulkan)
        ARCHIVE_POSTFIX=ubuntu-vulkan-arm64.tar.gz
        ;;
    win-64-cpu)
        ARCHIVE_POSTFIX=win-cpu-x64.zip
        ;;
    win-64-vulkan)
        ARCHIVE_POSTFIX=win-vulkan-x64.zip
        ;;
    *)
        echo "Invalid platform=$TARGET_PLATFORM backend=$BACKEND"
        exit 1
        ;;
esac

ARCHIVE_URL="https://github.com/ggml-org/llama.cpp/releases/download/${VERSION}/llama-${VERSION}-bin-$ARCHIVE_POSTFIX"
echo "Downloading $ARCHIVE_URL..."

if [[ "$ARCHIVE_POSTFIX" == *.zip ]]; then
    curl -sL $ARCHIVE_URL -o archive.zip
    unzip -o $TMPDIR/archive.zip
    rm archive.zip
else
    curl -sL $ARCHIVE_URL -o archive.tar.gz
    tar xzf archive.tar.gz
    mv llama-$VERSION/* .
    rmdir llama-$VERSION
    rm archive.tar.gz  
fi

mkdir -p "${PREFIX}/opt/llama" "${PREFIX}/bin"

for f in *; do
    [ -e "$f" ] || continue
    f=$(basename "$f")
    echo "Processing file: $f"
    case "$f" in
        LICENSE|build_env.sh|conda_build.*)
            ;;
        *.so|*.so.*)
            cp -v "$f" "${PREFIX}/opt/llama/"
            ;;
        *.dll)
            cp -v "$f" "${PREFIX}/opt/llama/"
            ;;
        *.exe)
            cp -v "$f" "${PREFIX}/opt/llama/"
            cp -v "$f" "${PREFIX}/bin/"
            ;;
        llama*|rpc-server)
            cp -v "$f" "${PREFIX}/opt/llama/"
            if [ -f "${PREFIX}/opt/llama/$f" ]; then
                if [ -x "${PREFIX}/opt/llama/$f" ] || [[ "$f" == *.exe ]]; then
                    if ln -svf "../opt/llama/${f}" "${PREFIX}/bin/${f}" 2>/dev/null; then
                        : # symlink created successfully
                    else
                        cp -v "${PREFIX}/opt/llama/${f}" "${PREFIX}/bin/"
                    fi
                fi
            fi
            ;;
        *)
            echo "Unknown file: $f"
            exit 1
            ;;
    esac
done
