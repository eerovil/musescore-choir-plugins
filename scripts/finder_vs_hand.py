#!/usr/bin/env python3
"""Find systems against the bands people drew: per page, how many systems each says.

Host evidence for a change to `system_finder.page_systems` (#480): every song in
`songs/` with a `.systems.json` and its PDF is read page by page, and each page whose
count differs from the hand-drawn one is printed, then the totals. Run it before and
after a change and compare. Extra PDFs given on the command line are read too, with
only the count printed per page, for scans that have no hand-drawn bands yet.

    .venv/bin/python scripts/finder_vs_hand.py [--songs songs] [extra.pdf ...]
"""
import argparse
import glob
import json
import os
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from src.song_app import pdf_systems, system_finder  # noqa: E402


def _pdf_of(song_dir):
    try:
        with open(os.path.join(song_dir, ".song.json")) as f:
            name = (json.load(f).get("sources") or {}).get("pdf")
    except (OSError, ValueError):
        name = None
    if name:
        path = name if os.path.isabs(name) else os.path.join(song_dir, os.path.basename(name))
        if os.path.exists(path):
            return path
    scans = [p for p in sorted(glob.glob(os.path.join(song_dir, "*.pdf")))
             if not p.endswith(".render.pdf")]           # the app's own renders
    return scans[0] if scans else None


def _count(pdf, page, render_dir):
    image = Image.open(pdf_systems.render_page(pdf, page, system_finder.QUICK_DPI, render_dir))
    return len(system_finder.page_systems(image))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--songs", default="songs")
    ap.add_argument("pdfs", nargs="*")
    args = ap.parse_args()
    differ = pages = 0
    with tempfile.TemporaryDirectory() as render_dir:
        for path in sorted(glob.glob(os.path.join(args.songs, "*", ".systems.json"))):
            song_dir = os.path.dirname(path)
            pdf = _pdf_of(song_dir)
            if not pdf:
                continue
            with open(path) as f:
                data = json.load(f)
            bands = data if isinstance(data, list) else data.get("systems", [])
            for page, hand in sorted(Counter(b["page"] for b in bands).items()):
                found = _count(pdf, page, render_dir)
                pages += 1
                if found != hand:
                    differ += 1
                    print(f"{os.path.basename(song_dir)} page {page}: drawn {hand}, found {found}")
        print(f"hand-drawn pages {pages}, differ {differ}")
        for pdf in args.pdfs:
            counts = [_count(pdf, p, render_dir) for p in range(1, pdf_systems.page_count(pdf) + 1)]
            print(f"{os.path.basename(pdf)}: {sum(counts)} systems, per page {counts}")


if __name__ == "__main__":
    main()
