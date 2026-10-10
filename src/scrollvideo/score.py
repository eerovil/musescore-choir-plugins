"""Preparing a temporary score before it is engraved and played.

Render-only edits live here: dropping staves that carry no music, taking out the
red marks cleaning leaves for a person, supplying an opening tempo when the
score has none, holding each fermata one beat longer than written, and letting two parts share a staff in the picture. The source score is never changed.
"""

from __future__ import annotations

import copy
import os
import re
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from lxml import etree

from src.clean_score.utils.problem_marks import strip_marks, strip_red_notes
from src.clean_score.utils.staff_display import fix_staff_display
from src.clean_score.utils.utils import starts_new_system


def has_opening_tempo(root: etree._Element) -> bool:
    """Whether any staff declares tempo before its first sounding/rest event."""
    score = root.find("Score")
    if score is None:
        return False
    for staff in score.findall("Staff"):
        measure = staff.find("Measure")
        if measure is None:
            continue
        for voice in measure.findall("voice"):
            for element in voice:
                if element.tag == "Tempo":
                    return True
                if element.tag in ("Chord", "Rest", "location"):
                    break
    return False


def add_opening_tempo(root: etree._Element, bpm: int) -> bool:
    """Add an invisible opening tempo when one is absent. Returns whether added."""
    if has_opening_tempo(root):
        return False
    score = root.find("Score")
    voice = score.find("Staff/Measure/voice") if score is not None else None
    if voice is None:
        raise ValueError("The score has no opening voice to attach a tempo to")

    voice.insert(_bar_start(voice), _tempo_mark(bpm))
    return True


def _tempo_mark(bpm: float) -> etree._Element:
    """An invisible tempo mark, written the way MuseScore writes "♩ = bpm"."""
    tempo = etree.Element("Tempo")
    etree.SubElement(tempo, "tempo").text = format(bpm / 60, ".12g")
    etree.SubElement(tempo, "visible").text = "0"
    text = etree.SubElement(tempo, "text")
    etree.SubElement(text, "sym").text = "metNoteQuarterUp"
    text[-1].tail = f" = {format(bpm, 'g')}"
    return tempo


def _bar_start(voice: etree._Element) -> int:
    """Where a mark at the start of this voice's bar goes: before its first event."""
    return next((i for i, child in enumerate(voice)
                 if child.tag in ("Chord", "Rest", "location")), len(voice))


def opening_bpm(root: etree._Element) -> Optional[float]:
    """The tempo the score opens at, in quarter notes a minute, or None."""
    score = root.find("Score")
    for staff in score.findall("Staff") if score is not None else []:
        measure = staff.find("Measure")
        for voice in measure.findall("voice") if measure is not None else []:
            for element in voice:
                if element.tag == "Tempo" and element.findtext("tempo"):
                    return float(element.findtext("tempo")) * 60
                if element.tag in ("Chord", "Rest", "location"):
                    break
    return None


# A tempo change a person records on the Record panel (#387): from this bar on,
# play at `bpm`, or at the opening tempo when it is START ("Tempo I"). Cleaning
# keeps no tempo marks and a scan never had any, so the render puts them back.
START = "start"


def add_tempo_changes(root: etree._Element, changes: Sequence[Dict]) -> int:
    """Put an invisible tempo mark at the start of each listed bar. Returns how many.

    `changes` are `{"measure": n, "bpm": 112 or "start"}`, bars counted from 1 the
    way recorded fixes count them. A mark already standing at the start of that
    bar is replaced, on every staff, so the bar plays at the tempo asked for.
    "start" is the tempo the score opens at, so add the opening tempo first.
    """
    staves = root.findall("./Score/Staff")
    if not changes or not staves:
        return 0
    start = opening_bpm(root)
    bars = staves[0].findall("Measure")
    added = 0
    for change in changes:
        measure, bpm = int(change["measure"]), change["bpm"]
        if bpm == START:
            if start is None:
                raise ValueError(f"Bar {measure} goes back to the opening tempo, "
                                 "but the score has none")
            bpm = start
        if not 1 < measure <= len(bars):
            raise ValueError(f"A tempo change at bar {measure}: the score has "
                             f"bars 1-{len(bars)}, and bar 1 is the opening tempo")
        for staff in staves:
            staff_bars = staff.findall("Measure")
            if measure > len(staff_bars):
                continue
            for voice in staff_bars[measure - 1].findall("voice"):
                for element in list(voice)[:_bar_start(voice)]:
                    if element.tag == "Tempo":
                        voice.remove(element)
        voice = bars[measure - 1].find("voice")
        if voice is None:
            voice = etree.SubElement(bars[measure - 1], "voice")
        voice.insert(_bar_start(voice), _tempo_mark(float(bpm)))
        added += 1
    return added


# How long a fermata holds (#380). Cleaning writes `timeStretch` 3, which made a
# held dotted half last six seconds at 90 bpm; the owner asked for "around one
# beat more, always". Part of the preview's cache key, so changing it rebuilds.
FERMATA_HOLD = "one beat"


def _beat(sig_n: int, sig_d: int) -> Fraction:
    """One beat of this meter as a fraction of a whole note: the written unit,
    except a compound meter (6/8, 9/8, 12/8) counts in dotted quarters."""
    if sig_d >= 8 and sig_n > 3 and sig_n % 3 == 0:
        return Fraction(3, sig_d)
    return Fraction(1, sig_d)


def hold_fermatas(root: etree._Element) -> int:
    """Set every fermata's `timeStretch` so it adds one beat. Returns how many.

    MuseScore slows the tempo by the stretch from the fermata's beat until the
    next note or rest starts on *any* staff, so the stretch is worked out from
    that span rather than from the held note: a held half over two moving
    quarters gets one beat added to the first quarter's span. A fermata standing
    where nothing starts (on a barline) and one marked not to play are left alone.
    """
    staves = root.findall("./Score/Staff")
    if not staves:
        return 0
    bars = [staff.findall("Measure") for staff in staves]
    sig_n, sig_d = 4, 4
    changed = 0
    for index, top in enumerate(bars[0]):
        starts, ends, fermatas = set(), [], []
        for staff_bars in bars:
            if index >= len(staff_bars):
                continue
            measure = staff_bars[index]
            for sig in measure.iter("TimeSig"):
                if staff_bars is bars[0]:
                    sig_n = int(_fraction(sig.findtext("sigN")) or sig_n)
                    sig_d = int(_fraction(sig.findtext("sigD")) or sig_d)
            for voice in measure.findall("voice"):
                for element, at in _walk(voice):
                    if element.tag in ("Chord", "Rest") and not any(
                            element.find(tag) is not None for tag in _GRACE):
                        starts.add(at)
                    elif element.tag == "Fermata":
                        fermatas.append((element, at))
                ends.append(_voice_end(voice))
        length = _fraction(top.get("len")) if top.get("len") else Fraction(sig_n, sig_d)
        end = max(ends + [length]) if ends else length
        beat = _beat(sig_n, sig_d)
        for fermata, at in fermatas:
            if fermata.findtext("play", "1").strip() == "0" or at not in starts:
                continue
            span = min([s for s in starts if s > at] + [end]) - at
            if span <= 0:
                continue
            stretch = fermata.find("timeStretch")
            if stretch is None:
                stretch = etree.SubElement(fermata, "timeStretch")
            stretch.text = format(float((span + beat) / span), ".6g")
            changed += 1
    return changed


def _voice_end(voice: etree._Element) -> Fraction:
    """Where this voice's last chord or rest ends in the bar."""
    # `_walk` says where each child starts, so a marker after the last one says
    # where it ends.
    return list(_walk([*voice, etree.Element("end")]))[-1][1]


def _part_name(part: etree._Element, index: int) -> str:
    return (part.findtext("trackName") or f"Part {index + 1}").strip()


def _staff_ids(part: etree._Element) -> List[str]:
    return [s.get("id") for s in part.findall("Staff") if s.get("id")]


def system_starts(mscx_path: str) -> List[int]:
    """0-based bars that begin a printed system, read off the score's own breaks.

    A page break ends a system too, so both kinds count. Empty when the score
    carries none. Normal-mode cleaning strips line breaks, so
    a cleaned score usually has none and the caller has to supply the printed
    grouping from the input score instead; a per-system score keeps its own.
    """
    try:
        root = etree.parse(mscx_path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return []
    for staff in root.findall(".//Score/Staff"):
        measures = staff.findall("Measure")
        breaks = [i for i, measure in enumerate(measures)
                  if starts_new_system(measure)]
        if breaks:
            # A break sits on the *last* bar of its system, so the next one starts
            # the following system. A break on the final bar starts nothing.
            return [0] + [i + 1 for i in breaks if i + 1 < len(measures)]
    return []


def measure_count(mscx_path: str) -> int:
    """How many bars the score has, counted on its first staff."""
    try:
        root = etree.parse(mscx_path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return 0
    return max((len(staff.findall("Measure"))
                for staff in root.findall(".//Score/Staff")), default=0)


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


def prepare(mscx_path: str, work_dir: str, keep_silent: bool = False,
            initial_bpm: Optional[int] = None,
            tempo_changes: Optional[Sequence[Dict]] = None) -> Tuple[str, List[str]]:
    """Return (score to render, names dropped).

    The original file is never touched; when no render-only edit is needed it is
    used as-is, so the common case costs nothing.
    """
    tree = etree.parse(mscx_path)
    root = tree.getroot()
    silent = [] if keep_silent else silent_parts(root)
    changed = bool(drop_parts(root, silent))
    # The red marks cleaning leaves for a person fixing the score (#238) are not
    # part of the music; a forgotten one must not end up in a practice track.
    changed = bool(strip_marks(root)) or changed
    changed = bool(strip_red_notes(root)) or changed
    # A score cleaned before #354 still hides the rests the scan shared between
    # two voices and draws some barlines wrong; drawing them is not editing music.
    changed = any(fix_staff_display(root).values()) or changed
    if initial_bpm is not None:
        changed = add_opening_tempo(root, initial_bpm) or changed
    changed = bool(add_tempo_changes(root, tempo_changes or ())) or changed
    changed = bool(hold_fermatas(root)) or changed
    if not changed:
        return mscx_path, []

    target = os.path.join(work_dir, "render_score.mscx")
    tree.write(target, encoding="UTF-8", xml_declaration=True)
    return target, silent


# ---------------------------------------------------------------------------
# Sharing a staff (#246)
# ---------------------------------------------------------------------------
#
# A four-part song on four staves is four staves of height in every frame. Two
# singers often read one staff on the page (S1+S2, T+B), and a practice video can
# do the same: the upper part becomes voice 1 with its stems up, the lower part
# voice 2 with its stems down. Only the *picture* changes. The audio mixes, the
# MIDI the clock is read from and the files written are all made from the
# unmerged score, so the existing check that the highlights land on the notes
# MuseScore plays also checks the merged picture.
#
# The merge is made on the .mscx, before MuseScore exports MusicXML, because
# MuseScore then lays out a two-voice staff by itself: stem directions, rests,
# beams. Doing it on the MusicXML would mean writing all of that by hand.

Group = Tuple[str, str]

# Elements a voice carries that belong to the staff or the score rather than to
# the voice. The upper voice already has them, so the moved voice drops its copy.
_STAFF_ELEMENTS = ("Clef", "KeySig", "TimeSig", "Tempo")


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


def validate_groups(groups: Sequence[Sequence[str]], names: Sequence[str],
                    dropped: Sequence[str] = ()) -> None:
    """Refuse a grouping that names a part the render does not have, or one twice."""
    seen: set = set()
    for group in groups:
        if len(group) != 2:
            raise ValueError(f"{'+'.join(group)}: a staff holds exactly two parts.")
        for name in group:
            if name in dropped:
                raise ValueError(f"{name} has nothing to sing and is left out of the "
                                 "video, so it cannot share a staff.")
            if name not in names:
                raise ValueError(f"No such part: {name}. Parts: {', '.join(names)}")
            if name in seen:
                raise ValueError(f"{name} is in more than one staff group.")
            seen.add(name)


def _music_voices(measure: etree._Element) -> List[etree._Element]:
    return [v for v in measure.findall("voice")
            if v.find("Chord") is not None or v.find("Rest") is not None]


def _is_measure_rest(voice: etree._Element) -> bool:
    events = [e for e in voice if e.tag in ("Chord", "Rest")]
    return (len(events) == 1 and events[0].tag == "Rest"
            and (events[0].findtext("durationType") or "").strip() == "measure")


def _syllables(voice: etree._Element) -> List[str]:
    return [(lyric.findtext("text") or "").strip()
            for lyric in voice.iter("Lyrics")
            if (lyric.findtext("no") or "0").strip() in ("", "0")]


# Written note values as fractions of a whole note.
_VALUES = {"longa": Fraction(4), "breve": Fraction(2), "whole": Fraction(1),
           "half": Fraction(1, 2), "quarter": Fraction(1, 4), "eighth": Fraction(1, 8),
           "16th": Fraction(1, 16), "32nd": Fraction(1, 32), "64th": Fraction(1, 64),
           "128th": Fraction(1, 128), "256th": Fraction(1, 256)}
_GRACE = ("acciaccatura", "appoggiatura", "grace4", "grace16", "grace32",
          "grace8after", "grace16after", "grace32after")


def _fraction(text: Optional[str]) -> Fraction:
    try:
        return Fraction((text or "0").strip())
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _walk(voice: etree._Element):
    """Yield (element, where in the bar it starts) for every child of a voice.

    Where is a fraction of a whole note, read the way MuseScore reads the file: a
    chord or rest moves on by its written value (dots and tuplets included), a
    `location` moves on by its fractions, and a grace note takes no time.
    """
    at = Fraction(0)
    tuplets: List[Fraction] = []
    for element in voice:
        yield element, at
        if element.tag == "Tuplet":
            normal = _fraction(element.findtext("normalNotes")) or Fraction(1)
            actual = _fraction(element.findtext("actualNotes")) or Fraction(1)
            tuplets.append(normal / actual)
        elif element.tag == "endTuplet":
            if tuplets:
                tuplets.pop()
        elif element.tag == "location":
            at += _fraction(element.findtext("fractions"))
        elif element.tag in ("Chord", "Rest"):
            if any(element.find(tag) is not None for tag in _GRACE):
                continue
            kind = (element.findtext("durationType") or "").strip()
            if kind == "measure":
                at += _fraction(element.findtext("duration"))
                continue
            value = _VALUES.get(kind, Fraction(0))
            dots = int((element.findtext("dots") or "0").strip() or 0)
            value *= 2 - Fraction(1, 2 ** dots)
            for ratio in tuplets:
                value *= ratio
            at += value


def _clefs_at(voice: etree._Element) -> List[Tuple[Fraction, etree._Element]]:
    """Every clef in this voice, with where in the bar it stands."""
    return [(at, element) for element, at in _walk(voice) if element.tag == "Clef"]


def _first_voice(measure: etree._Element) -> etree._Element:
    voice = measure.find("voice")
    if voice is None:
        voice = etree.Element("voice")
        index = next((i for i, child in enumerate(measure) if child.tag != "voice"),
                     len(measure))
        measure.insert(index, voice)
    return voice


def _insert_at(voice: etree._Element, at: Fraction, clef: etree._Element) -> None:
    """Put a clef into this voice where the bar reaches `at`.

    It goes in front of whatever introduces the first chord or rest starting there
    (a tuplet or beam marker belongs with its chord), or at the end of the bar when
    nothing starts that late — a clef before the barline for the next bar.
    """
    anchor = 0
    for index, (element, start) in enumerate(_walk(voice)):
        if element.tag in ("Chord", "Rest"):
            if start >= at:
                voice.insert(anchor, clef)
                return
            anchor = index + 1
        elif element.tag == "location":
            anchor = index + 1
    voice.append(clef)


def merge_staves(root: etree._Element, groups: Sequence[Sequence[str]]) -> Dict[str, int]:
    """Put each group's lower part on its upper part's staff, as voice 2.

    Returns which engraved staff (0-based, top first) each remaining part ends up
    on; both parts of a group map to the same one. Raises `ValueError` naming the
    bar when a part already has more than one voice somewhere, because which of
    them would be the second voice is a guess.
    """
    score = root.find("Score")
    parts = score.findall("Part") if score is not None else []
    by_name = {_part_name(part, i): part for i, part in enumerate(parts)}
    staves = {staff.get("id"): staff for staff in score.findall("Staff")} \
        if score is not None else {}

    def staff_of(name: str) -> etree._Element:
        ids = _staff_ids(by_name[name])
        if len(ids) != 1 or ids[0] not in staves:
            raise ValueError(f"{name} is not one staff, so it cannot share one.")
        return staves[ids[0]]

    for upper_name, lower_name in groups:
        upper, lower = staff_of(upper_name), staff_of(lower_name)
        upper_bars, lower_bars = upper.findall("Measure"), lower.findall("Measure")
        for name, bars in ((upper_name, upper_bars), (lower_name, lower_bars)):
            for number, measure in enumerate(bars, 1):
                if len(_music_voices(measure)) > 1:
                    raise ValueError(
                        f"{name} has more than one voice in bar {number}, so it "
                        "cannot share a staff.")

        # The lower part's clefs: tenor and bass go on a bass clef, the way a closed
        # score prints them, and a clef change the lower part makes later is made
        # on the shared staff too, at the same beat. Pitches are absolute, so no
        # note moves whichever clef is drawn.
        for voice in upper.iter("voice"):
            for element in voice.findall("Clef"):
                voice.remove(element)
        for top, bottom in zip(upper_bars, lower_bars):
            for voice in bottom.findall("voice"):
                for at, clef in _clefs_at(voice):
                    _insert_at(_first_voice(top), at, copy.deepcopy(clef))

        for top, bottom in zip(upper_bars, lower_bars):
            above = (_music_voices(top) or top.findall("voice")[:1])
            below = _music_voices(bottom)
            if not above or not below:
                continue
            above, below = above[0], below[0]
            if _is_measure_rest(above) and _is_measure_rest(below):
                continue   # one rest says it for both
            moved = copy.deepcopy(below)
            for tag in _STAFF_ELEMENTS:
                for element in moved.findall(tag):
                    moved.remove(element)
            # Words: once where both sing the same, a second line where they differ.
            words_above, words_below = _syllables(above), _syllables(moved)
            for lyric in list(moved.iter("Lyrics")):
                if words_below == words_above:
                    lyric.getparent().remove(lyric)
                elif words_above:
                    for no in lyric.findall("no"):
                        lyric.remove(no)
                    etree.SubElement(lyric, "no").text = "1"
                    lyric.insert(0, lyric[-1])
            top.insert(top.index(above) + 1, moved)

        # Cleaning forces every stem up; with two voices MuseScore has to choose.
        for element in [*upper.iter("StemDirection")]:
            element.getparent().remove(element)

        # The staff is labelled with both parts, so a singer finds their line.
        label = f"{upper_name}/{lower_name}"
        upper_part = by_name[upper_name]
        for element in [upper_part.find("trackName"),
                        *upper_part.findall("Instrument/longName"),
                        *upper_part.findall("Instrument/shortName"),
                        *upper_part.findall("Instrument/trackName")]:
            if element is not None:
                element.text = label

        lower_part = by_name[lower_name]
        score.remove(lower)
        score.remove(lower_part)

    # Number the staves that are left 1, 2, 3 again, in the Parts and in the music
    # alike, so a staff taken out of the middle leaves no gap behind it.
    if groups:
        renumber = {staff.get("id"): str(i)
                    for i, staff in enumerate(score.findall("Staff"), 1)}
        for staff in [*score.findall("Staff"), *score.findall("Part/Staff")]:
            if staff.get("id") in renumber:
                staff.set("id", renumber[staff.get("id")])

    index: Dict[str, int] = {}
    staves_above = 0
    for name, part in by_name.items():
        if part.getparent() is None:
            continue   # taken into the staff above it
        index[name] = staves_above
        staves_above += max(1, len(_staff_ids(part)))
    for upper_name, lower_name in groups:
        index[lower_name] = index[upper_name]
    return index


def merged_copy(source: str, work_dir: str,
                groups: Sequence[Sequence[str]]) -> Tuple[str, Dict[str, int]]:
    """Write the picture's score: `source` with these parts sharing staves."""
    tree = etree.parse(source)
    staff_of = merge_staves(tree.getroot(), groups)
    target = os.path.join(work_dir, "picture_score.mscx")
    tree.write(target, encoding="UTF-8", xml_declaration=True)
    return target, staff_of
