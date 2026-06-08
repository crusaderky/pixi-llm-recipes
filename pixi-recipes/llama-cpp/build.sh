#!/usr/bin/env bash
set -euo pipefail

# Configure backend-specific CMake flags.
EXTRA_CMAKE_ARGS=()
case "${BACKEND}" in
    cpu)
        ;;
    cuda)
        EXTRA_CMAKE_ARGS+=(-DGGML_CUDA=ON)
        ;;
    vulkan)
        EXTRA_CMAKE_ARGS+=(-DGGML_VULKAN=ON)
        ;;
    *)
        echo "Unknown backend: ${BACKEND}"
        exit 1
        ;;
esac

cmake -S . -B build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${PREFIX}" \
    -DCMAKE_INSTALL_LIBDIR=opt/llama \
    -DCMAKE_INSTALL_BINDIR=opt/llama \
    -DCMAKE_INSTALL_RPATH='$ORIGIN' \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    -DBUILD_SHARED_LIBS=ON \
    -DGGML_BACKEND_DL=ON \
    -DGGML_NATIVE=OFF \
    -DGGML_CPU_ALL_VARIANTS=ON \
    -DGGML_RPC=ON \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_TOOLS=ON \
    -DLLAMA_BUILD_SERVER=ON \
    "${EXTRA_CMAKE_ARGS[@]}"

cmake --build build --config Release --parallel "${CPU_COUNT:-2}"
cmake --install build --config Release

# Executables and shared libraries are co-located in ${PREFIX}/opt/llama so that
# llama.cpp can dlopen its optional backend DLLs (e.g. libggml-cuda.so) at runtime.
# Expose executables on PATH via relative symlinks into ${PREFIX}/bin.
mkdir -p "${PREFIX}/bin"
pushd "${PREFIX}/bin" >/dev/null
for exe in "${PREFIX}/opt/llama/"llama-* "${PREFIX}/opt/llama/rpc-server"; do
    [ -f "$exe" ] || continue
    [ -x "$exe" ] || continue
    name="$(basename "$exe")"
    ln -svf "../opt/llama/${name}" "${name}"
done
popd >/dev/null
