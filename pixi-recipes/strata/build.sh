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
setup.cmake_build(
    setup.ROOT,
    setup.ROOT / "build",
    "strata",
    [
        "-DSTRATA_ENABLE_CUDA=ON",
        "-DSTRATA_BUILD_TESTS=OFF",
        f"-DCMAKE_CUDA_ARCHITECTURES={cuda_archs}",
        f"-DCMAKE_CUDA_COMPILER={nvcc}",
        f"-DSTRATA_GGML_DIR={llama}",
    ],
    None,
    "build-strata.bat",
)
setup.cmake_build(
    setup.ROOT / "tools" / "vision",
    setup.ROOT / "build-vision",
    "strata-vision",
    [
        f"-DLLAMA_DIR={llama}",
        "-DSTRATA_VISION_CUDA=ON",
        f"-DCMAKE_CUDA_ARCHITECTURES={cuda_archs}",
        f"-DCMAKE_CUDA_COMPILER={nvcc}",
    ],
    None,
    "build-vision.bat",
)

engine = setup.ROOT / "engine"
engine.mkdir(exist_ok=True)
shutil.copy2(setup.ROOT / "build" / setup.EXE, engine / setup.EXE)
shutil.copy2(setup.ROOT / "build-vision" / "bin" / setup.VEXE, engine / setup.VEXE)

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
# GGUF tools read through STRATA_GGUF_PY. src/, include/ and third_party/ggml are not dead
# weight -- setup.py's source_hash() reads exactly those to verify BUILD.json.
APPDIR="${PREFIX}/opt/strata"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/third_party"
for item in serve tools data src include CMakeLists.txt setup.py requirements.txt; do
    cp -a "${STRATA_SRC}/${item}" "${APPDIR}/${item}"
done
cp -a "${STRATA_SRC}/third_party/ggml" "${APPDIR}/third_party/ggml"
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
