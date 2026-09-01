#!/usr/bin/env bash
set -euo pipefail

# --- ccache ------------------------------------------------------------------
# ccache is a conda build dependency, so it is on PATH from ${BUILD_PREFIX}/bin;
# no system-wide install is involved. CCACHE_DIR / CCACHE_MAXSIZE come from
# recipe.yaml (HOME here points at the throwaway work dir, so the cache location
# cannot be left to ccache's default).
#
# Everything below exists to make objects hit across *different* build trees:
# rattler-build compiles in .pixi/bld/<pkg>/<hash>/, so ${SRC_DIR}, ${PREFIX}
# and ${BUILD_PREFIX} all move between builds, and the conda prefixes are
# recreated with fresh inode timestamps each time.
#
#   base_dir       rewrite absolute paths below the build root (work/, bld/,
#                  host_placehold.../) to CWD-relative ones when hashing, so
#                  -I/-isystem args and __FILE__ stop depending on <hash>
#   hash_dir=off   don't hash the (moving) CWD into the result
#   compilercheck  hash the compiler's *contents*, not its path+mtime, since it
#                  lives in a per-build ${BUILD_PREFIX}
#   sloppiness     the conda-recipe set: freshly extracted headers under
#                  ${PREFIX}/include always look "too new" to cache, and the
#                  build embeds no meaningful __DATE__/__TIME__
export CCACHE_BASEDIR="$(dirname "${SRC_DIR}")"
export CCACHE_NOHASHDIR=1
export CCACHE_COMPILERCHECK=content
export CCACHE_SLOPPINESS=time_macros,include_file_mtime,include_file_ctime,system_headers,pch_defines,locale
# Per-build statistics, without zeroing the shared cache's cumulative counters
# (several backends may be building at once). Lands in the throwaway work dir.
export CCACHE_STATSLOG="${SRC_DIR}/ccache-stats.log"
mkdir -p "${CCACHE_DIR}"

# Setting the launchers explicitly also short-circuits ggml's own GGML_CCACHE
# autodetection (ggml/src/CMakeLists.txt), which would otherwise install ccache
# as a global RULE_LAUNCH_COMPILE.
EXTRA_CMAKE_ARGS=(
    -DCMAKE_C_COMPILER_LAUNCHER=ccache
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
)
INSTALL_RPATH='$ORIGIN'

# Configure backend-specific CMake flags.
case "${BACKEND}" in
    cpu)
        ;;
    cuda)
        EXTRA_CMAKE_ARGS+=(-DGGML_CUDA=ON)
        EXTRA_CMAKE_ARGS+=(-DCMAKE_CUDA_COMPILER_LAUNCHER=ccache)
        # Compile exotic KV quantization combinations
        # faster runtime, slower compile time
        EXTRA_CMAKE_ARGS+=(-DGGML_CUDA_FA_ALL_QUANTS=ON)
        ;;
    vulkan)
        EXTRA_CMAKE_ARGS+=(-DGGML_VULKAN=ON)
        ;;
    rocm)
        # System ROCm (hipBLAS/rocBLAS) is not available on conda-forge, so this
        # backend links against the ROCm install on the build host. ROCM_PATH is
        # passed in from recipe.yaml (default /opt/rocm/core-10.0, overridable
        # with LLAMA_ROCM_PATH); ggml/src/ggml-hip/CMakeLists.txt reads
        # $ENV{ROCM_PATH} and prepends it to CMAKE_PREFIX_PATH, which is how
        # find_package(hip|hipblas|rocblas) resolves. Deliberately *not*
        # `hipconfig -R`: /usr/bin/hipconfig is a Debian alternatives symlink that
        # ROCm 10 hijacks from Ubuntu's hipcc package, so its answer depends on
        # alternatives priority rather than on anything this recipe controls.
        if [ ! -d "${ROCM_PATH:-}" ]; then
            echo "ROCM_PATH=${ROCM_PATH} does not exist." >&2
            echo "Install ROCm there, or point LLAMA_ROCM_PATH at your ROCm prefix." >&2
            exit 1
        fi
        export ROCM_PATH
        export HIP_PATH="${HIP_PATH:-${ROCM_PATH}}"
        # Ask that prefix's own hipconfig, not whichever one is on PATH.
        if [ -z "${HIPCXX:-}" ]; then
            export HIPCXX="$("${ROCM_PATH}/bin/hipconfig" -l)/clang++"
        fi
        # ROCm 10 installs no ld.so.conf.d drop-in, and its libamdhip64 shares a
        # soname -- libamdhip64.so.7 -- with Ubuntu's archive ROCm 7.1, which *is*
        # on the default linker path. Without an absolute RPATH the loader would
        # silently bind this build to 7.1 instead of failing loudly. See the
        # matching rpath_allowlist in recipe.yaml.
        INSTALL_RPATH="${INSTALL_RPATH}:${ROCM_PATH}/lib"
        # GPU_TARGETS (semicolon-separated gfx list) comes from recipe.yaml; keep
        # GPU detection out of the build so it also compiles on GPU-less CI hosts.
        EXTRA_CMAKE_ARGS+=(-DGGML_HIP=ON)
        EXTRA_CMAKE_ARGS+=(-DAMDGPU_TARGETS="${GPU_TARGETS}")
        EXTRA_CMAKE_ARGS+=(-DGPU_TARGETS="${GPU_TARGETS}")
        EXTRA_CMAKE_ARGS+=(-DCMAKE_HIP_COMPILER="${HIPCXX}")
        EXTRA_CMAKE_ARGS+=(-DCMAKE_HIP_COMPILER_LAUNCHER=ccache)
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
    -DCMAKE_INSTALL_RPATH="${INSTALL_RPATH}" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
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

# Per-build hit rate, for the build log.
echo "ccache statistics for this build (cache dir: ${CCACHE_DIR}):"
ccache --show-log-stats

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
