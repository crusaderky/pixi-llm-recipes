#!/usr/bin/env bash
set -euo pipefail

# Strata's engine (strata) and its image encoder (strata-vision) are C++/CUDA, and the
# project publishes no Linux prebuilt: its release assets are Windows only, so a plain
# Linux install compiles both. Compiling them *here*, at package build time, is what makes
# `pixi install -e strata` produce a working env -- the first start then only downloads the
# model. It is deliberately the same build the project's own Dockerfile runs.
#
# What is not done here: the model. The GGUF shards come from the Hugging Face cache and
# the MTP draft layer from the Qwen checkpoint at the first start (scripts/strata-run.py).
#
# CUDA_ARCHITECTURES (recipe.yaml) is the GPU list to embed; no GPU is needed at build time.

# rattler-build extracts each source into <work>/<target_directory> and SRC_DIR is the work
# directory -- but that has moved between versions, so both layouts are accepted.
WORK="$(cd "${SRC_DIR}" && pwd)"
if [ ! -f "${WORK}/strata/setup.py" ]; then
    WORK="$(cd "${SRC_DIR}/.." && pwd)"
fi
STRATA_SRC="${WORK}/strata"
LLAMA_SRC="${WORK}/llama.cpp"
if [ ! -f "${STRATA_SRC}/setup.py" ] || [ ! -d "${LLAMA_SRC}/ggml" ]; then
    echo "expected the Strata and llama.cpp sources in ${WORK}" >&2
    exit 1
fi
export STRATA_SRC LLAMA_SRC
export CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES:-86}"

python - <<'PYEOF'
import json
import os
import pathlib
import shutil
import sys

sys.path.insert(0, os.environ["STRATA_SRC"])
import setup  # noqa: E402  Strata's own build driver, so the flags cannot drift from upstream

# "86" / "86;89" / "89,120" -> CMake's semicolon-separated form
archs = [a.strip() for a in os.environ["CUDA_ARCHITECTURES"].replace(",", ";").split(";") if a.strip()]
if not archs:
    sys.exit("CUDA_ARCHITECTURES is empty")
cuda_archs = ";".join(archs)

nvcc, version = setup.find_nvcc()
if nvcc is None:
    sys.exit("no nvcc in the build environment (the recipe's cuda-nvcc build requirement)")
print(f"  nvcc {nvcc} (CUDA {version[0]}.{version[1]}), architectures {cuda_archs}", flush=True)

llama = pathlib.Path(os.environ["LLAMA_SRC"])

# Every definition setup.py's own build_engine() adds, so the engine this recipe compiles is
# the engine upstream compiles: cmake_build alone is only the build *function* -- the definition
# list belongs to upstream's caller, and it is not derivable from the two flags this script used
# to carry. ggml_defs/vision_llama are upstream's own accessors (the override, else the vendored
# tree). engine_defs is the one that changes the binary: -DSTRATA_EXPERIMENTAL_SM60=ON for a
# card below sm_75 or a CUDA 12 toolkit -- without it, STRATA_CUDA_ARCHITECTURES=70 built a
# Pascal engine that cannot run (nothing else in the recipe or in BUILD.json reports it).
# The toolkit major comes from the nvcc that builds, not from the recipe's cuda-version pin.
# arch_nums is the same digits BUILD.json records; an architecture name CMake spells with a
# suffix ("90a", "compute_90") cannot be a Pascal card, so it skips the floor check.
arch_nums = [int(a.split("-")[0]) for a in archs if a.split("-")[0].isdigit()]
toolkit = int(version[0])

# Two of upstream's def helpers are deliberately NOT called, because both are about the machine
# that runs setup.py rather than about this package:
#   toolkit_root_defs -> -DCUDAToolkit_ROOT, to stop a host with two CUDA toolkits linking
#       cudart from the wrong one. A rattler-build environment has exactly one, in ${PREFIX},
#       and pointing CUDAToolkit_ROOT at a conda prefix is a worse bet than CMake's own search
#       (conda ships the libraries in lib/, FindCUDAToolkit looks for lib64 first).
#   isa_floor_defs -> the *older-CPU* engine (STRATA_ISA_FLOOR), chosen from the CPU setup.py
#       finds. A package must not be tuned to the build host in either direction, so this
#       builds the normal engine, which is what floor "" means.
engine_defs = [
    "-DSTRATA_ENABLE_CUDA=ON",
    "-DSTRATA_BUILD_TESTS=OFF",
    f"-DCMAKE_CUDA_ARCHITECTURES={cuda_archs}",
    f"-DCMAKE_CUDA_COMPILER={nvcc}",
    *setup.host_compiler_defs(True),
    *setup.ggml_defs(llama),
    *(setup.engine_defs(arch_nums, toolkit) if arch_nums else []),
]
setup.cmake_build(
    setup.ROOT,
    setup.ROOT / "build",
    "strata",
    engine_defs,
    None,
    "build-strata.bat",
)
# Upstream's vision defs: the same tree accessors, the same host compilers (it says "native,
# like the engine"), and STRATA_PORTABLE=OFF, which the engine build gets from its default.
vision_defs = [
    f"-DLLAMA_DIR={setup.vision_llama(llama)}",
    "-DSTRATA_VISION_CUDA=ON",
    "-DSTRATA_PORTABLE=OFF",
    *setup.host_compiler_defs(True),
    f"-DCMAKE_CUDA_ARCHITECTURES={cuda_archs}",
    f"-DCMAKE_CUDA_COMPILER={nvcc}",
]
setup.cmake_build(
    setup.ROOT / "tools" / "vision",
    setup.ROOT / "build-vision",
    "strata-vision",
    vision_defs,
    None,
    "build-vision.bat",
)

engine = setup.ROOT / "engine"
engine.mkdir(exist_ok=True)
shutil.copy2(setup.ROOT / "build" / setup.EXE, engine / setup.EXE)
shutil.copy2(setup.ROOT / "build-vision" / "bin" / setup.VEXE, engine / setup.VEXE)
# libstrata_kq.so: llama.cpp's MMQ prompt instances for Q4_K / Q5_K / Q5_1 / Q6_K -- the
# experts of Unsloth's K-quant files -- kept in a library of their own so the engine does not
# pay ~3.8 MB of VRAM for them at every start; it dlopens them out of its own folder
# (/proc/self/exe's directory -- src/prefill/mmq_kq_loader.cpp), so it has to sit beside the
# executable, not in ${PREFIX}/lib.
#
# It is normally ABSENT from this package, and that is upstream's own answer, not a packaging
# bug: 0.2.14 moved the CUDA/HIP prompt path to Strata's own fused kernels and CMake's
# STRATA_MMQ defaults OFF since then (upstream's build_engine passes -DSTRATA_MMQ=OFF for
# everything but arm64). strata_kq is built inside strata_cuda_mmq(), which runs only under
# `if(STRATA_ENABLE_CUDA AND STRATA_MMQ)`, so a default build compiles no MMQ at all and the
# engine reports its prompt layers on the fused kernels. The copy stays for a -DSTRATA_MMQ=ON
# A/B build, where the loader still wants the file beside the executable.
kq = setup.ROOT / "build" / setup.KQ_LIB
if kq.is_file():
    shutil.copy2(kq, engine / setup.KQ_LIB)
    print(f"  K-quant prompt kernels: {setup.KQ_LIB}", flush=True)

# BUILD.json is what setup.py reads at the first start to decide whether the engine is
# current: source=local plus a matching src hash means it compiles nothing again, so the
# runtime never needs a compiler. `cuda_dirs` is the directory the engine's shared
# libraries are found in *after* installation -- rattler-build rewrites the prefix in this
# file, and the server prepends the result to LD_LIBRARY_PATH when it starts the engine.
meta = {
    "source": "local",
    "version": setup.source_version(),
    "archs": [int(a.split("-")[0]) for a in cuda_archs.split(";") if a.split("-")[0].isdigit()],
    "vision": "gpu",
    "cuda_dirs": [str(pathlib.Path(os.environ["PREFIX"]) / "lib")],
    "src": setup.source_hash(setup.ENGINE_SOURCES),
    "vision_src": setup.source_hash(setup.VISION_SOURCES),
}
(engine / "BUILD.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
print(f"  engine: {meta}", flush=True)
PYEOF

# The runtime tree: the application, the two executables and the llama.cpp checkout its
# GGUF tools read through STRATA_GGUF_PY. Every part setup.py's ENGINE_SOURCES names must be
# here byte for byte: source_hash() fingerprints them at the first start and a missing one
# makes the engine look stale, which sends setup.py to a compile it cannot do (no CUDA
# Toolkit in the env) and then to an apt/sudo prompt. `cmake/` is only used at build time and
# is the one that was easy to forget -- it is read for no other reason than this hash.
#
# `strata_control/` is not a build-time part but is just as fatal to leave out: 0.2 imports it
# from setup.py itself (`acquisition`, `tls`, `atomic`, `draft_layer`, `dsv41_context`,
# `engine_component`, `user_args`) and from serve/server.py (`engine_component`, `tls`,
# `hipblaslt_tuning`, `vision_reader`, `draft_layer`, `drafting`, `user_args`, `gpu_roles`), so
# a tree without it cannot even be loaded. It is stdlib-only, so it needs no dependency here.
# The three requirements files are what setup.py reads to build its own package list -- the
# wrapper turns that list into a check of this env instead of a pip install.
APPDIR="${PREFIX}/opt/strata"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/third_party"
for item in serve tools data src include cmake strata_control CMakeLists.txt setup.py \
    requirements.txt requirements-build.txt requirements-monitor.txt; do
    cp -a "${STRATA_SRC}/${item}" "${APPDIR}/${item}"
done
cp -a "${STRATA_SRC}/third_party/ggml" "${APPDIR}/third_party/ggml"
# 0.2.14 added Strata's own prompt kernels, a second ggml tree that ENGINE_SOURCES names:
# leaving it out makes source_hash drift and the first start offer a compile it cannot do.
cp -a "${STRATA_SRC}/third_party/strata-ggml" "${APPDIR}/third_party/strata-ggml"
# The image encoder (VISION_SOURCES) links Strata's own llama tree beside the ggml one.
cp -a "${STRATA_SRC}/third_party/strata-llama" "${APPDIR}/third_party/strata-llama"
rm -rf "${APPDIR}/tools/vision/build"
# get_llama_cpp() returns this tree instead of downloading the archive at the first start.
# The .git directory of the source clone is not part of it (it would be a few hundred MB).
cp -a "${LLAMA_SRC}" "${APPDIR}/third_party/llama.cpp"
rm -rf "${APPDIR}/third_party/llama.cpp/.git"
cp -a "${STRATA_SRC}/engine" "${APPDIR}/engine"
chmod +x "${APPDIR}/engine/strata" "${APPDIR}/engine/strata-vision"

# The build trees are throwaway: the compiled engine is copied out above, and a rebuild
# starts by hashing the sources anyway. Dropping them here keeps the package ~10 GB
# lighter, which matters on a machine whose model files already fill the disk.
rm -rf "${STRATA_SRC}/build" "${STRATA_SRC}/build-vision"

echo "strata installed to ${APPDIR}"
