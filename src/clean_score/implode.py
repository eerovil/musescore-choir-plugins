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
    systems: list["SystemGrouping"] = field(default_factory=list)

    @property
    def inferred(self) -> bool:
        return self.source == "part names"

    @property
    def reviewed(self) -> bool:
        return self.source == "reviewed"


@dataclass
class SystemGrouping:
    """The printed staff positions used by one measure range."""

    start: int
    end: int
    printed: dict[int, PrintedStaff]


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


def _from_system_map(recorded: str, names: dict[int, str]) -> list[SystemGrouping] | None:
    """Read each system's printed positions without collapsing their differences."""
    try:
        raw = json.loads(recorded)
        systems = []
        for entry in raw:
            mapped = {
                int(position): PrintedStaff(
                    [int(staff) for staff in staves],
                    [names.get(int(staff), "") for staff in staves],
                )
                for position, staves in entry["map"].items()
            }
            if mapped and set(mapped) != set(range(1, max(mapped) + 1)):
                return None
            if any(not group.staves for group in mapped.values()):
                return None
            listed = [staff for group in mapped.values() for staff in group.staves]
            if len(listed) != len(set(listed)):
                return None
            systems.append(SystemGrouping(int(entry["start"]), int(entry["end"]), mapped))
    except (KeyError, TypeError, ValueError):
        return None
    systems.sort(key=lambda system: system.start)
    return systems or None


def _system_slots(systems: list[SystemGrouping], names: dict[int, str]) -> list[PrintedStaff]:
    """Fixed score staves representing changing top-to-bottom page positions."""
    count = max((max(system.printed, default=0) for system in systems), default=0)
    slots = []
    for position in range(1, count + 1):
        staves = []
        for system in systems:
            group = system.printed.get(position)
            for staff in group.staves if group else []:
                if staff not in staves:
                    staves.append(staff)
        slots.append(PrintedStaff(staves, [names.get(staff, "") for staff in staves]))
    return slots


def _system_staff_ids(systems: list[SystemGrouping]) -> set[int]:
    listed = set()
    for system in systems:
        for group in system.printed.values():
            listed.update(group.staves)
    return listed


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


def _from_review(
    override: list[list[str]], names: dict[int, str]
) -> list[PrintedStaff] | None:
    """A grouping somebody read off the page and wrote down.

    Named by part rather than by staff id, because a name is what a person can
    check against the page and an id is not, and because re-cleaning a score can
    renumber the staves under a recorded id.
    """
    by_name: dict[str, int] = {name: staff for staff, name in names.items()}
    printed = []
    for group in override:
        staves = [by_name[name] for name in group if name in by_name]
        if len(staves) != len(group):
            missing = [name for name in group if name not in by_name]
            raise KeyError(f"the score has no part called {missing}")
        printed.append(PrintedStaff(staves, [names[staff] for staff in staves]))
    return printed or None


def grouping(root: etree._Element, override: list[list[str]] | None = None) -> Grouping:
    """How this score's staves map back onto the printed page.

    An `override` is a person's reading of the page and beats everything else:
    the recorded maps describe how the app split the staves, which is not always
    how they were printed.
    """
    names = staff_names(root)
    if override:
        printed = _from_review(override, names)
        if printed is not None:
            return Grouping(printed, "reviewed")
    score = root.find("Score")
    ids = [int(staff.get("id", "0")) for staff in score.findall("Staff")]
    singing = [staff for staff in ids if staff not in silent_staves(root)]

    recorded = _meta(root, "lyricsSystemMap")
    systems = _from_system_map(recorded, names) if recorded else None
    system_ids = _system_staff_ids(systems) if systems else set()
    if systems and system_ids >= set(singing) and system_ids <= set(ids):
        return Grouping(_system_slots(systems, names), "per-system map", systems)

    recorded = _meta(root, "lyricsStaffMap")
    printed = _from_staff_map(recorded, names) if recorded else None
    if printed is not None:
        listed = {staff for group in printed for staff in group.staves}
        # A recorded map that names every singing staff is the score's own word
        # for what the page looked like.  One that does not is not usable: the
        # staves it leaves out would silently vanish from the reference.
        if listed >= set(singing):
            return Grouping([g for g in printed if set(g.staves) <= set(singing)], "staff map")

    return Grouping(_inferred(names, singing), "part names")


def implode(
    root: etree._Element,
    override: list[list[str]] | None = None,
    drop_rests: list[str] | None = None,
) -> Grouping:
    """Merge the staves back onto the printed ones, in place.

    `drop_rests` names parts whose silence is not printed.  A voice added above
    a staff -- a third tenor line over T1 and T2 -- is written only where it
    sings; printing a bar of rests for it every time it is absent is something
    the page does not do and an OMR reading of the page will never produce.

    Returns the grouping used, so a caller can say whether it was recorded,
    guessed, or read off the page by a person.
    """
    score = root.find("Score")
    names = staff_names(root)
    found = grouping(root, override)
    staves = {int(s.get("id", "0")): s for s in score.findall("Staff")}
    parts = {}
    for part in score.findall("Part"):
        for staff in part.findall("Staff"):
            parts[int(staff.get("id", "0"))] = part

    system_staves = (
        _merge_system_staves(staves, found, names, drop_rests or [])
        if found.systems
        else []
    )
    merged_parts, merged_staves = [], []
    for number, printed in enumerate(found.printed, start=1):
        first = printed.staves[0]
        silent = [
            index
            for index, name in enumerate(printed.names)
            if name in set(drop_rests or [])
        ]
        staff = (
            system_staves[number - 1]
            if system_staves
            else _merge_staves([staves[s] for s in printed.staves], silent)
        )
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

    _hide_empty_staves(score)

    # The lyric routing describes staves that no longer exist.
    for tag in list(score.findall("metaTag")):
        if tag.get("name") in ("lyricsStaffMap", "lyricsSystemMap"):
            score.remove(tag)
    return found


def _hide_empty_staves(score: etree._Element) -> None:
    """Print a staff only in the systems where it sings, as the page does.

    A score has a fixed number of staves; a printed page does not -- a part that
    rests through a system is simply not printed, which is why a system of this
    music can be two staves and the next one three.  Without this the reference
    carries an empty staff the page never shows, and a staff-by-staff comparison
    against a reading of that page lines up against the wrong staff.
    """
    style = score.find("Style")
    if style is None:
        style = etree.Element("Style")
        score.insert(0, style)
    # The page hides a resting staff in its first system too, so this does.
    for name, value in (("hideEmptyStaves", "1"), ("dontHideStavesInFirstSystem", "0")):
        for existing in style.findall(name):
            style.remove(existing)
        style.append(_text_element(name, value))


def _text_element(tag: str, text: str) -> etree._Element:
    element = etree.Element(tag)
    element.text = text
    return element


def _rename(part: etree._Element, label: str) -> None:
    for path in ("trackName", "Instrument/longName", "Instrument/shortName"):
        for element in part.findall(path):
            element.text = label


def _merge_staves(
    members: list[etree._Element], drop_when_resting: list[int] | None = None
) -> etree._Element:
    """One staff carrying every member's voices, bar by bar.

    Every voice is treated alike, including the topmost one: a part written
    above the others -- a third tenor line over T1 and T2 -- is first in the
    order and may still be absent from a bar it does not sing in.
    """
    unprinted = set(drop_when_resting or [])
    staff = etree.Element("Staff")
    for element in members[0]:
        if element.tag != "Measure":
            staff.append(etree.fromstring(etree.tostring(element)))
    bars = [member.findall("Measure") for member in members]
    for index in range(max(len(member_bars) for member_bars in bars)):
        present = [
            (position, member_bars[index])
            for position, member_bars in enumerate(bars)
            if index < len(member_bars)
        ]
        if not present:
            continue
        staff.append(_merge_measure([source for _, source in present], unprinted))
    return staff


def _merge_measure(
    sources: list[etree._Element], drop_when_resting: set[int] | None = None
) -> etree._Element:
    """Merge source measures into one printed measure."""
    unprinted = drop_when_resting or set()
    measure = etree.fromstring(etree.tostring(sources[0]))
    for voice in measure.findall("voice"):
        measure.remove(voice)
    kept = 0
    for position, source in enumerate(sources):
        for voice in etree.fromstring(etree.tostring(source)).findall("voice"):
            if position in unprinted and voice.find("Chord") is None:
                continue
            if kept:
                # Only the staff's first voice carries its clef, key and meter;
                # a second copy would print them twice.
                for element in voice.findall("*"):
                    if element.tag in STAFF_LEVEL:
                        voice.remove(element)
            measure.append(voice)
            kept += 1
    return measure


def _first_kept_source(
    sources: list[etree._Element], drop_when_resting: set[int]
) -> int | None:
    """Return the source member whose voice carries this printed staff's state."""
    for position, source in enumerate(sources):
        for voice in source.findall("voice"):
            if position not in drop_when_resting or voice.find("Chord") is not None:
                return position
    return None


def _rest_measure(source: etree._Element) -> etree._Element:
    """A full-bar rest carrying the source measure's staff-level state."""
    measure = etree.fromstring(etree.tostring(source))
    original = measure.find("voice")
    for voice in measure.findall("voice"):
        measure.remove(voice)
    voice = etree.SubElement(measure, "voice")
    if original is not None:
        for element in original.findall("*"):
            if element.tag in STAFF_LEVEL:
                voice.append(etree.fromstring(etree.tostring(element)))
    rest = etree.SubElement(voice, "Rest")
    etree.SubElement(rest, "durationType").text = "measure"
    return measure


def _source_states(
    bars: list[etree._Element],
) -> list[dict[str, etree._Element]]:
    """The clef, key and meter in force at each source measure."""
    current: dict[str, etree._Element] = {}
    states = []
    for measure in bars:
        for element in measure.iter():
            if element.tag in STAFF_LEVEL:
                current[element.tag] = etree.fromstring(etree.tostring(element))
        states.append(dict(current))
    return states


def _measure_state(measure: etree._Element) -> dict[str, etree._Element]:
    """Return explicit staff-level state written in one output measure."""
    return {
        element.tag: element
        for element in measure.iter()
        if element.tag in STAFF_LEVEL
    }


def _carry_source_state(
    measure: etree._Element,
    source_state: dict[str, etree._Element],
    output_state: dict[str, etree._Element],
) -> None:
    """Write changed inherited state when a fixed output position changes source."""
    voice = measure.find("voice")
    if voice is None:
        return
    present = _measure_state(measure)
    inherited = []
    for tag in STAFF_LEVEL:
        incoming = source_state.get(tag)
        previous = output_state.get(tag)
        if (
            tag not in present
            and incoming is not None
            and (previous is None or etree.tostring(incoming) != etree.tostring(previous))
        ):
            inherited.append(incoming)
    for element in reversed(inherited):
        voice.insert(0, etree.fromstring(etree.tostring(element)))


def _merge_system_staves(
    staves: dict[int, etree._Element],
    found: Grouping,
    names: dict[int, str],
    drop_rests: list[str],
) -> list[etree._Element]:
    """Build fixed position staves whose contents follow each system's map."""
    source_bars = {staff: element.findall("Measure") for staff, element in staves.items()}
    source_states = {staff: _source_states(bars) for staff, bars in source_bars.items()}
    base = next(iter(staves.values()))
    base_bars = base.findall("Measure")
    by_measure: dict[int, SystemGrouping] = {}
    for system in found.systems:
        if system.start < 1 or system.end < system.start or system.end > len(base_bars):
            raise ValueError(
                f"lyricsSystemMap range {system.start}-{system.end} is outside the score"
            )
        for measure in range(system.start - 1, system.end):
            if measure in by_measure:
                raise ValueError(f"lyricsSystemMap overlaps at measure {measure + 1}")
            by_measure[measure] = system
    missing = [measure + 1 for measure in range(len(base_bars)) if measure not in by_measure]
    if missing:
        raise ValueError(f"lyricsSystemMap does not cover measures {missing}")

    merged = []
    for position, slot in enumerate(found.printed, start=1):
        staff = etree.Element("Staff")
        template = staves[slot.staves[0]]
        for element in template:
            if element.tag != "Measure" and not (position > 1 and element.tag == "VBox"):
                staff.append(etree.fromstring(etree.tostring(element)))
        previous_owner: int | None = None
        output_state: dict[str, etree._Element] = {}
        for index, base_measure in enumerate(base_bars):
            group = by_measure[index].printed.get(position)
            if group:
                sources = [source_bars[source][index] for source in group.staves]
                silent = {
                    member
                    for member, source in enumerate(group.staves)
                    if names.get(source, "") in set(drop_rests)
                }
                measure = _merge_measure(sources, silent)
                owner = _first_kept_source(sources, silent)
                current_owner = group.staves[owner] if owner is not None else None
                if current_owner is not None and current_owner != previous_owner:
                    _carry_source_state(
                        measure,
                        source_states[current_owner][index],
                        output_state,
                    )
            else:
                measure = _rest_measure(base_measure)
                current_owner = None
            for layout_break in measure.findall("LayoutBreak"):
                measure.remove(layout_break)
            staff.append(measure)
            output_state.update(_measure_state(measure))
            previous_owner = current_owner
        merged.append(staff)

    top = merged[0].findall("Measure") if merged else []
    for system in found.systems[:-1]:
        if system.end <= len(top):
            layout_break = etree.SubElement(top[system.end - 1], "LayoutBreak")
            etree.SubElement(layout_break, "subtype").text = "line"
    return merged
