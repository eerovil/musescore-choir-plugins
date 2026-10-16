#!/usr/bin/env bash
# Set this checkout up to run the song app, on Linux or macOS (#408).
#
#   scripts/setup.sh            # asks before downloading anything big
#   scripts/setup.sh --yes      # says yes to every question (homr included)
#   scripts/setup.sh --yes --no-homr
#
# Safe to run again: it only does what is still missing, and never changes a
# value already set in .env. It never runs sudo; a system package it cannot
# install itself (poppler) is named with the command to install it.
#
# What it does, in order:
#   1. .venv with Python 3.11 or newer, and pip-requirements.txt in it
#   2. MuseScore 3: finds it, or (Linux) downloads the official 3.6.2 AppImage
#      into ~/.local/bin
#   3. poppler (pdftoppm, pdfinfo): checks, and says how to install it
#   4. .env from .env.default, with the MuseScore path filled in
#   5. homr, which reads scanned scores (optional, ~10 minutes, ~660 MB)
#   6. ./song.py --check, which says what works
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

YES=0
HOMR=ask
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --no-homr) HOMR=no ;;
    --homr) HOMR=yes ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

MUSESCORE_URL="${MUSESCORE_URL:-https://github.com/musescore/MuseScore/releases/download/v3.6.2/MuseScore-3.6.2.548021370-x86_64.AppImage}"
OS="$(uname -s)"

say() { printf '\n==> %s\n' "$*"; }
ask() {  # ask "question" -> 0 for yes
  if [ "$YES" = 1 ]; then return 0; fi
  if [ ! -t 0 ]; then return 1; fi
  local answer
  read -r -p "$1 [y/N] " answer
  case "$answer" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# --- 1. Python ---------------------------------------------------------------
say "Python"
python=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    python="$(command -v "$candidate")"
    break
  fi
done
if [ ! -x .venv/bin/python ]; then
  if [ -z "$python" ]; then
    echo "Python 3.11 or newer is needed and none was found." >&2
    case "$OS" in
      Darwin) echo "Install it with: brew install python@3.13" >&2 ;;
      *) echo "Install it with your package manager, e.g. dnf install python3 / apt install python3 python3-venv" >&2 ;;
    esac
    exit 1
  fi
  echo "    making .venv with $python"
  "$python" -m venv .venv
fi
.venv/bin/python -m pip install -q -r pip-requirements.txt
echo "    .venv ready ($(.venv/bin/python --version))"

# --- 2. MuseScore 3 ------------------------------------------------------------
say "MuseScore 3"
env_value() {  # the value of $1 in .env, quotes taken off; empty if unset
  [ -f .env ] || return 0
  sed -n "s/^$1=//p" .env | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}
is_musescore3() { "$1" --version 2>/dev/null | grep -Eq 'MuseScore3? *3\.'; }
HEADLESS="$HOME/.local/bin/musescore3-headless"
# Sets $musescore to a MuseScore 3 that answers, or leaves it empty. MuseScore 3's
# Linux build only draws on an X display, even from the command line, so on a
# machine with no screen (a server, a container) it is run inside xvfb-run, through
# a two-line wrapper that MUSESCORE_CLI_PATH then names.
use_if_musescore3() {
  local candidate="$1"
  [ -n "$candidate" ] && [ -x "$candidate" ] || return 1
  if is_musescore3 "$candidate"; then musescore="$candidate"; return 0; fi
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1 \
     && xvfb-run -a "$candidate" --version 2>/dev/null | grep -Eq 'MuseScore3? *3\.'; then
    mkdir -p "$(dirname "$HEADLESS")"
    printf '#!/bin/sh\n# MuseScore 3 on a machine with no screen (scripts/setup.sh).\nexec xvfb-run -a "%s" "$@"\n' \
      "$candidate" > "$HEADLESS"
    chmod +x "$HEADLESS"
    echo "    no screen here: MuseScore runs inside xvfb-run, through $HEADLESS"
    musescore="$HEADLESS"
    return 0
  fi
  return 1
}
why_not() {  # what MuseScore says when it does not start
  echo "What it says:" >&2
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
    xvfb-run -a "$1" --version 2>&1 | tail -5 >&2 || true
  else
    "$1" --version 2>&1 | tail -5 >&2 || true
  fi
  echo "A missing library (\"cannot open shared object file\") is a system package; SETUP.md lists them." >&2
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && ! command -v xvfb-run >/dev/null 2>&1; then
    echo "There is no screen here, and MuseScore 3 needs one: install xvfb-run" >&2
    echo "(dnf install xorg-x11-server-Xvfb, apt install xvfb) and run this again." >&2
  fi
}

musescore=""
for candidate in "${MUSESCORE_CLI_PATH:-}" "$(env_value MUSESCORE_CLI_PATH)" \
    "$(command -v mscore3 || true)" "$(command -v musescore3 || true)" \
    "$(command -v mscore || true)" "$HOME/.local/bin/MuseScore3.AppImage" \
    "$HOME/.local/opt/MuseScore3/AppRun" \
    "/Applications/MuseScore 3.app/Contents/MacOS/mscore"; do
  use_if_musescore3 "$candidate" && break
done

APPIMAGE="$HOME/.local/bin/MuseScore3.AppImage"
if [ -z "$musescore" ]; then
  if [ "$OS" = Darwin ]; then
    echo "MuseScore 3 is not installed. Get MuseScore 3.6.2 for macOS from" >&2
    echo "  https://github.com/musescore/MuseScore/releases/tag/v3.6.2" >&2
    echo "and run this again. MuseScore 4 works too, but this script does not look for it:" >&2
    echo "set MUSESCORE_CLI_PATH in .env to it yourself (it skips one check; see SETUP.md)." >&2
  elif [ "$(uname -m)" != x86_64 ]; then
    echo "MuseScore 3 is not installed, and its AppImage is for x86_64 only." >&2
    echo "Install MuseScore 3 from your distribution, set MUSESCORE_CLI_PATH in .env, and run this again." >&2
  elif [ -x "$APPIMAGE" ] || ask "MuseScore 3 was not found. Download the official MuseScore 3.6.2 AppImage (160 MB) into ~/.local/bin?"; then
    if [ ! -x "$APPIMAGE" ]; then
      mkdir -p "$HOME/.local/bin"
      curl -fL --progress-bar -o "$APPIMAGE.part" "$MUSESCORE_URL"
      mv "$APPIMAGE.part" "$APPIMAGE"
      chmod +x "$APPIMAGE"
    fi
    if ! use_if_musescore3 "$APPIMAGE"; then
      # No FUSE (containers, some desktops): an unpacked AppImage runs without it.
      echo "    the AppImage does not start as it is; unpacking it into ~/.local/opt/MuseScore3"
      rm -rf "$HOME/.local/opt/MuseScore3"
      mkdir -p "$HOME/.local/opt"
      (cd "$HOME/.local/opt" && "$APPIMAGE" --appimage-extract >/dev/null && mv squashfs-root MuseScore3)
      if ! use_if_musescore3 "$HOME/.local/opt/MuseScore3/AppRun"; then
        echo "MuseScore 3 is downloaded but does not start." >&2
        why_not "$HOME/.local/opt/MuseScore3/AppRun"
      fi
    fi
  else
    echo "Skipped. Install MuseScore 3 and set MUSESCORE_CLI_PATH in .env, or run this again." >&2
  fi
fi
[ -n "$musescore" ] && echo "    $musescore ($("$musescore" --version 2>/dev/null | grep -Eo '3\.[0-9.]+' | head -1))"

# --- 3. poppler ------------------------------------------------------------------
say "poppler"
if command -v pdftoppm >/dev/null 2>&1 && command -v pdfinfo >/dev/null 2>&1; then
  echo "    pdftoppm and pdfinfo found"
else
  echo "    not found. Without it there are no page crops, so no scanning. Install it with:"
  if [ "$OS" = Darwin ]; then echo "      brew install poppler"
  elif command -v dnf >/dev/null 2>&1; then echo "      sudo dnf install poppler-utils"
  elif command -v apt-get >/dev/null 2>&1; then echo "      sudo apt install poppler-utils"
  else echo "      your package manager's poppler-utils package"; fi
fi

# --- 4. .env -----------------------------------------------------------------------
say ".env"
if [ ! -f .env ]; then
  cp .env.default .env
  echo "    made .env from .env.default"
fi
if [ -n "$musescore" ] && [ -z "$(env_value MUSESCORE_CLI_PATH)" ]; then
  tmp="$(mktemp)"
  if grep -q '^MUSESCORE_CLI_PATH=' .env; then
    awk -v path="$musescore" '/^MUSESCORE_CLI_PATH=/ { print "MUSESCORE_CLI_PATH=\"" path "\""; next } { print }' .env > "$tmp"
  else
    { cat .env; echo "MUSESCORE_CLI_PATH=\"$musescore\""; } > "$tmp"
  fi
  cat "$tmp" > .env
  rm -f "$tmp"
  echo "    MUSESCORE_CLI_PATH=\"$musescore\""
else
  echo "    left as it is"
fi

# --- 5. homr -------------------------------------------------------------------------
say "homr (reads scanned PDFs; optional)"
if [ -x "${HOMR_VENV:-$HOME/.local/share/musescore-choir-plugins/homr-venv}/bin/homr" ]; then
  echo "    installed; scripts/install-homr.sh --status says whether it is current"
elif [ "$HOMR" = yes ] || { [ "$HOMR" = ask ] && ask "Install homr now? About 10 minutes and 660 MB."; }; then
  scripts/install-homr.sh
else
  echo "    skipped. Install it later with scripts/install-homr.sh, or from a song's Scan stage."
fi

# --- 6. check -------------------------------------------------------------------------
say "Check"
exec .venv/bin/python song.py --check
