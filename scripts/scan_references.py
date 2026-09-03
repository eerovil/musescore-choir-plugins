#!/usr/bin/env python3
"""Read each reference song's page with homr, into a scratch song of its own.

The six reference songs are finished work -- recorded, uploaded, sung -- and the
scan stage writes into the song it scans and can put it back on `scan` when a
band comes back a hole.  So none of this touches them: each gets a throwaway song
under `scan-eval/` holding a link to its PDF and a copy of its reviewed
`.systems.json`, and that is what is scanned.  What comes out is the same
`scanned.musicxml` the app would produce, next to a reference nobody scanned.

    .venv/bin/python scripts/scan_references.py            # every song in the set
    .venv/bin/python scripts/scan_references.py <slug> ...

Writes `scan-eval/<slug>/`, and prints a line per system.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.song_app import pdf_systems, scan, state  # noqa: E402

MANIFEST = Path("fixtures/omr-songs.json")
SCRATCH = Path("scan-eval")


def in_the_set() -> list[str]:
    listed = json.loads(MANIFEST.read_text())["songs"]
    return [name for name, entry in listed.items()
            if entry["review"]["status"] != "excluded"]


def stage_a_copy(slug: str, root: Path) -> None:
    """A song holding nothing but the page and the bands drawn on it."""
    real = Path("songs") / slug
    listed = json.loads(MANIFEST.read_text())["songs"][slug]
    pdf = listed["pdf"]
    here = root / slug
    here.mkdir(parents=True, exist_ok=True)
    target = here / pdf
    if not target.exists():
        # A link, not a copy: these are scans of in-copyright editions and one
        # of them is 4 MB.
        os.symlink(os.path.abspath(real / pdf), target)
    shutil.copy(real / pdf_systems.BOUNDS_FILE, here / pdf_systems.BOUNDS_FILE)
    (here / ".song.json").write_text(json.dumps({
        "name": slug, "stage": "scan", "sources": {"pdf": pdf},
    }, indent=2, ensure_ascii=False))


def main() -> None:
    load_dotenv()
    slugs = sys.argv[1:] or in_the_set()
    root = SCRATCH.resolve()
    for slug in slugs:
        stage_a_copy(slug, root)

    # Every song here is one of these throwaways, so nothing real is in reach.
    state.SONGS_DIR = str(root)
    for slug in slugs:
        print(f"\n===== {slug}", flush=True)
        began = time.time()
        song = state.load(slug)
        result = scan.run(song, log=lambda m: print("  " + m, flush=True))
        took = time.time() - began
        holes = result.get("holes") or []
        print(f"  -> {len(result.get('systems') or [])} systems, "
              f"{len(holes)} hole(s) {holes}, {took:.0f}s", flush=True)


if __name__ == "__main__":
    main()
