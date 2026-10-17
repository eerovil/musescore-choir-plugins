#!/usr/bin/env bash
# Build an onnxruntime GPU wheel this host's GTX 970 can run (issue #418).
#
# The card is compute 5.2 (Maxwell). onnxruntime's own GPU wheels start at 6.0,
# so they register CUDA and then fail on the first kernel (#93), and CUDA 13 has
# dropped Maxwell altogether. This builds the same onnxruntime version homr
# installs (1.29.0) against CUDA 12.8 for compute 5.2 only, in an NVIDIA CUDA
# container, so the host needs nothing but podman. patch.py says what had to
# change in onnxruntime's source and why.
#
#   scripts/onnxruntime-sm52/build.sh
#   ORT_SM52_DIR=/var/mnt/ssd/ort-sm52 scripts/onnxruntime-sm52/build.sh
#
# The work directory needs ~25 GB (the container image, the source and the
# build tree); pick a disk with room. A first build is about two hours on four
# cores; re-running it continues where it stopped. The wheel lands in
# $ORT_SM52_DIR/dist/ and is installed with:
#
#   HOMR_ORT_WHEEL=$ORT_SM52_DIR/dist/onnxruntime_gpu-1.29.0-cp312-cp312-linux_x86_64.whl \
#       scripts/install-homr.sh
#
# Driver 580 is the last NVIDIA driver line that supports Maxwell, so this
# lasts as long as the host stays on it.

set -euo pipefail

ORT_VERSION="${ORT_VERSION:-1.29.0}"
W="${ORT_SM52_DIR:-$HOME/.local/share/musescore-choir-plugins/onnxruntime-sm52}"
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG=docker.io/nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04
# Its own podman storage, beside the build, so the 8 GB image lands on the
# same disk as everything else rather than in the home directory.
ROOT="$W/podman-root"

mkdir -p "$W/dist"
if [ ! -d "$W/src/.git" ]; then
    git clone --depth 1 --branch "v$ORT_VERSION" https://github.com/microsoft/onnxruntime "$W/src"
fi
python3 "$HERE/patch.py" "$W/src"

podman --root "$ROOT" pull -q "$IMG"
podman --root "$ROOT" run --rm -v "$W/src:/src:Z" -w /src "$IMG" bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3 python3-dev python3-venv python3-pip git >/dev/null
python3 -m venv /venv && . /venv/bin/activate
pip install -q cmake ninja numpy packaging wheel setuptools psutil
# Compute 5.2 only. Flash and memory-efficient attention need newer cards;
# warnings are not errors because code only compiled with those two off has
# unused variables in it.
ARGS="--config Release --build_wheel --parallel 4 --nvcc_threads 1 \
  --use_cuda --cuda_version 12.8 --cuda_home /usr/local/cuda --cudnn_home /usr \
  --skip_tests --compile_no_warning_as_error --allow_running_as_root --skip_submodule_sync \
  --cmake_generator Ninja \
  --cmake_extra_defines CMAKE_CUDA_ARCHITECTURES=52 \
    onnxruntime_USE_FLASH_ATTENTION=OFF \
    onnxruntime_USE_MEMORY_EFFICIENT_ATTENTION=OFF \
    onnxruntime_BUILD_UNIT_TESTS=OFF"
./build.sh $ARGS --update
./build.sh $ARGS --build
# A symbol left undefined only shows when the library is loaded, and then
# onnxruntime falls back to the CPU without saying why, so refuse it here.
if nm -D --undefined-only build/Linux/Release/libonnxruntime_providers_cuda.so | grep "11onnxruntime" >&2; then
  echo "libonnxruntime_providers_cuda.so has undefined onnxruntime symbols (above)." >&2
  exit 1
fi
'
cp "$W"/src/build/Linux/Release/dist/onnxruntime_gpu-*.whl "$W/dist/"
ls -l "$W"/dist/onnxruntime_gpu-*.whl
