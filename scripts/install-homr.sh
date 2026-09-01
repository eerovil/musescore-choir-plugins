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
# Idempotent: re-running upgrades the pin in place and re-downloads only
# whatever weights are missing.
#
#   scripts/install-homr.sh                 # default location
#   HOMR_VENV=/somewhere/else scripts/install-homr.sh
#
# Afterwards, point the app at it if you used a non-default location:
#   HOMR_BIN=/somewhere/else/bin/homr   in .env

set -euo pipefail

# Where homr comes from. ONE PLACE, deliberately: issue #97 decided to fork
# homr and fix multi-page joining, staff grouping and PDF input inside the
# fork, because by the time the app sees homr's output the staves are gone. So
# this line becomes a git URL once #104 stands the fork up —
#   "git+https://github.com/<owner>/homr@<ref>"
# — and nothing else in this script or in src/song_app/omr.py changes with it.
HOMR_SOURCE="${HOMR_SOURCE:-homr==0.7.0}"

# homr 0.7.0 declares >=3.11,<3.16, but every benchmark number recorded for this
# project came off 3.12. uv fetches the interpreter, so this costs nothing.
HOMR_PYTHON="${HOMR_PYTHON:-3.12}"
HOMR_VENV="${HOMR_VENV:-$HOME/.local/share/musescore-choir-plugins/homr-venv}"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/" >&2
    exit 1
fi

echo "Creating $HOMR_VENV (python $HOMR_PYTHON)"
mkdir -p "$(dirname "$HOMR_VENV")"
# --allow-existing rather than --clear: re-running this to move HOMR_SOURCE on
# to the fork must not throw away the 150 MB of weights already downloaded.
uv venv --allow-existing --python "$HOMR_PYTHON" "$HOMR_VENV"

# There is no [cpu] extra on 0.7.0 despite what upstream's README suggests —
# uv warns and ignores it. Plain homr pulls the CPU onnxruntime, which is all
# this host can use anyway (see issue #93: the GTX 970 is sm_52, below
# onnxruntime's sm_60 floor).
echo "Installing $HOMR_SOURCE"
VIRTUAL_ENV="$HOMR_VENV" uv pip install "$HOMR_SOURCE"

# Pull the model weights now rather than on the first parse a person is waiting
# for. ~150 MB, into the homr package directory inside the venv.
echo "Downloading model weights (once; ~150 MB)"
"$HOMR_VENV/bin/homr" --init

echo
echo "homr installed: $HOMR_VENV/bin/homr"
if [ "$HOMR_VENV" != "$HOME/.local/share/musescore-choir-plugins/homr-venv" ]; then
    echo "Non-default location — add to .env:"
    echo "  HOMR_BIN=$HOMR_VENV/bin/homr"
fi
