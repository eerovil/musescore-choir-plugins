"""Write the staff line into the descriptions of videos already uploaded (#323).

New uploads carry `stemmanauha-staff: N/M` (see `staff_lines`); this adds it to
the videos uploaded before that, for every song in `songs/` whose `.song.json`
records uploads. The rest of each description and its categoryId are kept, a line
already there is replaced rather than repeated, and the ALL video is left alone.

`--dry-run` prints `title → line` and never signs in to YouTube.
"""

import argparse
import json
import os
import sys
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from . import staff_lines

# (song folder, upload title, video id, line or None, why there is no line)
Plan = Tuple[str, str, str, Optional[str], str]


def _part(upload: dict, song_name: str, slug: str) -> str:
    part = upload.get("part")
    if part is not None:
        return part
    title = upload.get("title", "")
    for name in (song_name, slug):
        if name and title.startswith(name + " "):
            return title[len(name) + 1:]
    return title.rsplit(" ", 1)[-1]


def plan(songs_dir: str, only: Sequence[str] = ()) -> Iterator[Plan]:
    """Every recorded upload and the line it should carry."""
    for slug in sorted(os.listdir(songs_dir)):
        if only and slug not in only:
            continue
        song_dir = os.path.join(songs_dir, slug)
        state = os.path.join(song_dir, ".song.json")
        if not os.path.isfile(state):
            continue
        with open(state, encoding="utf-8") as f:
            data = json.load(f)
        uploads = (data.get("record") or {}).get("uploads") or []
        if not uploads:
            continue
        try:
            staves = staff_lines.song_part_staves(song_dir)
            problem = "" if staves else "no cleaned score"
        except Exception as e:  # a score the renderer's rules refuse
            staves, problem = {}, f"cannot read the score: {e}"
        for upload in uploads:
            part = _part(upload, data.get("name", ""), slug)
            line = staff_lines.line_for(part, staves)
            why = ("" if line else
                   "ALL video, stays zoomed out" if part.strip().upper() in staff_lines._ALL_PARTS
                   else problem or f"no part {part!r} in the score")
            yield slug, upload.get("title", part), upload.get("video_id", ""), line, why


def run(songs_dir: str, only: Sequence[str] = (), dry_run: bool = True,
        out: Callable[[str], None] = print) -> int:
    """Print the plan; unless `dry_run`, also write it. Returns videos changed."""
    rows: List[Plan] = list(plan(songs_dir, only))
    youtube = None
    changed = 0
    for slug, title, video_id, line, why in rows:
        if not line:
            out(f"{title} → (skipped: {why})")
            continue
        if dry_run:
            out(f"{title} → {line}")
            continue
        from .upload_to_youtube import (QuotaExceeded, _update_video_description,
                                        get_authenticated_service)
        if youtube is None:
            youtube = get_authenticated_service()
        try:
            done = _update_video_description(youtube, video_id, line)
        except QuotaExceeded:
            raise
        except Exception as e:
            out(f"{title} → FAILED ({video_id}): {e}")
            continue
        if done is None:
            out(f"{title} → {line} (already there, or video gone)")
        else:
            changed += 1
            out(f"{title} → {line} (updated)")
    out(f"{changed} video(s) updated." if not dry_run
        else f"Dry run: {sum(1 for r in rows if r[3])} video(s) would get a line.")
    return changed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("songs", nargs="*", help="song folders to do (default: all)")
    parser.add_argument("--songs-dir", default="songs")
    parser.add_argument("--dry-run", action="store_true",
                        help="print title → line; change nothing, no YouTube login")
    args = parser.parse_args(argv)
    run(args.songs_dir, args.songs, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
