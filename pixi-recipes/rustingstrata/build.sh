#!/usr/bin/env bash
set -euo pipefail

# RustingStrata publishes no Linux binary, so build the pinned release with the
# cargo from the recipe's build environment. CUDA kernels are NVRTC-compiled at
# run time; there is no GPU, CUDA toolkit or C++ engine build to do here.

SRC="${SRC_DIR}/rustingstrata"
if [ ! -f "${SRC}/Cargo.toml" ]; then
    SRC="${SRC_DIR}"
fi
if [ ! -f "${SRC}/Cargo.toml" ] || [ ! -f "${SRC}/Cargo.lock" ]; then
    echo "expected the RustingStrata Cargo sources under ${SRC_DIR}" >&2
    exit 1
fi
cd "${SRC}"

# Keep cargo's caches inside the throwaway source tree: rattler-build redirects
# HOME, and the agent sandbox mounts the host's real $HOME as tmpfs.
export CARGO_HOME="${SRC}/.cargo-home"
mkdir -p "${CARGO_HOME}"

cargo build --release --locked --bin rustingstrata
mkdir -p "${PREFIX}/bin"
install -m 755 "${SRC}/target/release/rustingstrata" "${PREFIX}/bin/rustingstrata"

"${PREFIX}/bin/rustingstrata" --version
