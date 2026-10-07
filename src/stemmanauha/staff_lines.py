"""Which staff each practice video's part is sung from, written for YouTube.

The stemmanauhat site zooms a part's video on a phone to that part's staff. Guessing
the staff from part names fails when split parts share one (Meri: S1-1 and S1-2 on
one staff) and is right when they do not (Kaipaava), so the uploader says it, in one
line of the video's description:

    stemmanauha-staff: <staff>/<staves>

`<staff>` counts from the top starting at 1, `<staves>` is how many the video shows.
Parts sharing a staff get the same number, and the ALL video gets no line, since it
stays zoomed out. The site reads it with `STAFF_LINE_RE`
(eerovil/musescore-choir-plugins#323, eerovil/stemmanauhat#5).

The staves are counted the way the scrolling renderer draws them: parts with nothing
to sing (a click or spacer staff, percussion) are left out, and the song's shared
staves (`record.staff_groups`) count once.
"""

import glob
import json
import os
import re
from typing import Dict, Optional, Sequence, Tuple

from lxml import etree

STAFF_LINE_RE = re.compile(r"^stemmanauha-staff:\s*(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE)
DESCRIPTION = "Practice track"

# The mixes that are every voice at once; their videos are not zoomed.
_ALL_PARTS = {"ALL", "KAIKKI"}


def staff_line(staff: int, staves: int) -> str:
    return f"stemmanauha-staff: {staff}/{staves}"


def part_staves(mscx_path: str,
                staff_groups: Sequence[Sequence[str]] = ()) -> Dict[str, Tuple[int, int]]:
    """`{part: (staff, staves)}`, 1-based from the top, as the video draws them.

    Uses the renderer's own rules (`scrollvideo.score`) on an in-memory copy, so
    the numbers cannot drift from the picture: silent parts dropped, and each
    group's lower part put on its upper part's staff.
    """
    from src.scrollvideo import score as score_mod
    from src.scrollvideo.audio import part_names

    root = etree.parse(mscx_path).getroot()
    score_mod.drop_parts(root, score_mod.silent_parts(root))
    if staff_groups:
        index = score_mod.merge_staves(root, staff_groups)
    else:
        index = {name: i for i, name in enumerate(part_names(root))}
    staves = len(set(index.values()))
    return {name: (i + 1, staves) for name, i in index.items()}


def _song_score(song_dir: str, data: Dict) -> Optional[str]:
    cleaned = data.get("cleaned")
    if cleaned and os.path.exists(os.path.join(song_dir, cleaned)):
        return os.path.join(song_dir, cleaned)
    found = sorted(glob.glob(os.path.join(song_dir, "*_cleaned.mscx")))
    return found[0] if found else None


def song_part_staves(song_dir: str) -> Dict[str, Tuple[int, int]]:
    """`part_staves` for the score in `songs/<song>/`, with the song's own shared
    staves. Empty when the song has no cleaned score."""
    from src.scrollvideo import score as score_mod

    data: Dict = {}
    state = os.path.join(song_dir, ".song.json")
    if os.path.exists(state):
        with open(state, encoding="utf-8") as f:
            data = json.load(f)
    score = _song_score(song_dir, data)
    if not score:
        return {}
    record = data.get("record") or {}
    # Only the scrolling renderer shares staves; a screen recording shows the
    # score as it is.
    groups = (score_mod.parse_groups(record.get("staff_groups"))
              if record.get("renderer") in (None, "scroll") else [])
    return part_staves(score, groups)


def line_for(part: str, staves: Dict[str, Tuple[int, int]]) -> Optional[str]:
    """The description line for this part's video, or None (ALL, or a part the
    score does not have)."""
    if not part or part.strip().upper() in _ALL_PARTS:
        return None
    found = staves.get(part)
    if found is None:  # a title spelt "solo" for a part named "Solo"
        matches = [v for k, v in staves.items() if k.lower() == part.lower()]
        found = matches[0] if len(matches) == 1 else None
    return staff_line(*found) if found else None


def with_line(description: str, line: Optional[str]) -> str:
    """`description` carrying exactly this staff line: an old one is replaced,
    otherwise the line is added at the end. None takes any line out."""
    kept = [row for row in (description or "").split("\n")
            if not STAFF_LINE_RE.match(row)]
    while kept and not kept[-1].strip():
        kept.pop()
    if line:
        kept.append(line)
    return "\n".join(kept)
