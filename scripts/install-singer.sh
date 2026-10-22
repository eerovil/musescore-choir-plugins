#!/usr/bin/env bash
# Install the singer sing_song.py runs (src/sing_song/sing_ufr.py) into a venv of its own.
#
# On the processor by default; with an NVIDIA card it installs the CUDA 11 build of
# onnxruntime, which also runs on old cards (a GTX 970 is compute 5.2, below the newer
# builds' floor), and sing_song.py then sings on the card. English songs need g2p_en,
# installed here too.
#
#   scripts/install-singer.sh                    # ~/.local/share/musescore-choir-plugins/sing-venv
#   SING_VENV=/somewhere/else scripts/install-singer.sh   # or SING_VENV=... in .env
#   SING_GPU=0 scripts/install-singer.sh         # the processor even with a card (1: the card)
#
# The voices are not installed: download the UFR DiffSinger pack yourself
# (https://utaufrance.com/telechargement-du-pack-diffsinger-ufr/; its terms forbid
# re-hosting it) and set UFR_DIR in .env to the folder with core/, dsdur/ and tiger/.
set -euo pipefail

# an exported SING_VENV wins, then the one in .env (sing_song.py reads the same), then the default
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${SING_VENV:-}" ] && [ -f "$ROOT/.env" ]; then
    SING_VENV=$(sed -n 's/^SING_VENV=//p' "$ROOT/.env" | tail -1 | sed "s/^[\"']//; s/[\"']\$//")
fi
SING_VENV="${SING_VENV:-$HOME/.local/share/musescore-choir-plugins/sing-venv}"
SING_VENV="${SING_VENV/#\~/$HOME}"

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "uv is not installed. See https://docs.astral.sh/uv/getting-started/" >&2
        exit 1
    fi
    echo "uv not found — installing it into ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
fi

if [ -z "${SING_GPU:-}" ]; then
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then SING_GPU=1; else SING_GPU=0; fi
fi

# 3.12: the CUDA 11 onnxruntime has no wheels for later pythons.
uv venv --allow-existing --python 3.12 "$SING_VENV"
common=(numpy==1.26.4 soundfile==0.14.0 g2p_en==2.1.0)
if [ "$SING_GPU" = 1 ]; then
    echo "An NVIDIA card: installing the CUDA 11 onnxruntime"
    VIRTUAL_ENV="$SING_VENV" uv pip uninstall onnxruntime >/dev/null 2>&1 || true
    VIRTUAL_ENV="$SING_VENV" uv pip install "${common[@]}" onnxruntime-gpu==1.17.1 \
        nvidia-cudnn-cu11==8.9.6.50 nvidia-cuda-runtime-cu11==11.8.89 nvidia-cuda-nvrtc-cu11==11.8.89 \
        nvidia-cublas-cu11==11.11.3.6 nvidia-cufft-cu11==10.9.0.58 nvidia-curand-cu11==10.3.0.86
else
    echo "No NVIDIA card (or SING_GPU=0): installing onnxruntime for the processor"
    VIRTUAL_ENV="$SING_VENV" uv pip uninstall onnxruntime-gpu nvidia-cudnn-cu11 nvidia-cuda-runtime-cu11 \
        nvidia-cuda-nvrtc-cu11 nvidia-cublas-cu11 nvidia-cufft-cu11 nvidia-curand-cu11 >/dev/null 2>&1 || true
    VIRTUAL_ENV="$SING_VENV" uv pip install "${common[@]}" onnxruntime==1.20.1
fi
# g2p_en fetches its word lists (nltk data, into ~/nltk_data) the first time it is imported.
"$SING_VENV/bin/python" -c "import g2p_en" >/dev/null
echo "Singer installed in $SING_VENV ($([ "$SING_GPU" = 1 ] && echo 'graphics card' || echo processor))."
