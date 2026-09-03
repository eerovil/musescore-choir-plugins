"""Put a cleaned score back into the shape the page it came from was printed in.

`clean_score` splits a printed staff carrying two voices into one staff per
voice, because that is what a practice track needs.  The page does the opposite,
and so does every OMR reading of it, so a cleaned score cannot be compared with
one directly.  This undoes the split: the voices that shared a printed staff are
put back on one staff, upper voice first.

Which output staves shared a printed staff is usually recorded in the score, by
the same pass that split them -- `lyricsStaffMap` for an ordinary clean and
`lyricsSystemMap` where the staves change role from system to system.  Where a
score records neither, the grouping is **inferred** from the part names, and
every caller is told so: a guessed reference that reads like a recorded one is
the way this ends up measuring homr against a mistake of ours.
"""

import json
import re
from dataclasses import dataclass, field

from lxml import etree

#: Elements that belong to the staff rather than to a voice, so only the first
#: voice of a merged staff may keep them.
STAFF_LEVEL = ("Clef", "KeySig", "TimeSig")
VOICE_ORDER = "SATB"
#: What a part has to be called to be read as a choir voice.  "Solo" is not a
#: soprano, so the whole name has to look like one -- a letter or a word, and
#: then only numbers.
VOICE_NAME = re.compile(
    r"^\s*(soprano|sopraano|alto|altto|tenor|tenori|baritone|bass|basso|mezzo|[SATB])"
    r"\s*([\d\s.-]*)$",
    re.IGNORECASE,
)
VOICE_LETTER = {
    "soprano": "S", "sopraano": "S", "mezzo": "A", "alto": "A", "altto": "A",
    "tenor": "T", "tenori": "T", "baritone": "B", "bass": "B", "basso": "B",
}


@dataclass
class PrintedStaff:
    """One staff of the printed page, and the cleaned staves that came off it."""

    staves: list[int]
    names: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return "/".join(self.names) if self.names else f"staff {self.staves[0]}"


@dataclass
class Grouping:
    """How a cleaned score's staves map back onto printed ones."""

    printed: list[PrintedStaff]
    source: str

    @property
    def inferred(self) -> bool:
        return self.source == "part names"


def _meta(root: etree._Element, name: str) -> str:
    for tag in root.iter("metaTag"):
        if tag.get("name") == name:
            return (tag.text or "").strip()
    return ""


def staff_names(root: etree._Element) -> dict[int, str]:
    """The part name of each staff, by staff id."""
    names = {}
    for part in root.iter("Part"):
        name = part.findtext("trackName") or part.findtext("Instrument/longName") or ""
        for staff in part.findall("Staff"):
            names[int(staff.get("id", "0"))] = name.strip()
    return names


def voice_of(name: str) -> tuple[str, tuple[int, ...]] | None:
    """The choir voice a part name says it is, and its numbering.

    "Alto 1-2" is the second half of a split Alto 1; "Solo" is not a voice this
    can place and comes back as nothing.
    """
    match = VOICE_NAME.match(name or "")
    if match is None:
        return None
    word, numbers = match.group(1).lower(), match.group(2)
    letter = VOICE_LETTER.get(word, word.upper())
    return letter, tuple(int(value) for value in re.findall(r"\d+", numbers))


def _from_staff_map(recorded: str, names: dict[int, str]) -> list[PrintedStaff] | None:
    """Read `1:1,2;2:3,4` -- printed staff to the staves it was split into."""
    printed = []
    for entry in recorded.split(";"):
        if ":" not in entry:
            return None
        _, staves = entry.split(":", 1)
        ids = [int(value) for value in staves.split(",") if value.strip()]
        if not ids:
            return None
        printed.append(PrintedStaff(ids, [names.get(staff, "") for staff in ids]))
    return printed or None


def _from_system_map(recorded: str, names: dict[int, str]) -> list[PrintedStaff] | None:
    """A per-system map says which staves shared a printed one, system by system.

    A score has a fixed number of staves while a page prints only the ones that
    sound, so the grouping is taken over the whole score: two voices that share a
    printed staff anywhere shared one, and a system where only one of them sings
    prints that staff with one voice on it.
    """
    try:
        systems = json.loads(recorded)
    except ValueError:
        return None
    together: dict[int, set[int]] = {}
    for system in systems:
        for staves in system.get("map", {}).values():
            for staff in staves:
                together.setdefault(staff, set()).update(staves)
    groups: list[list[int]] = []
    seen: set[int] = set()
    for staff in sorted(together):
        if staff in seen:
            continue
        group = sorted(together[staff])
        seen.update(group)
        groups.append(group)
    if not groups:
        return None
    return [PrintedStaff(g, [names.get(staff, "") for staff in g]) for g in groups]


def _inferred(names: dict[int, str], singing: list[int]) -> list[PrintedStaff]:
    """Guess the grouping from the part names: S1 with S2, Alto 1-1 with 1-2.

    Choral engraving puts two numbered voices of one part on a staff, so within
    a voice the parts are paired off in order.  A part whose name is not a voice
    at all -- a solo line, a descant -- keeps a staff to itself rather than being
    guessed onto somebody else's.  All of this is a guess, and `Grouping.
    inferred` says so wherever the result is used.
    """
    by_voice: dict[str, list[tuple[tuple[int, ...], int]]] = {}
    alone: list[int] = []
    for staff in singing:
        voice = voice_of(names.get(staff, ""))
        if voice is None:
            alone.append(staff)
            continue
        by_voice.setdefault(voice[0], []).append((voice[1], staff))

    printed = []
    for letter in sorted(by_voice, key=lambda k: VOICE_ORDER.index(k)):
        ordered = [staff for _, staff in sorted(by_voice[letter])]
        for index in range(0, len(ordered), 2):
            group = ordered[index : index + 2]
            printed.append(PrintedStaff(group, [names.get(s, "") for s in group]))
    printed.extend(PrintedStaff([staff], [names.get(staff, "")]) for staff in alone)
    printed.sort(key=lambda item: item.staves[0])
    return printed


def silent_staves(root: etree._Element) -> list[int]:
    """Staves with nothing to sing: the click track the recorder adds."""
    silent = []
    for index, staff in enumerate(root.find("Score").findall("Staff"), start=1):
        if staff.find(".//Chord") is None:
            silent.append(int(staff.get("id", str(index))))
    return silent


def grouping(root: etree._Element) -> Grouping:
    """How this score's staves map back onto the printed page."""
    names = staff_names(root)
    score = root.find("Score")
    ids = [int(staff.get("id", "0")) for staff in score.findall("Staff")]
    singing = [staff for staff in ids if staff not in silent_staves(root)]

    for recorded, reader, source in (
        (_meta(root, "lyricsSystemMap"), _from_system_map, "per-system map"),
        (_meta(root, "lyricsStaffMap"), _from_staff_map, "staff map"),
    ):
        if not recorded:
            continue
        printed = reader(recorded, names)
        if printed is None:
            continue
        listed = {staff for group in printed for staff in group.staves}
        # A recorded map that names every singing staff is the score's own word
        # for what the page looked like.  One that does not is not usable: the
        # staves it leaves out would silently vanish from the reference.
        if listed >= set(singing):
            return Grouping([g for g in printed if set(g.staves) <= set(singing)], source)

    return Grouping(_inferred(names, singing), "part names")


def implode(root: etree._Element) -> Grouping:
    """Merge the staves back onto the printed ones, in place.

    Returns the grouping used, so a caller can say whether it was recorded or
    guessed.
    """
    score = root.find("Score")
    found = grouping(root)
    staves = {int(s.get("id", "0")): s for s in score.findall("Staff")}
    parts = {}
    for part in score.findall("Part"):
        for staff in part.findall("Staff"):
            parts[int(staff.get("id", "0"))] = part

    merged_parts, merged_staves = [], []
    for number, printed in enumerate(found.printed, start=1):
        first = printed.staves[0]
        staff = _merge_staves([staves[s] for s in printed.staves])
        staff.set("id", str(number))
        merged_staves.append(staff)

        part = etree.fromstring(etree.tostring(parts[first]))
        for child in part.findall("Staff"):
            part.remove(child)
        holder = etree.SubElement(part, "Staff")
        holder.set("id", str(number))
        etree.SubElement(holder, "StaffType", group="pitched").append(
            _text_element("name", "stdNormal")
        )
        part.insert(0, holder)
        _rename(part, printed.label)
        merged_parts.append(part)

    for element in score.findall("Part") + score.findall("Staff"):
        score.remove(element)
    anchor = score.index(score.findall("metaTag")[-1]) + 1 if score.findall("metaTag") else 0
    for offset, element in enumerate(merged_parts + merged_staves):
        score.insert(anchor + offset, element)

    # The lyric routing describes staves that no longer exist.
    for tag in list(score.findall("metaTag")):
        if tag.get("name") in ("lyricsStaffMap", "lyricsSystemMap"):
            score.remove(tag)
    return found


def _text_element(tag: str, text: str) -> etree._Element:
    element = etree.Element(tag)
    element.text = text
    return element


def _rename(part: etree._Element, label: str) -> None:
    for path in ("trackName", "Instrument/longName", "Instrument/shortName"):
        for element in part.findall(path):
            element.text = label


def _merge_staves(members: list[etree._Element]) -> etree._Element:
    """One staff carrying every member's voices, bar by bar."""
    staff = etree.Element("Staff")
    for element in members[0]:
        if element.tag != "Measure":
            staff.append(etree.fromstring(etree.tostring(element)))
    bars = [member.findall("Measure") for member in members]
    for index in range(max(len(m) for m in bars)):
        measure = None
        for position, member_bars in enumerate(bars):
            if index >= len(member_bars):
                continue
            source = etree.fromstring(etree.tostring(member_bars[index]))
            if measure is None:
                measure = source
                continue
            for voice in source.findall("voice"):
                # Only the first voice of a staff carries its clef, key and
                # meter; a second copy would print them twice.
                for element in voice.findall("*"):
                    if element.tag in STAFF_LEVEL:
                        voice.remove(element)
                measure.append(voice)
        if measure is not None:
            staff.append(measure)
    return staff
