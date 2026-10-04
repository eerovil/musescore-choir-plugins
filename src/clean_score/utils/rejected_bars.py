"""Reset a bar MuseScore 3 would call corrupted, and say what was taken out.

MuseScore checks every score it opens (`Score::sanityCheck`, `libmscore/check.cpp`):
voice 1 of each staff must add up to exactly the bar, and no other voice may run past
it. A score that fails is "corrupted", and on Sangerhilsen the full app crashed on it
outright. Only the app runs the check — a command-line export does not — which is why
every render here went through and the first anyone heard of it was a person opening
the file.

The bars that fail are the ones a scan read badly enough that a voice runs longer than
the bar, typically a triplet with a note lost and the rest of the voice stepping
backwards to make room. There is no reading of the page in them to recover, so the
staff's bar is reset to a whole-bar rest and the notes it held are handed back, for
the caller to write down where a person will see them. Losing them quietly would be
worse than leaving the file broken; losing them loudly is what makes it openable.
"""
from fractions import Fraction
from typing import Dict, Iterator, List, Optional, Tuple

from lxml import etree

from .score_fixes import note_name

_DUR = {
    "whole": Fraction(1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}
# What a cleared bar keeps: the things that are about the staff, not the music in it.
_KEEP = ("TimeSig", "KeySig", "Clef")


def _fraction(text: Optional[str]) -> Optional[Fraction]:
    if not text:
        return None
    try:
        return Fraction(text.strip())
    except (ValueError, ZeroDivisionError):
        return None


def _bar_lengths(staff: etree._Element) -> List[Fraction]:
    meter = Fraction(4, 4)
    out = []
    for measure in staff.findall("Measure"):
        ts = measure.find(".//TimeSig")
        if ts is not None and ts.findtext("sigN") and ts.findtext("sigD"):
            meter = Fraction(int(ts.findtext("sigN")), int(ts.findtext("sigD")))
        out.append(_fraction(measure.get("len")) or meter)
    return out


def _walk(voice: etree._Element, bar: Fraction) -> Iterator[Tuple[Fraction, etree._Element]]:
    """Each child of a voice with the position it stands at."""
    pos = Fraction(0)
    scale = Fraction(1)
    for el in voice:
        yield pos, el
        if el.tag == "Tuplet":
            n, a = _fraction(el.findtext("normalNotes")), _fraction(el.findtext("actualNotes"))
            scale = (n / a) if (n and a) else Fraction(1)
        elif el.tag == "endTuplet":
            scale = Fraction(1)
        elif el.tag == "location":
            pos += _fraction(el.findtext("fractions")) or 0
        elif el.tag in ("Chord", "Rest"):
            kind = (el.findtext("durationType") or "").strip()
            if kind == "measure":
                pos += bar
            elif kind in _DUR:
                dots = int((el.findtext("dots") or "0").strip() or 0)
                pos += _DUR[kind] * _DOT.get(dots, Fraction(1)) * scale


def _lands_outside(pos: Fraction, pointer: etree._Element, bar: Fraction) -> bool:
    """Whether a spanner's `next`/`prev` location reaches out of its own bar."""
    loc = pointer.find("location")
    if loc is None:
        return False
    if loc.find("measures") is not None:
        return True
    target = pos + (_fraction(loc.findtext("fractions")) or 0)
    return target < 0 or target >= bar


def _cut_spanners_into(measure: Optional[etree._Element], bar: Fraction, side: str) -> None:
    """Drop the half of any tie or slur in `measure` whose other half was cleared."""
    if measure is None:
        return
    for voice in measure.findall("voice"):
        for pos, el in list(_walk(voice, bar)):
            if el.tag == "Spanner":
                pointer = el.find(side)
                if pointer is not None and _lands_outside(pos, pointer, bar):
                    voice.remove(el)
            elif el.tag == "Chord":
                for note in el.findall("Note"):
                    for tie in note.findall("Spanner"):
                        pointer = tie.find(side)
                        if pointer is not None and _lands_outside(pos, pointer, bar):
                            note.remove(tie)


def clear_bar(root: etree._Element, staff_index: int, measure_no: int) -> Optional[List[str]]:
    """Reset one staff's bar to a whole-bar rest. Returns the notes it held, or None.

    `staff_index` and `measure_no` are 1-based and counted the way MuseScore counts
    them in its own message: the score's staves in order, and every bar from the top.
    None when there is no such bar.
    """
    staves = root.findall(".//Score/Staff")
    if not 1 <= staff_index <= len(staves):
        return None
    staff = staves[staff_index - 1]
    measures = staff.findall("Measure")
    if not 1 <= measure_no <= len(measures):
        return None
    lengths = _bar_lengths(staff)
    measure, bar = measures[measure_no - 1], lengths[measure_no - 1]

    removed = [note_name(int(n.findtext("pitch")), int(n.findtext("tpc")) if n.findtext("tpc") else None)
               for n in measure.iter("Note") if (n.findtext("pitch") or "").strip().isdigit()]

    voices = measure.findall("voice")
    for i, voice in enumerate(voices):
        for el in list(voice):
            if el.tag not in _KEEP:
                voice.remove(el)
        if i and not len(voice):
            measure.remove(voice)
    if not voices:
        voice = etree.SubElement(measure, "voice")
    else:
        voice = voices[0]
    rest = etree.SubElement(voice, "Rest")
    etree.SubElement(rest, "durationType").text = "measure"
    etree.SubElement(rest, "duration").text = f"{bar.numerator}/{bar.denominator}"

    if measure_no > 1:
        _cut_spanners_into(measures[measure_no - 2], lengths[measure_no - 2], "next")
    if measure_no < len(measures):
        _cut_spanners_into(measures[measure_no], lengths[measure_no], "prev")
    return removed


def staff_names(root: etree._Element) -> Dict[int, str]:
    """1-based staff position -> the part name a person knows it by."""
    by_id: Dict[str, str] = {}
    for part in root.findall(".//Score/Part"):
        name = (part.findtext("trackName") or part.findtext("Instrument/trackName") or "").strip()
        for st in part.findall("Staff"):
            by_id[st.get("id", "")] = name
    return {i: by_id.get(st.get("id", ""), "") or f"staff {i}"
            for i, st in enumerate(root.findall(".//Score/Staff"), start=1)}
