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
if [ ! -f "${WORK}/strata/setup.py" ] && [ ! -f "${WORK}/strata/terminal/setup.py" ]; then
    WORK="$(cd "${SRC_DIR}/.." && pwd)"
fi
STRATA_SRC="${WORK}/strata"
# 0.2.16 moved the Terminal (setup.py and the Python app) into terminal/ and the engine
# sources into engine/. The source hash and the installed engine folder still follow
# setup.py's own ROOT/engine_src(), so never hardcode either one here.
if [ -f "${STRATA_SRC}/setup.py" ]; then
    SETUP_DIR="${STRATA_SRC}"
elif [ -f "${STRATA_SRC}/terminal/setup.py" ]; then
    SETUP_DIR="${STRATA_SRC}/terminal"
else
    echo "expected Strata's setup.py in ${STRATA_SRC} or ${STRATA_SRC}/terminal" >&2
    exit 1
fi
LLAMA_SRC="${WORK}/llama.cpp"
if [ ! -d "${LLAMA_SRC}/ggml" ]; then
    echo "expected the llama.cpp sources in ${WORK}" >&2
    exit 1
fi
export STRATA_SRC SETUP_DIR LLAMA_SRC
export CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES:-86}"

python - <<'PYEOF'
import json
import os
import pathlib
import shutil
import sys

sys.path.insert(0, os.environ["SETUP_DIR"])
import setup  # noqa: E402  Strata's own build driver, so the flags cannot drift from upstream

if setup.LLAMA_CPP_COMMIT != os.environ["LLAMA_COMMIT"]:
    sys.exit(
        f"llama_cpp_commit in recipe.yaml ({os.environ['LLAMA_COMMIT']}) does not match "
        f"this Strata tag's LLAMA_CPP_COMMIT ({setup.LLAMA_CPP_COMMIT})"
    )

# "86" / "86;89" / "89,120" -> CMake's semicolon-separated form
archs = [a.strip() for a in os.environ["CUDA_ARCHITECTURES"].replace(",", ";").split(";") if a.strip()]
if not archs:
    sys.exit("CUDA_ARCHITECTURES is empty")
cuda_archs = ";".join(archs)

nvcc, version = setup.find_nvcc()
if nvcc is None:
    sys.exit("no nvcc in the build environment (the recipe's cuda-nvcc build requirement)")
print(f"  nvcc {nvcc} (CUDA {version[0]}.{version[1]}), architectures {cuda_archs}", flush=True)

# Every definition setup.py's own build_engine() adds, so the engine this recipe compiles is
# the engine upstream compiles: cmake_build alone is only the build *function* -- the
# definition list belongs to upstream's caller, and it is not derivable from the two flags
# this script used to carry. ggml_defs/vision_llama were upstream's accessors; since 0.2.16
# the engine builds from the vendored third_party/strata-ggml by default and the picture
# reader is Strata's own tower (no -DLLAMA_DIR), so neither override is passed.
# engine_defs is the one that changes the binary: -DSTRATA_EXPERIMENTAL_SM60=ON for a card
# below sm_75 or a CUDA 12 toolkit -- without it a Pascal engine cannot run. The toolkit
# major comes from the nvcc that builds, not from the recipe's cuda-version pin.
# arch_nums is the same digits BUILD.json records; an architecture name CMake spells with a
# suffix ("90a", "compute_90") cannot be a Pascal card, so it skips the floor check.
arch_nums = [int(a.split("-")[0]) for a in archs if a.split("-")[0].isdigit()]
toolkit = int(version[0])

# Two of upstream's def helpers are deliberately NOT called, because both are about the
# machine that runs setup.py rather than about this package:
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
    "-DSTRATA_Q6K_EXPERTS=ON",
    f"-DCMAKE_CUDA_ARCHITECTURES={cuda_archs}",
    f"-DCMAKE_CUDA_COMPILER={nvcc}",
    *setup.host_compiler_defs(True),
    *(setup.engine_defs(arch_nums, toolkit) if arch_nums else []),
]
setup.cmake_build(
    setup.engine_src(),
    setup.ROOT / "build",
    "strata",
    engine_defs,
    None,
    "build-strata.bat",
)
# Upstream's vision defs: Strata's own tower, the same host compilers (it says "native,
# like the engine"), and STRATA_PORTABLE=OFF, which the engine build gets from its default.
vision_defs = [
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

# BUILD.json is what setup.py reads at the first start to decide whether the engine is
# current: source=local plus a matching src hash means it compiles nothing again, so the
# runtime never needs a compiler. `cuda_dirs` is the directory the engine's shared
# libraries are found in *after* installation -- rattler-build rewrites the prefix in this
# file, and the server prepends the result to LD_LIBRARY_PATH when it starts the engine.
# The stamp lives beside the binaries in setup.engine_dir(); source_hash() reads the
# sources under setup.engine_src(). Since 0.2.16 those are sibling trees.
eng = setup.ROOT / "engine"
eng.mkdir(exist_ok=True)
shutil.copy2(setup.ROOT / "build" / setup.EXE, eng / setup.EXE)
shutil.copy2(setup.ROOT / "build-vision" / "bin" / setup.VEXE, eng / setup.VEXE)
# libstrata_kq.so: llama.cpp's MMQ prompt instances kept in a library of their own so the
# engine does not pay VRAM for them at every start; the loader looks beside the executable.
# Normally ABSENT since 0.2.14 (STRATA_MMQ defaults OFF, the CUDA prompt path is Strata's
# own); the copy stays for an A/B build where the loader wants it.
kq = setup.ROOT / "build" / setup.KQ_LIB
if kq.is_file():
    shutil.copy2(kq, eng / setup.KQ_LIB)
    print(f"  K-quant prompt kernels: {setup.KQ_LIB}", flush=True)
    # No stale MMQ library: an older one would be refused at load time.
else:
    (eng / setup.KQ_LIB).unlink(missing_ok=True)

meta = {
    "source": "local",
    "version": setup.source_version(),
    "archs": arch_nums,
    "vision": "gpu",
    "cuda_dirs": [str(pathlib.Path(os.environ["PREFIX"]) / "lib")],
    "src": setup.source_hash(setup.ENGINE_SOURCES),
    "vision_src": setup.source_hash(setup.VISION_SOURCES),
}
(eng / "BUILD.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
print(f"  engine: {meta}", flush=True)
PYEOF

# The runtime tree mirrors the 0.2.16 checkout: terminal/ holds what setup.py imports and
# writes (run configs, logs), engine/ holds the sources setup.py hashes at the first start.
# Every part ENGINE_SOURCES names must be here byte for byte: source_hash() fingerprints
# them at the first start and a missing one makes the engine look stale, which sends setup.py
# to a compile it cannot do (no CUDA Toolkit in the env) and then to an apt/sudo prompt.
# VISION_SOURCES adds terminal/tools/vision and engine/third_party/stb; the preparation
# tools use engine/third_party/strata-gguf-py. `strata_control/` is imported by setup.py and
# serve/server.py at module level, so an app tree without it cannot even be loaded.
# The three requirements files are what setup.py reads to build its own package list -- the
# wrapper turns that list into a check of this env instead of a pip install.
APPDIR="${PREFIX}/opt/strata"
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/terminal" "${APPDIR}/engine"
cp -a "${SETUP_DIR}/." "${APPDIR}/terminal/"
cp -a "${WORK}/strata/engine/." "${APPDIR}/engine/"
# The build trees and the test/benchmark sources are not hashed and not shipped.
rm -rf "${APPDIR}/terminal/build" "${APPDIR}/terminal/build-vision" \
    "${APPDIR}/terminal/tests" "${APPDIR}/terminal/bench" "${APPDIR}/terminal/ref" \
    "${APPDIR}/engine/tests" "${APPDIR}/engine/bench" "${APPDIR}/engine/spark" \
    "${APPDIR}/engine/metal" "${APPDIR}/engine/sycl"
chmod +x "${APPDIR}/terminal/engine/strata" "${APPDIR}/terminal/engine/strata-vision"

rm -rf "${SETUP_DIR}/build" "${SETUP_DIR}/build-vision"

echo "strata installed to ${APPDIR}"
