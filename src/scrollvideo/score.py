"""Preparing the score before it is engraved.

The one edit made here is dropping staves that carry no music — the click/rest
staff `add_rest_track.qml` adds, and any percussion part. They cost a staff of
height in every frame and would each get their own pointless practice video.
"""

from __future__ import annotations

import os
from typing import List, Tuple

from lxml import etree


def _part_name(part: etree._Element, index: int) -> str:
    return (part.findtext("trackName") or f"Part {index + 1}").strip()


def _staff_ids(part: etree._Element) -> List[str]:
    return [s.get("id") for s in part.findall("Staff") if s.get("id")]


def silent_parts(root: etree._Element) -> List[str]:
    """Names of parts with nothing to sing: percussion, or only rests."""
    score = root.find("Score")
    if score is None:
        return []

    music = {staff.get("id"): staff for staff in score.findall("Staff")}
    silent = []
    for index, part in enumerate(score.findall("Part")):
        percussion = (part.find(".//Instrument[@id='drumset']") is not None
                      or (part.findtext(".//useDrumset") or "").strip() == "1")
        ids = _staff_ids(part)
        has_notes = any(music[i].find(".//Chord") is not None
                        for i in ids if i in music)
        if percussion or not has_notes:
            silent.append(_part_name(part, index))
    return silent


def drop_parts(root: etree._Element, names: List[str]) -> int:
    """Remove these parts and the staves they own. Returns the number dropped."""
    score = root.find("Score")
    if score is None or not names:
        return 0

    wanted = set(names)
    dropped = 0
    for index, part in enumerate(list(score.findall("Part"))):
        if _part_name(part, index) not in wanted:
            continue
        for staff_id in _staff_ids(part):
            staff = score.find(f"Staff[@id='{staff_id}']")
            if staff is not None:
                score.remove(staff)
        score.remove(part)
        dropped += 1
    return dropped


def prepare(mscx_path: str, work_dir: str, keep_silent: bool = False) -> Tuple[str, List[str]]:
    """Return (score to render, names dropped).

    The original file is never touched; when there is nothing to drop it is used
    as-is, so the common case costs nothing.
    """
    if keep_silent:
        return mscx_path, []
    tree = etree.parse(mscx_path)
    silent = silent_parts(tree.getroot())
    if not silent:
        return mscx_path, []

    target = os.path.join(work_dir, "render_score.mscx")
    drop_parts(tree.getroot(), silent)
    tree.write(target, encoding="UTF-8", xml_declaration=True)
    return target, silent
