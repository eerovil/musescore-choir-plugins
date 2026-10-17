#!/usr/bin/env bash
# Install homr (optical music recognition) into a venv the song app can call.
#
# homr does not go into the app's own .venv on purpose. The unattended deploy
# reinstalls pip-requirements.txt on every merge, and homr is ~660 MB of
# onnxruntime/opencv wheels plus ~150 MB of model weights that it stores inside
# its own site-packages — so a reinstall there would churn the weights and a
# resolution failure would take the live app down. It lives in its own venv,
# outside the checkout, so a fresh clone or a deploy never touches it.
#
# Idempotent: re-running upgrades homr in place and re-downloads only whatever
# weights are missing.
#
#   scripts/install-homr.sh                 # default location
#   HOMR_VENV=/somewhere/else scripts/install-homr.sh
#   scripts/install-homr.sh --status        # installed vs the fork's main; installs nothing
#   HOMR_ORT_WHEEL=/path/onnxruntime_gpu-*.whl scripts/install-homr.sh   # read on the GPU
#
# HOMR_ORT_WHEEL swaps homr's CPU onnxruntime for a GPU one built for this
# host's GTX 970 (scripts/onnxruntime-sm52/build.sh; issue #418). The stock GPU
# wheels start at compute 6.0 and the card is 5.2 (#93). The wheel's path is
# kept in the venv's marker, so a later plain run (the app's Update button)
# puts it back rather than quietly going back to the CPU.
#
# The song app's Library page runs this same script behind its Install / Update
# homr button (src/song_app/homr_install.py), so a phone can update homr too.
#
# This is the ONLY install. A branch of the fork is not installed at all: the
# app runs a local working copy of it straight from source, borrowing this
# venv's dependencies (see src/song_app/omr.py, "engines"). Editing a branch and
# reading a page again is then a loop with no install in it.
#
# Afterwards, point the app at it if you used a non-default location:
#   HOMR_BIN=/somewhere/else/bin/homr   in .env

set -euo pipefail

# Where homr comes from. ONE PLACE, deliberately. It used to be the immutable
# v0.7.0 commit; it now follows the fork's `main`, which is upstream's own tip.
# The trade is deliberate and worth naming: an install is no longer reproducible
# from this file alone — two hosts set up a month apart get different OMR, and
# so does one host reinstalled — in exchange for picking up upstream's fixes
# without a commit hash to move by hand each time. Nothing installs this
# automatically (the deploy never touches the homr venv), so the day it changes
# is a day somebody ran this script, and running it is when the frozen benchmark
# should be run again. An explicit HOMR_SOURCE still overrides it: pass a commit
# to get an old parse back.
#
# The [cpu] extra is now load-bearing and was not on 0.7.0: upstream has moved
# onnxruntime out of the base dependencies into cpu/cuda/rocm extras, so a plain
# install of `main` produces a homr with no inference runtime at all. It fails at
# the first parse, not at install time. The stock GPU builds cannot run on this
# host's GTX 970 (issue #93: sm_52, below their sm_60 floor); HOMR_ORT_WHEEL is
# the way round that (#418).
HOMR_REPO="${HOMR_REPO:-https://github.com/eerovil/homr.git}"

DEFAULT_VENV="$HOME/.local/share/musescore-choir-plugins/homr-venv"

# `main` is a label a person recognises; a hand-written HOMR_SOURCE is not one,
# so an install made from an explicit source is labelled by that source instead.
if [ -z "${HOMR_SOURCE:-}" ]; then
    HOMR_SOURCE="homr[cpu] @ git+$HOMR_REPO@main"
    HOMR_LABEL="main"
else
    HOMR_LABEL=""
fi

# homr declares >=3.11,<3.16, but every benchmark number recorded for this
# project came off 3.12. uv fetches the interpreter, so this costs nothing.
HOMR_PYTHON="${HOMR_PYTHON:-3.12}"
HOMR_VENV="${HOMR_VENV:-$DEFAULT_VENV}"

if [ "${1:-}" = "--status" ]; then
    # The same two facts the app's homr box shows, for someone at a shell.
    installed=none
    for info in "$HOMR_VENV"/lib/python3.*/site-packages/homr-*.dist-info/direct_url.json; do
        [ -f "$info" ] || continue
        installed=$(grep -o '"commit_id": *"[0-9a-f]*"' "$info" | grep -o '[0-9a-f]\{40\}' || true)
        installed=${installed:-unknown}
    done
    latest=$(git ls-remote "$HOMR_REPO" refs/heads/main 2>/dev/null | cut -f1)
    echo "installed=$installed"
    echo "latest=${latest:-unreachable}"
    exit 0
fi

# uv is where the installer gets python and the wheels from. A fresh host
# usually lacks it, and that was the one step that stopped this script cold, so
# fetch it with astral's own installer into ~/.local/bin rather than give up.
# The app's service has a bare PATH, hence the prepend even when uv is there.
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/" >&2
        exit 1
    fi
    echo "uv not found — installing it into ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
    if ! command -v uv >/dev/null 2>&1; then
        echo "uv's installer ran but uv is still not on PATH." >&2
        exit 1
    fi
fi

# A GPU onnxruntime chosen on an earlier run stays chosen: the app's Update
# button runs this script with no environment of its own.
if [ -z "${HOMR_ORT_WHEEL:-}" ] && [ -f "$HOMR_VENV/homr-engine.txt" ]; then
    HOMR_ORT_WHEEL=$(sed -n 's/^ort_wheel=//p' "$HOMR_VENV/homr-engine.txt")
fi
if [ -n "${HOMR_ORT_WHEEL:-}" ] && [ ! -f "$HOMR_ORT_WHEEL" ]; then
    echo "HOMR_ORT_WHEEL is not a file: $HOMR_ORT_WHEEL" >&2
    echo "Build it with scripts/onnxruntime-sm52/build.sh, or unset HOMR_ORT_WHEEL" >&2
    echo "(and remove the ort_wheel line from $HOMR_VENV/homr-engine.txt) to read on the CPU." >&2
    exit 1
fi

echo "Creating $HOMR_VENV (python $HOMR_PYTHON)"
mkdir -p "$(dirname "$HOMR_VENV")"
# --allow-existing rather than --clear: re-running this after the source moved
# must not throw away the 150 MB of weights already downloaded.
uv venv --allow-existing --python "$HOMR_PYTHON" "$HOMR_VENV"

echo "Installing $HOMR_SOURCE"
VIRTUAL_ENV="$HOMR_VENV" uv pip install "$HOMR_SOURCE"

if [ -n "${HOMR_ORT_WHEEL:-}" ]; then
    # Both packages write the same onnxruntime/ directory, so both go before
    # the GPU one is put in whole. The CUDA libraries are pinned to what the
    # wheel was built and tested against: cuDNN 9.10.2 is the last NVIDIA
    # recommends for cards older than Volta.
    echo "Installing the GPU onnxruntime $HOMR_ORT_WHEEL"
    VIRTUAL_ENV="$HOMR_VENV" uv pip uninstall onnxruntime onnxruntime-gpu || true
    VIRTUAL_ENV="$HOMR_VENV" uv pip install --reinstall "$HOMR_ORT_WHEEL" \
        'nvidia-cuda-runtime-cu12==12.8.*' 'nvidia-cuda-nvrtc-cu12==12.8.*' \
        'nvidia-cublas-cu12==12.8.*' 'nvidia-cufft-cu12==11.3.3.*' \
        'nvidia-curand-cu12==10.*' 'nvidia-cudnn-cu12==9.10.2.*'
    # Newer glibc refuses to load a library that asks for an executable stack,
    # and onnxruntime's does.
    python3 "$(dirname "$0")/onnxruntime-sm52/clear_execstack.py" \
        "$HOMR_VENV"/lib/python3.*/site-packages/onnxruntime/capi/*.so
    # A locally built wheel looks for the CUDA runtime by bare name only and,
    # not finding it, switches the GPU off for the whole run without a word.
    # This one looks in the nvidia wheel installed beside it first.
    for capi in "$HOMR_VENV"/lib/python3.*/site-packages/onnxruntime/capi; do
        [ -d "$capi" ] || continue
        cp "$(dirname "$0")/onnxruntime-sm52/_ld_preload.py" "$capi/_ld_preload.py"
    done
fi

# What this venv is, in a file: the app labels the default engine with it, and a
# host reinstalled a month later can say what it got. Written after the install,
# so a venv that failed to build is not labelled as a working engine.
echo "source=$HOMR_SOURCE" > "$HOMR_VENV/homr-engine.txt"
if [ -n "$HOMR_LABEL" ]; then
    echo "branch=$HOMR_LABEL" >> "$HOMR_VENV/homr-engine.txt"
fi
if [ -n "${HOMR_ORT_WHEEL:-}" ]; then
    # src/song_app/omr.py passes --gpu force only when this line is here.
    echo "gpu=force" >> "$HOMR_VENV/homr-engine.txt"
    echo "ort_wheel=$HOMR_ORT_WHEEL" >> "$HOMR_VENV/homr-engine.txt"
fi

# Pull the model weights now rather than on the first parse a person is waiting
# for. ~150 MB, into the homr package directory inside the venv.
echo "Downloading model weights (once; ~150 MB)"
if [ -n "${HOMR_ORT_WHEEL:-}" ]; then
    # The GPU route reads with homr's fp16 models, a second set of weights.
    "$HOMR_VENV/bin/homr" --init --gpu force
else
    "$HOMR_VENV/bin/homr" --init
fi

echo
echo "homr installed: $HOMR_VENV/bin/homr"
if [ "$HOMR_VENV" != "$DEFAULT_VENV" ]; then
    # Only a venv in the usual place is found by the app's engine list, so one
    # put elsewhere still has to be pointed at by hand.
    echo "Non-default location — add to .env:"
    echo "  HOMR_BIN=$HOMR_VENV/bin/homr"
fi
echo "A branch of the fork needs no install: check it out under \$HOMR_CHECKOUT"
echo "(default ~/homr, git worktrees included) and pick it in the Scan panel."
