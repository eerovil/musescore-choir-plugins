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
#   scripts/install-homr.sh                             # the default engine
#   HOMR_BRANCH=prototype/system-4 scripts/install-homr.sh   # beside it, a fork branch
#   HOMR_VENV=/somewhere/else scripts/install-homr.sh
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

# A branch of the fork, installed BESIDE the default rather than over it. A
# branch is where a homr change is tried out (prototype/system-4, say), and the
# question it exists to answer is "does this read the page better than what we
# have?" — which cannot be answered by an install that replaced the thing it is
# being compared against. So a branch gets a venv of its own, named after it,
# and the app offers both as engines to scan with (omr.engines). Unset means the
# default engine, and the default engine is `main`.
HOMR_BRANCH="${HOMR_BRANCH:-}"

DEFAULT_VENV="$HOME/.local/share/musescore-choir-plugins/homr-venv"
if [ -n "$HOMR_BRANCH" ]; then
    # prototype/system-4 -> homr-venv-prototype-system-4. A directory name is
    # not a branch name, so the app reads the branch back out of the marker
    # file below rather than trying to undo this.
    DEFAULT_VENV="$DEFAULT_VENV-${HOMR_BRANCH//[^A-Za-z0-9]/-}"
fi

# A branch is a label a person recognises; a hand-written HOMR_SOURCE is not one,
# so an install made from an explicit source is labelled by that source instead.
if [ -z "${HOMR_SOURCE:-}" ]; then
    HOMR_SOURCE="homr[cpu] @ git+$HOMR_REPO@${HOMR_BRANCH:-main}"
    HOMR_LABEL="${HOMR_BRANCH:-main}"
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

# What this venv is, in a file, because a directory name cannot say it. The app
# lists the installed engines and has to label them with something a person
# recognises — the branch, not `homr-venv-prototype-system-4`. Written after the
# install, so a venv that failed to build is not labelled as a working engine.
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
elif [ -n "$HOMR_BRANCH" ]; then
    echo "Offered in the Scan panel as: $HOMR_BRANCH"
fi
