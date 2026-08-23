"""Repair measures where one voice falls short of the length the others agree on.

MuseScore writes `len="9/8"` on a measure whose content does not match the
prevailing meter. `preprocess_corrupted_measures` tries to fix these by shortening
final rests, but all-or-nothing: it gives up entirely if any over-long voice does
not end in a rest, and then the measure stays malformed for good.

This pass handles the other case — a measure that is *internally consistent except
for one voice*. The length is decided by the voices themselves: whichever length a
majority of the note-bearing voices agree on is taken as the truth, and voices that
fall short of it are padded with a rest.

**It only ever adds rests.** An earlier version of this pass also deleted things
that looked like OCR junk — a trailing rest, a `location` gap, a repeated notehead —
and on the reference score it deleted a real note: the bar looked like 4/4, but the
voice with "one note too many" was singing six syllables that the page prints, and
the lyric arithmetic caught it immediately afterwards. Three voices had independently
been read as 9/8; the bar simply is 9/8. Padding cannot make that mistake, because
adding a rest to a voice that is already complete is never necessary and never
happens.
"""
import logging
from collections import Counter
from fractions import Fraction
from typing import Dict, List, Optional

from lxml import etree

logger = logging.getLogger(__name__)

_DUR = {
    "whole": Fraction(1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}
# Rests to pad with, longest first, so a gap is filled in as few as possible.
_PAD = [("whole", Fraction(1)), ("half", Fraction(1, 2)), ("quarter", Fraction(1, 4)),
        ("eighth", Fraction(1, 8)), ("16th", Fraction(1, 16)), ("32nd", Fraction(1, 32))]


def _fraction(text: Optional[str]) -> Optional[Fraction]:
    if not text:
        return None
    try:
        return Fraction(text.strip())
    except (ValueError, ZeroDivisionError):
        return None


def _voice_len(voice: etree._Element) -> Optional[Fraction]:
    """How much time a voice occupies, the way MuseScore counts it.

    `location` gaps count: a voice can look complete on its notes alone and still
    occupy more of the bar because of one.
    """
    total = Fraction(0)
    scale = Fraction(1)
    for el in voice:
        if el.tag == "Tuplet":
            n, a = _fraction(el.findtext("normalNotes")), _fraction(el.findtext("actualNotes"))
            scale = (n / a) if (n and a) else Fraction(1)
        elif el.tag == "endTuplet":
            scale = Fraction(1)
        elif el.tag == "location":
            got = _fraction(el.findtext("fractions"))
            if got is not None:
                total += got
        elif el.tag in ("Chord", "Rest"):
            kind = (el.findtext("durationType") or "").strip()
            if kind == "measure":
                continue
            base = _DUR.get(kind)
            if base is None:
                return None
            dots = int((el.findtext("dots") or "0").strip() or 0)
            total += base * _DOT.get(dots, Fraction(1)) * scale
    return total


def _has_notes(voice: etree._Element) -> bool:
    return voice.find("Chord") is not None


def _pad(voice: etree._Element, missing: Fraction) -> None:
    """Append rests totalling `missing` to the end of the voice."""
    for name, size in _PAD:
        while missing >= size:
            rest = etree.SubElement(voice, "Rest")
            etree.SubElement(rest, "durationType").text = name
            missing -= size
        if missing == 0:
            return


def fix_overfull_measures(root: etree._Element) -> int:
    """Pad voices that fall short of the length the rest of the measure agrees on.

    Returns the number of voices padded.
    """
    staves = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None]
    if not staves:
        return 0
    padded = 0
    count = max(len(s.findall("Measure")) for s in staves)

    for mi in range(count):
        voices: List[etree._Element] = []
        for staff in staves:
            ms = staff.findall("Measure")
            if mi >= len(ms) or not ms[mi].get("len"):
                continue
            found = ms[mi].findall("voice")
            voices.extend(found if found else [ms[mi]])
        if not voices:
            continue

        lengths = [(v, _voice_len(v)) for v in voices if _has_notes(v)]
        lengths = [(v, n) for v, n in lengths if n is not None]
        if len(lengths) < 3:
            continue                      # too few voices to call one of them odd

        agreed, votes = Counter(n for _, n in lengths).most_common(1)[0]
        if votes < len(lengths) - 1 or votes < 2:
            logger.debug("Measure %d: voices do not agree (%s), left alone",
                         mi + 1, [str(n) for _, n in lengths])
            continue

        for voice, filled in lengths:
            if filled < agreed:
                _pad(voice, agreed - filled)
                padded += 1
                logger.debug("Measure %d: padded a voice from %s to %s",
                             mi + 1, filled, agreed)
    return padded
