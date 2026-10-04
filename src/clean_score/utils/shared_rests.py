"""Give both voices of a staff the rest the page prints once for the two of them.

Two voices on one staff that fall silent at the same moment are engraved with one
rest, not two, and the scanner writes it into one of them only. On the staff that is
correct: MuseScore fills the other voice's gap with rests as it reads the file. It
stops being correct the moment the split puts each voice on a staff of its own,
because the rest goes with the voice that owned it and the other one is left a bar
that ends early. On Sangerhilsen that was 19 of 29 health findings, and enough short
bars to call the whole parse unusable, without a single note being wrong.

So before the split, a gap in one voice is filled with copies of the other voice's
rests, and only when those rests cover the gap exactly. A gap with a note anywhere in
the other voice is left alone: that is a missing note, not a shared rest, and the
health check should go on reporting it. Nothing is invented, shortened or removed.
"""
import copy
import logging
from fractions import Fraction
from typing import List, Optional, Tuple

from lxml import etree

logger = logging.getLogger(__name__)

_DUR = {
    "whole": Fraction(1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}

# (start, end, element) — element is a Chord, a Rest, or a `location` gap.
Span = Tuple[Fraction, Fraction, etree._Element]


def _fraction(text: Optional[str]) -> Optional[Fraction]:
    if not text:
        return None
    try:
        return Fraction(text.strip())
    except (ValueError, ZeroDivisionError):
        return None


def _length(el: etree._Element) -> Optional[Fraction]:
    base = _DUR.get((el.findtext("durationType") or "").strip())
    if base is None:
        return None
    dots = int((el.findtext("dots") or "0").strip() or 0)
    return base * _DOT.get(dots, Fraction(1))


def _spans(voice: etree._Element) -> Optional[List[Tuple[Fraction, Fraction, etree._Element, bool]]]:
    """Where each chord, rest and gap of a voice sits, plus whether it is in a tuplet.

    None when the voice cannot be laid out for certain: a measure rest, a length we
    cannot read, or a gap that steps backwards.
    """
    out = []
    pos = Fraction(0)
    scale: Optional[Fraction] = None
    for el in voice:
        if el.tag == "Tuplet":
            n, a = _fraction(el.findtext("normalNotes")), _fraction(el.findtext("actualNotes"))
            scale = (n / a) if (n and a) else Fraction(1)
        elif el.tag == "endTuplet":
            scale = None
        elif el.tag == "location":
            if el.find("measures") is not None:
                return None
            got = _fraction(el.findtext("fractions"))
            if got is None or got < 0:
                return None
            out.append((pos, pos + got, el, scale is not None))
            pos += got
        elif el.tag in ("Chord", "Rest"):
            length = _length(el)
            if length is None:
                return None
            length *= scale if scale is not None else 1
            out.append((pos, pos + length, el, scale is not None))
            pos += length
    return out


def _covering_rests(spans, start: Fraction, end: Fraction) -> Optional[List[etree._Element]]:
    """The other voice's rests tiling [start, end) exactly, or None."""
    inside = [s for s in spans if s[0] < end and s[1] > start]
    if not inside or inside[0][0] != start or inside[-1][1] != end:
        return None
    pos = start
    for s0, s1, el, tupled in inside:
        if s0 != pos or el.tag != "Rest" or tupled:
            return None
        pos = s1
    return [el for _, _, el, _ in inside]


def _fill(voice: etree._Element, other: etree._Element, bar: Fraction) -> int:
    mine, theirs = _spans(voice), _spans(other)
    if mine is None or theirs is None or not any(el.tag == "Chord" for _, _, el, _ in mine):
        return 0
    copied = 0
    for start, end, el, tupled in mine:
        if el.tag != "location" or tupled:
            continue
        rests = _covering_rests(theirs, start, end)
        if rests:
            for rest in rests:
                el.addprevious(copy.deepcopy(rest))
            voice.remove(el)
            copied += len(rests)
    end = mine[-1][1] if mine else Fraction(0)
    if end < bar:
        rests = _covering_rests(theirs, end, bar)
        if rests:
            for rest in rests:
                voice.append(copy.deepcopy(rest))
            copied += len(rests)
    return copied


def share_rests(root: etree._Element) -> int:
    """Fill each two-voice staff bar's gaps from the other voice's rests. Returns how many."""
    copied = 0
    for staff in root.findall(".//Score/Staff"):
        meter = Fraction(4, 4)
        for mi, measure in enumerate(staff.findall("Measure"), start=1):
            ts = measure.find(".//TimeSig")
            if ts is not None and ts.findtext("sigN") and ts.findtext("sigD"):
                meter = Fraction(int(ts.findtext("sigN")), int(ts.findtext("sigD")))
            bar = _fraction(measure.get("len")) or meter
            voices = measure.findall("voice")
            if len(voices) != 2:
                continue
            here = _fill(voices[0], voices[1], bar) + _fill(voices[1], voices[0], bar)
            if here:
                logger.debug("Staff %s, measure %d: shared %d rest(s) between its voices",
                             staff.get("id"), mi, here)
            copied += here
    if copied:
        logger.info("Gave %d shared rest(s) to the voice that was missing them", copied)
    return copied
