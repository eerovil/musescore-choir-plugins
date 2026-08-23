"""Apply score edits that a person or an AI authorised, from a recorded list.

Some OCR damage cannot be repaired automatically and should not be guessed at: a
dot the scanner invented, a melisma slur it dropped. The automatic passes decline
these on purpose — `overfull_measures` will not touch a note, and slurs are never
mirrored because they connect different pitches and cannot be pitch-checked.

What is left is a judgement about the printed page, and the answer belongs in the
song folder as data rather than in a one-off hand edit that the next rebuild
erases. Each entry carries a `why`, because six months later the diff will not say
why a note lost its dot.

    [{"kind": "undot", "staff": 3, "measure": 26, "index": 0, "why": "..."},
     {"kind": "slur",  "staff": 4, "measure": 32, "index": 0, "span": 1, "why": "..."}]

`staff` and `measure` are 1-based and refer to the **cleaned** score, where each
staff carries one voice. `index` counts chords in that measure from 0. Applying is
strict: an entry that does not match raises, because a silently skipped fix would
leave the score looking repaired when it is not.
"""
import logging
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


class FixError(ValueError):
    """A recorded fix does not match the score it was recorded against."""


def _chords(root: etree._Element, staff_id: int, measure_no: int) -> List[etree._Element]:
    staves = [s for s in root.findall(".//Score/Staff")
              if s.get("id") == str(staff_id) and s.find("Measure") is not None]
    if not staves:
        raise FixError(f"no staff {staff_id}")
    measures = staves[0].findall("Measure")
    if measure_no < 1 or measure_no > len(measures):
        raise FixError(f"staff {staff_id} has no measure {measure_no}")
    measure = measures[measure_no - 1]
    body = measure.find("voice") if measure.find("voice") is not None else measure
    return [el for el in body if el.tag == "Chord"]


def _length(chord: etree._Element) -> Fraction:
    base = _DUR.get((chord.findtext("durationType") or "").strip(), Fraction(0))
    dots = int((chord.findtext("dots") or "0").strip() or 0)
    return base * _DOT.get(dots, Fraction(1))


def _undot(chord: etree._Element) -> str:
    dots = chord.find("dots")
    if dots is None:
        raise FixError("that chord has no dot to remove")
    chord.remove(dots)
    return "removed a dot"


def _slur(chords: List[etree._Element], start: int, span: int) -> str:
    """Slur chord `start` to chord `start + span`, so the later ones carry no syllable."""
    end = start + span
    if end >= len(chords):
        raise FixError(f"cannot slur {span} past chord {start}: only {len(chords)} chords")
    distance = sum(_length(chords[i]) for i in range(start, end))

    head = etree.SubElement(chords[start], "Spanner", type="Slur")
    etree.SubElement(etree.SubElement(head, "Slur"), "up").text = "up"
    loc = etree.SubElement(etree.SubElement(head, "next"), "location")
    etree.SubElement(loc, "fractions").text = str(distance)

    tail = etree.SubElement(chords[end], "Spanner", type="Slur")
    loc = etree.SubElement(etree.SubElement(tail, "prev"), "location")
    etree.SubElement(loc, "fractions").text = f"-{distance}"
    return f"slurred {span} note(s) from chord {start}"


def apply_fixes(root: etree._Element, fixes: List[Dict]) -> List[str]:
    """Apply each recorded fix. Returns one line per fix, for the build log."""
    done: List[str] = []
    for fix in fixes:
        kind = fix.get("kind")
        staff, measure = int(fix["staff"]), int(fix["measure"])
        index = int(fix.get("index", 0))
        chords = _chords(root, staff, measure)
        if index >= len(chords):
            raise FixError(
                f"staff {staff} m{measure} has {len(chords)} chords, no index {index}")
        if kind == "undot":
            what = _undot(chords[index])
        elif kind == "slur":
            what = _slur(chords, index, int(fix.get("span", 1)))
        else:
            raise FixError(f"unknown fix kind {kind!r}")
        line = f"staff {staff} m{measure}: {what} — {fix.get('why', 'no reason recorded')}"
        logger.debug(line)
        done.append(line)
    return done
