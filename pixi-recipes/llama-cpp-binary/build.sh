#!/usr/bin/env bash
set -euo pipefail

# Install pre-built llama.cpp binaries into the conda prefix (Linux only;
# Windows is handled by build.bat).
#
# VERSION, FORK, ASSET_PREFIX and BACKEND are set by recipe.yaml
# (build.script.env). FORK is the GitHub `owner/repo` of the active llama.cpp
# fork; ASSET_PREFIX is the release-asset file prefix (`beellama` for
# Anbeeld/beellama.cpp, `llama` for mainline ggml-org/llama.cpp).
#
# Strategy:
#   1. Detect target platform
#   2. Download the matching release archive and extract it into the work dir
#   3. Copy files into ${PREFIX}/opt/llama, create symlinks in ${PREFIX}/bin

# Detect target platform
if [[ "$(uname -m)" == aarch64 ]] || [[ "$(uname -m)" == arm64 ]]; then
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
    linux-64-cuda)
        # beellama only
        ARCHIVE_POSTFIX=ubuntu-cuda-13.3-x64.tar.gz
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
        # llama.cpp mainline only
        ARCHIVE_POSTFIX=ubuntu-vulkan-arm64.tar.gz
        ;;
    *)
        echo "Invalid platform=$TARGET_PLATFORM backend=$BACKEND"
        exit 1
        ;;
esac

ARCHIVE_URL="https://github.com/${FORK}/releases/download/${VERSION}/${ASSET_PREFIX}-${VERSION}-bin-$ARCHIVE_POSTFIX"
echo "Downloading $ARCHIVE_URL..."

# Release-asset HEAD requests are intermittently 404 on GitHub; retry.
curl -sL --retry 5 --retry-delay 5 --retry-all-errors "$ARCHIVE_URL" -o archive.tar.gz
tar xzf archive.tar.gz
mv "${ASSET_PREFIX}-${VERSION}"/* .
rmdir "${ASSET_PREFIX}-${VERSION}"
rm archive.tar.gz

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
        llama*|*rpc-server)
            cp -v "$f" "${PREFIX}/opt/llama/"
            if [ -x "${PREFIX}/opt/llama/$f" ]; then
                ln -svf "../opt/llama/${f}" "${PREFIX}/bin/${f}"
            fi
            ;;
        *)
            echo "Unknown file: $f"
            exit 1
            ;;
    esac
done

# Add missing libgomp symlink
mkdir -p ${PREFIX}/lib
cd ${PREFIX}/lib
ln -fs libgomp.so.1.0.0 libgomp.so.1
