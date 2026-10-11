"""Preparing a temporary copy of the cleaned score before it is published.

Publish-only edits live here: dropping staves that carry no music, taking out
the red marks cleaning leaves for a person, supplying an opening tempo when the
score has none, the tempo changes set on the Publish panel, and holding each
fermata one beat longer than written. The source score is never changed.
"""

from __future__ import annotations

import os
import re
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from lxml import etree

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
