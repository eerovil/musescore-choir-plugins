#!/usr/bin/env python3
"""Propose the printed-system bands for a song and save them.

The app deliberately never writes `.systems.json` on its own -- `find-systems`
hands the proposal to the Systems editor unsaved, so a person drags before
anything is recorded.  This is the same proposal written straight to the file,
for songs being prepared as OMR fixtures in bulk; **the drag is still owed**, so
open each song's Systems tab and correct what the finder got wrong.

    .venv/bin/python scripts/propose_bounds.py <slug> [<slug> ...]
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.song_app import pdf_systems, pipeline, state, system_finder  # noqa: E402
from scripts.reference_manifest import reference_files  # noqa: E402


def bounds_score(song) -> str:
    xml = song.source_path("xml")
    if not xml or not os.path.exists(xml):
        return ""
    try:
        return pipeline.convert_to_mscx(xml, song.dir)
    except Exception:
        return ""


def main() -> None:
    load_dotenv()
    # The page a fixture is checked against is the manifest's, not whichever
    # PDF the song folder happens to name first: which one was the printed page
    # has twice been the thing that was wrong.
    for slug in sys.argv[1:]:
        song = state.load(slug)
        pdf = str(reference_files(slug).pdf)
        print(f"== {slug}: {os.path.basename(pdf)}", flush=True)
        bands = system_finder.find_bands(pdf, log=lambda m: print("  " + m, flush=True))
        saved = pipeline.save_system_bounds(
            song.dir, [b.to_dict() for b in bands], bounds_score(song)
        )
        labelled = sum(1 for b in saved if b.get("measure_start"))
        print(f"   saved {len(saved)} bands to {pdf_systems.BOUNDS_FILE}"
              f" ({labelled} labelled with a measure range)", flush=True)


if __name__ == "__main__":
    main()
