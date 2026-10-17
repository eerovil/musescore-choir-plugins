"""Which parts share a staff on the site, the way the page printed them (#420).

Cleaning gives every part a staff of its own, which a practice track needs. On
the site a song with more than `MAX_STAVES` of them is too tall to read, so the
published picture puts the parts back two to a staff, as the page printed them.
Only the picture: the MIDI and the part list stay one per part.

Where the pairs come from, best first: what the clean recorded about which
staves came off one printed staff (`lyricsStaffMap`); for a song whose staves
change role from system to system (`lyricsSystemMap`), the pairs that share a
printed staff in the most bars, since on the site a part cannot move between
staves; and failing both, a guess from the part names. The source is returned
with the pairs, so a guess is never shown as if it were read off the page.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Sequence, Tuple

from lxml import etree

from src.clean_score import implode

#: More singing parts than this and the site draws them two to a staff.
MAX_STAVES = 5

Group = Tuple[str, str]

PAGE = "page"
PAGE_PER_SYSTEM = "page, per system"
PART_NAMES = "part names"


def parse_groups(text: Optional[str]) -> List[Group]:
    """`"S1+S2, A1+A2"` -> `[("S1", "S2"), ("A1", "A2")]`.

    Groups are separated by commas, semicolons or new lines; `"S1+S2 A1+A2"` works
    too, as long as every word in it is a group. A blank text is no grouping.
    """
    groups: List[Group] = []
    for chunk in re.split(r"[,;\n]+", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        words = chunk.split()
        pieces = words if len(words) > 1 and all("+" in w for w in words) else [chunk]
        for piece in pieces:
            names = [name.strip() for name in piece.split("+")]
            if len(names) != 2 or not all(names):
                raise ValueError(
                    f"“{piece}” is not two parts joined by +, e.g. S1+S2. "
                    "A staff holds at most two parts.")
            groups.append((names[0], names[1]))
    return groups


def format_groups(groups: Sequence[Sequence[str]]) -> str:
    """The text `parse_groups` reads, written the one way the app stores it."""
    return ", ".join("+".join(group) for group in groups)


def validate_groups(groups: Sequence[Sequence[str]], names: Sequence[str]) -> None:
    """Refuse a grouping that names a part the score does not have, or one twice."""
    seen: set = set()
    for group in groups:
        if len(group) != 2:
            raise ValueError(f"{'+'.join(group)}: a staff holds exactly two parts.")
        for name in group:
            if name not in names:
                raise ValueError(f"No such part: {name}. Parts: {', '.join(names)}")
            if name in seen:
                raise ValueError(f"{name} is in more than one shared staff.")
            seen.add(name)


def _pairs(staves: Sequence[int]) -> List[List[int]]:
    """A printed group cut into pairs, top first; a part left over stays alone."""
    return [list(staves[i:i + 2]) for i in range(0, len(staves) - 1, 2)]


def _from_staff_map(recorded: str, singing: List[int]) -> Optional[List[List[int]]]:
    groups = []
    listed = set()
    for entry in recorded.split(";"):
        if ":" not in entry:
            return None
        try:
            ids = [int(v) for v in entry.split(":", 1)[1].split(",") if v.strip()]
        except ValueError:
            return None
        listed.update(ids)
        groups += _pairs([s for s in ids if s in singing])
    # A map that leaves a singing staff out does not describe this score.
    return groups if listed >= set(singing) else None


def _from_system_map(recorded: str, singing: List[int]) -> Optional[List[List[int]]]:
    """Pairs that share a printed staff in the most bars, each part used once."""
    try:
        systems = json.loads(recorded)
        shared: Dict[Tuple[int, int], int] = {}
        listed = set()
        for system in systems:
            bars = int(system["end"]) - int(system["start"]) + 1
            positions: Dict[str, List[int]] = {}
            for position, staves in system["map"].items():
                positions.setdefault(str(position), []).extend(int(s) for s in staves)
            for staves in positions.values():
                staves = sorted({s for s in staves if s in singing})
                listed.update(staves)
                for i, upper in enumerate(staves):
                    for lower in staves[i + 1:]:
                        shared[(upper, lower)] = shared.get((upper, lower), 0) + bars
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not listed >= set(singing):
        return None
    used: set = set()
    groups = []
    for (upper, lower), _bars in sorted(shared.items(), key=lambda kv: (-kv[1], kv[0])):
        if upper in used or lower in used:
            continue
        used.update((upper, lower))
        groups.append([upper, lower])
    return sorted(groups)


def _from_names(names: Dict[int, str], singing: List[int]) -> List[List[int]]:
    """A guess: a b-part with its main part (S1 + S1b), then numbered parts of one
    voice in order (S1 + S2). A part that is not a choir voice stays alone."""
    by_name = {names[s]: s for s in singing}
    used: set = set()
    groups = []
    for staff in singing:
        name = names[staff]
        if len(name) > 1 and name.endswith("b") and by_name.get(name[:-1]) not in (None, staff):
            base = by_name[name[:-1]]
            if base not in used and staff not in used:
                used.update((base, staff))
                groups.append(sorted([base, staff]))
    by_voice: Dict[str, List[Tuple[Tuple[int, ...], int]]] = {}
    for staff in singing:
        if staff in used:
            continue
        voice = implode.voice_of(names[staff])
        if voice is not None:
            by_voice.setdefault(voice[0], []).append((voice[1], staff))
    for entries in by_voice.values():
        groups += [sorted(pair) for pair in _pairs([s for _, s in sorted(entries)])]
    return sorted(groups)


def proposal(root: etree._Element) -> Tuple[List[Group], str]:
    """The parts that share a staff on the site, and where that came from.

    Nothing when the score has `MAX_STAVES` singing parts or fewer. `root` is the
    score as it is published: silent parts gone and the parts named as they will
    be, since the pairs are given by name.
    """
    score = root.find("Score")
    if score is None:
        return [], ""
    names = implode.staff_names(root)
    silent = set(implode.silent_staves(root))
    singing = [int(s.get("id", "0")) for s in score.findall("Staff")
               if int(s.get("id", "0")) not in silent]
    if len(singing) <= MAX_STAVES or len({names.get(s) for s in singing}) != len(singing):
        return [], ""

    groups, source = None, ""
    recorded = implode._meta(root, "lyricsSystemMap")
    if recorded:
        groups, source = _from_system_map(recorded, singing), PAGE_PER_SYSTEM
    if groups is None:
        recorded = implode._meta(root, "lyricsStaffMap")
        if recorded:
            groups, source = _from_staff_map(recorded, singing), PAGE
    if groups is None:
        groups, source = _from_names(names, singing), PART_NAMES
    return [(names[upper], names[lower]) for upper, lower in groups], source
