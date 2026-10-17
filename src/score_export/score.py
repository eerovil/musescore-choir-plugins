"""Preparing a temporary copy of the cleaned score before it is published.

Publish-only edits live here: dropping staves that carry no music, taking out
the red marks cleaning leaves for a person, supplying an opening tempo when the
score has none, the tempo changes set on the Publish panel, holding each
fermata one beat longer than written, and letting parts share a staff in the
published picture (#420). The source score is never changed.
"""

from __future__ import annotations

import copy
import os
import re
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from lxml import etree

from src.clean_score.utils.utils import part_staff_ids

from src.clean_score.utils.problem_marks import strip_marks, strip_red_notes
from src.clean_score.utils.staff_display import fix_staff_display


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
    bar is replaced, on every staff, so the bar plays at the tempo asked for —
    except one that prints words ("Adagio ♩ = c. 70", "Tempo I"), which stays
    where it is, words and all, and only its speed changes (#404).
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
        worded = False
        for staff in staves:
            staff_bars = staff.findall("Measure")
            if measure > len(staff_bars):
                continue
            for voice in staff_bars[measure - 1].findall("voice"):
                for element in list(voice)[:_bar_start(voice)]:
                    if element.tag != "Tempo":
                        continue
                    if _has_words(element):
                        _retime(element, float(bpm))
                        worded = True
                    else:
                        voice.remove(element)
        if worded:
            added += 1
            continue
        voice = bars[measure - 1].find("voice")
        if voice is None:
            voice = etree.SubElement(bars[measure - 1], "voice")
        voice.insert(_bar_start(voice), _tempo_mark(float(bpm)))
        added += 1
    return added


# A metronome figure in a tempo mark's text: "= c. 70", "♩ = 70", "(ca. 66)".
_METRONOME = re.compile(r"\b(?:c|ca|circa)\.|\d+(?:[.,]\d+)?")


def _has_words(tempo: etree._Element) -> bool:
    """Whether a tempo mark prints words ("Adagio", "Tempo I"), not only "♩ = n".

    The note symbol is a `<sym>`; its name is not text the reader sees."""
    text = tempo.find("text")
    if text is None:
        return False
    shown = [text.text or ""]
    for child in text:
        if child.tag != "sym":
            shown.extend(child.itertext())
        shown.append(child.tail or "")
    shown = "".join(shown)
    return any(ch.isalpha() for ch in _METRONOME.sub("", shown))


def _retime(tempo: etree._Element, bpm: float) -> None:
    """Make a printed tempo mark play at `bpm`, keeping its words as printed (#404).

    MuseScore re-reads the speed off the text when `followText` is on, so it
    is turned off: the recorded speed is the one that plays."""
    value = tempo.find("tempo")
    if value is None:
        value = etree.SubElement(tempo, "tempo")
    value.text = format(bpm / 60, ".12g")
    follow = tempo.find("followText")
    if follow is not None:
        follow.text = "0"


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
    """The staves this part owns: by number when the part names them, by position
    when it does not. MuseScore 4 saves a part's staves without their number, so a
    score saved there would otherwise read as having no music at all."""
    score = part.getparent()
    if score is None:
        return [s.get("id") for s in part.findall("Staff") if s.get("id")]
    return part_staff_ids(score)[score.findall("Part").index(part)]


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


def singing_part_names(root: etree._Element) -> List[str]:
    """The names of the parts `prepare` keeps, in score order (top staff first)."""
    score = root.find("Score")
    if score is None:
        return []
    silent = set(silent_parts(root))
    names = [_part_name(p, i) for i, p in enumerate(score.findall("Part"))]
    return [name for name in names if name not in silent]


def rename_parts(root: etree._Element, names: Sequence[str]) -> int:
    """Give the parts these names, in order: the track name and the instrument's
    long and short names, so the MIDI, the MusicXML and the site agree (#402).
    Returns how many parts changed. A list of another length is refused."""
    score = root.find("Score")
    parts = score.findall("Part") if score is not None else []
    if len(parts) != len(names):
        raise ValueError(f"The score has {len(parts)} parts; {len(names)} names given")
    changed = 0
    for part, name in zip(parts, names):
        fields = [part.find("trackName")]
        instrument = part.find("Instrument")
        if instrument is not None:
            fields += [instrument.find("longName"), instrument.find("shortName")]
        if fields[0] is None:
            fields[0] = etree.SubElement(part, "trackName")
        fields = [f for f in fields if f is not None]
        if all((f.text or "") == name for f in fields):
            continue
        for field in fields:
            field.text = name
        changed += 1
    return changed


def prepare(mscx_path: str, work_dir: str, keep_silent: bool = False,
            initial_bpm: Optional[int] = None,
            tempo_changes: Optional[Sequence[Dict]] = None,
            part_names: Optional[Sequence[str]] = None) -> Tuple[str, List[str]]:
    """Return (score to render, names dropped).

    `part_names` renames the parts left after the silent ones go, top staff first
    (#402): the names a person corrected on the Publish panel, since cleaning
    names parts by guessing from pitch.

    The original file is never touched; when no render-only edit is needed it is
    used as-is, so the common case costs nothing.
    """
    tree = etree.parse(mscx_path)
    root = tree.getroot()
    silent = [] if keep_silent else silent_parts(root)
    changed = bool(drop_parts(root, silent))
    if part_names is not None:
        changed = bool(rename_parts(root, part_names)) or changed
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


# ---------------------------------------------------------------------------
# Two parts on one staff in the published picture (#246, #420)
# ---------------------------------------------------------------------------
# Elements a voice carries that belong to the staff or the score rather than to
# the voice. The upper voice already has them, so the moved voice drops its copy.
_STAFF_ELEMENTS = ("Clef", "KeySig", "TimeSig", "Tempo")


def _music_voices(measure: etree._Element) -> List[etree._Element]:
    return [v for v in measure.findall("voice")
            if v.find("Chord") is not None or v.find("Rest") is not None]


def _is_measure_rest(voice: etree._Element) -> bool:
    events = [e for e in voice if e.tag in ("Chord", "Rest")]
    return (len(events) == 1 and events[0].tag == "Rest"
            and (events[0].findtext("durationType") or "").strip() == "measure")


def _verse(lyric: etree._Element) -> int:
    try:
        return int((lyric.findtext("no") or "0").strip() or 0)
    except ValueError:
        return 0


def _words(voice: etree._Element) -> List[Tuple[int, str]]:
    return [(_verse(lyric), (lyric.findtext("text") or "").strip())
            for lyric in voice.iter("Lyrics")]


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
    """Put a clef into this voice where the bar reaches `at`: in front of the first
    chord or rest starting there, or at the end of the bar when nothing does."""
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


def _staff_of(root: etree._Element, name: str) -> etree._Element:
    score = root.find("Score")
    for index, part in enumerate(score.findall("Part")):
        if _part_name(part, index) == name:
            ids = _staff_ids(part)
            staff = score.find(f"Staff[@id='{ids[0]}']") if len(ids) == 1 else None
            if staff is None:
                raise ValueError(f"{name} is not one staff, so it cannot share one.")
            return staff
    raise ValueError(f"No such part: {name}")


def voice_count(root: etree._Element, name: str) -> int:
    """How many voices this part uses: the highest voice that carries music, in
    any bar. MuseScore counts a bar's voices by their place in it."""
    count = 1
    for measure in _staff_of(root, name).findall("Measure"):
        voices = measure.findall("voice")
        for index, voice in enumerate(voices, 1):
            if voice.find("Chord") is not None or voice.find("Rest") is not None:
                count = max(count, index)
    return count


#: How many voices of its own a part may bring to a shared staff. MuseScore holds
#: four and stems voices 1 and 3 up, 2 and 4 down, so the upper part takes 1 and
#: 3 and the lower part 2 and 4: a unison of the two then shares one notehead.
MAX_VOICES = 2


def can_share(root: etree._Element, upper: str, lower: str) -> bool:
    """Whether both parts' voices fit on one staff."""
    return max(voice_count(root, upper), voice_count(root, lower)) <= MAX_VOICES


def merge_staves(root: etree._Element, groups: Sequence[Sequence[str]]
                 ) -> Dict[str, Dict]:
    """Put each group's lower part on its upper part's staff: the upper part in
    voices 1 and 3, the lower part in voices 2 and 4.

    Returns where each part is drawn: {name: {"staff": n, "voices": [1, ...]}},
    the staff being the 0-based MusicXML part it lands in and the voices numbered
    from 1 the way MuseScore exports them to MusicXML. Raises `ValueError` when a part sings more than two
    voices. MuseScore lays the voices out itself (stems, rests, beams). Words both
    parts sing are printed once; words that differ go on lines below the upper
    part's verses. A bar both rest through keeps one rest.
    """
    score = root.find("Score")
    for upper_name, lower_name in groups:
        if not can_share(root, upper_name, lower_name):
            raise ValueError(
                f"{upper_name} and {lower_name} sing {voice_count(root, upper_name)} and "
                f"{voice_count(root, lower_name)} voices, more than one staff holds.")
    names = [_part_name(p, i) for i, p in enumerate(score.findall("Part"))]
    by_name = dict(zip(names, score.findall("Part")))
    voices_of = {name: list(range(1, voice_count(root, name) + 1)) for name in names}

    for upper_name, lower_name in groups:
        upper, lower = _staff_of(root, upper_name), _staff_of(root, lower_name)
        upper_bars, lower_bars = upper.findall("Measure"), lower.findall("Measure")

        # The staff's clefs: in a bar where the lower part sets one (its opening
        # clef included), the lower part's, at the beat each stands on, since tenor
        # and bass share a bass clef; in any other bar the upper part's own change
        # stays, or the rest of the song would be drawn under the wrong clef.
        # Pitches are absolute, so no note moves whichever clef is drawn.
        for top, bottom in zip(upper_bars, lower_bars):
            clefs = [(at, clef) for voice in bottom.findall("voice")
                     for at, clef in _clefs_at(voice)]
            if not clefs:
                continue
            for voice in top.findall("voice"):
                for element in voice.findall("Clef"):
                    voice.remove(element)
            for at, clef in clefs:
                _insert_at(_first_voice(top), at, copy.deepcopy(clef))

        # Lines the lower part's own words go on, below every verse of the upper's.
        upper_verses = [_verse(lyric) for lyric in upper.iter("Lyrics")]
        shift = max(upper_verses) + 1 if upper_verses else 0
        for top, bottom in zip(upper_bars, lower_bars):
            above = top.findall("voice")
            below = [v for v in bottom.findall("voice")[:MAX_VOICES]
                     if v.find("Chord") is not None or v.find("Rest") is not None]
            both_rest = (above and _is_measure_rest(above[0])
                         and len(_music_voices(top)) == 1
                         and len(below) == 1 and _is_measure_rest(below[0]))
            same = bool(above) and bool(below) and _words(below[0]) == _words(above[0])
            moved = []
            for voice in ([] if both_rest or not below
                          else bottom.findall("voice")[:MAX_VOICES]):
                voice = copy.deepcopy(voice)
                for tag in _STAFF_ELEMENTS:
                    for element in voice.findall(tag):
                        voice.remove(element)
                for lyric in list(voice.iter("Lyrics")):
                    if same:
                        lyric.getparent().remove(lyric)
                        continue
                    verse = _verse(lyric) + shift
                    for no in lyric.findall("no"):
                        lyric.remove(no)
                    if verse:
                        no = etree.Element("no")
                        no.text = str(verse)
                        lyric.insert(0, no)
                moved.append(voice)
            # A bar's voices are told apart by their place in it: interleave them,
            # writing an unused place as an empty voice.
            slots = [etree.Element("voice") for _ in range(2 * MAX_VOICES)]
            for index, voice in enumerate(above[:MAX_VOICES]):
                slots[2 * index] = voice
            for index, voice in enumerate(moved):
                slots[2 * index + 1] = voice
            while len(slots) > 1 and len(slots[-1]) == 0:
                slots.pop()
            at = top.index(above[0]) if above else 0
            for voice in above:
                top.remove(voice)
            for offset, voice in enumerate(slots):
                top.insert(at + offset, voice)

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
        score.remove(lower)
        score.remove(by_name[lower_name])
        voices_of[upper_name] = [2 * v - 1 for v in voices_of[upper_name]]
        voices_of[lower_name] = [2 * v for v in voices_of[lower_name]]

    # Number the staves that are left 1, 2, 3 again, so none leaves a gap.
    if groups:
        renumber = {staff.get("id"): str(i)
                    for i, staff in enumerate(score.findall("Staff"), 1)}
        for staff in [*score.findall("Staff"), *score.findall("Part/Staff")]:
            if staff.get("id") in renumber:
                staff.set("id", renumber[staff.get("id")])

    # The staff a part is drawn on is told as the part it ends up in, which is
    # what the MusicXML (and the site) counts by.
    kept = [name for name in names if by_name[name].getparent() is not None]
    staff_of: Dict[str, int] = {name: index for index, name in enumerate(kept)}
    for upper_name, lower_name in groups:
        staff_of[lower_name] = staff_of[upper_name]
    return {name: {"staff": staff_of[name], "voices": voices_of[name]} for name in names}


def merged_copy(source: str, work_dir: str, groups: Sequence[Sequence[str]]
                ) -> Tuple[str, Dict[str, Dict]]:
    """Write the published picture's score: `source` with these parts sharing staves."""
    tree = etree.parse(source)
    where = merge_staves(tree.getroot(), groups)
    target = os.path.join(work_dir, "picture_score.mscx")
    tree.write(target, encoding="UTF-8", xml_declaration=True)
    return target, where
