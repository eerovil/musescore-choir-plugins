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
# the first parse, not at install time. CPU is the only one this host can use
# (issue #93: the GTX 970 is sm_52, below onnxruntime's sm_60 floor).
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

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/" >&2
    exit 1
fi

echo "Creating $HOMR_VENV (python $HOMR_PYTHON)"
mkdir -p "$(dirname "$HOMR_VENV")"
# --allow-existing rather than --clear: re-running this after the source moved
# must not throw away the 150 MB of weights already downloaded.
uv venv --allow-existing --python "$HOMR_PYTHON" "$HOMR_VENV"

echo "Installing $HOMR_SOURCE"
VIRTUAL_ENV="$HOMR_VENV" uv pip install "$HOMR_SOURCE"

# What this venv is, in a file: the app labels the default engine with it, and a
# host reinstalled a month later can say what it got. Written after the install,
# so a venv that failed to build is not labelled as a working engine.
echo "source=$HOMR_SOURCE" > "$HOMR_VENV/homr-engine.txt"
if [ -n "$HOMR_LABEL" ]; then
    echo "branch=$HOMR_LABEL" >> "$HOMR_VENV/homr-engine.txt"
fi

# Pull the model weights now rather than on the first parse a person is waiting
# for. ~150 MB, into the homr package directory inside the venv.
echo "Downloading model weights (once; ~150 MB)"
"$HOMR_VENV/bin/homr" --init

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
