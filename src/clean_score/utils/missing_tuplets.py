#!/usr/bin/env python3
"""
Auto-fix OCR'd measures where a tuplet bracket was dropped from one voice but a
parallel voice (any staff, same measure) kept it.

Pattern: a real score has the same rhythm in two parts (e.g. T1 and T2), one with
a triplet. OCR detects the triplet on one voice but emits the other voice's notes
as plain (untupleted) durations, so that voice's measure no longer adds up. We
repair it by copying the well-formed tuplet onto the broken voice's matching run
of notes, then padding any leftover with a trailing rest.

This only fires when a *donor* tuplet exists at the same measure index, same tick
position, same base duration and note count — i.e. the OCR clearly just lost a
bracket on one of two parallel voices. It never guesses a tuplet out of thin air.
"""

import logging
from copy import deepcopy
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from lxml import etree

logger = logging.getLogger(__name__)

# Duration type -> fraction of a whole note (Division-independent).
_BASE = {
    "whole": Fraction(1),
    "half": Fraction(1, 2),
    "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8),
    "16th": Fraction(1, 16),
    "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64),
    "128th": Fraction(1, 128),
}
# Largest-first, for decomposing a leftover duration into rests.
_REST_UNITS = sorted(_BASE.items(), key=lambda kv: kv[1], reverse=True)


def _dur(el: etree._Element) -> Fraction:
    """Notated (un-scaled) duration of a Chord/Rest as a fraction of a whole note."""
    base = _BASE.get(el.findtext("durationType"), Fraction(0))
    dots = el.findtext("dots")
    if dots and dots.isdigit():
        add = base
        for _ in range(int(dots)):
            add /= 2
            base += add
    return base


def _expected(measure: etree._Element, current: Fraction) -> Fraction:
    """Measure length in whole-note units, honouring TimeSig and a `len` override."""
    ts = measure.find(".//TimeSig")
    if ts is not None:
        n, d = ts.findtext("sigN"), ts.findtext("sigD")
        if n and d:
            current = Fraction(int(n), int(d))
    length = measure.get("len")
    if length and "/" in length:
        n, d = length.split("/")
        current = Fraction(int(n), int(d))
    return current


class _TupletInfo:
    __slots__ = ("pos", "normal", "actual", "base", "element")

    def __init__(self, pos, normal, actual, base, element):
        self.pos = pos
        self.normal = normal
        self.actual = actual
        self.base = base
        self.element = element


def _scan_voice(voice: etree._Element):
    """
    Walk a voice once.

    Returns (total, notes, tuplets) where:
      total   = summed scaled duration (Fraction of a whole note)
      notes   = list of (element, start_pos, in_tuplet) for each Chord/Rest
      tuplets = list of _TupletInfo for tuplet groups found in this voice
    """
    pos = Fraction(0)
    scale = Fraction(1)
    in_tuplet = False
    notes: List[Tuple[etree._Element, Fraction, bool]] = []
    tuplets: List[_TupletInfo] = []
    for el in voice:
        if el.tag == "Tuplet":
            normal = int(el.findtext("normalNotes") or 1)
            actual = int(el.findtext("actualNotes") or 1)
            tuplets.append(_TupletInfo(pos, normal, actual, el.findtext("baseNote"), el))
            scale = Fraction(normal, actual)
            in_tuplet = True
        elif el.tag == "endTuplet":
            scale = Fraction(1)
            in_tuplet = False
        elif el.tag == "location":
            frac = el.findtext("fractions")
            if frac and "/" in frac:
                n, d = frac.split("/")
                pos += Fraction(int(n), int(d))
        elif el.tag in ("Chord", "Rest"):
            notes.append((el, pos, in_tuplet))
            pos += _dur(el) * scale
    return pos, notes, tuplets


def _make_rests(deficit: Fraction) -> List[etree._Element]:
    """Decompose a leftover duration into the fewest simple rests (largest first)."""
    rests: List[etree._Element] = []
    remaining = deficit
    for name, size in _REST_UNITS:
        while remaining >= size:
            rest = etree.Element("Rest")
            etree.SubElement(rest, "durationType").text = name
            rests.append(rest)
            remaining -= size
        if remaining == 0:
            break
    return rests


def fix_missing_tuplets(root: etree._Element) -> int:
    """
    Repair voices that are short a tuplet bracket present on a parallel voice.

    Returns the number of measures fixed. Operates in place.
    """
    staves = root.findall(".//Score/Staff")
    if not staves:
        return 0

    # Index every tuplet group by measure index so a broken voice on any staff can
    # find a donor at the same position.
    donors: Dict[int, List[_TupletInfo]] = {}
    for staff in staves:
        for mi, measure in enumerate(staff.findall("Measure")):
            for voice in measure.findall("voice"):
                _, _, tuplets = _scan_voice(voice)
                if tuplets:
                    donors.setdefault(mi, []).extend(tuplets)

    fixed = 0
    for staff in staves:
        sid = staff.get("id", "?")
        sig = Fraction(4, 4)
        for mi, measure in enumerate(staff.findall("Measure")):
            sig = _expected(measure, sig)
            if mi not in donors:
                continue
            for voice in measure.findall("voice"):
                total, notes, own = _scan_voice(voice)
                if own:
                    continue  # already has its own tuplet(s); leave it alone
                if total == sig or not notes:
                    continue  # well-formed or empty
                if _try_wrap(voice, notes, donors[mi]):
                    # Re-pad to the expected length after wrapping.
                    new_total, _, _ = _scan_voice(voice)
                    if new_total < sig:
                        for rest in _make_rests(sig - new_total):
                            voice.append(rest)
                    fixed += 1
                    logger.info(
                        "Auto-fixed missing tuplet on staff %s measure %d "
                        "(was %s, now %s of a whole).",
                        sid, mi + 1, total, sig,
                    )
    return fixed


def _try_wrap(
    voice: etree._Element,
    notes: List[Tuple[etree._Element, Fraction, bool]],
    candidates: List[_TupletInfo],
) -> bool:
    """
    Find a donor tuplet whose run this voice can mirror, and wrap it. Returns True
    on success. A match needs the same start tick, base duration, and at least the
    donor's note count of consecutive plain notes of that duration.
    """
    for donor in candidates:
        run: List[etree._Element] = []
        for el, pos, in_tup in notes:
            if in_tup:
                continue
            if not run:
                if pos == donor.pos and el.findtext("durationType") == donor.base \
                        and not (el.findtext("dots") or "").strip("0"):
                    run.append(el)
            else:
                if el.findtext("durationType") == donor.base \
                        and not (el.findtext("dots") or "").strip("0"):
                    run.append(el)
                else:
                    break
            if len(run) == donor.actual:
                break
        if len(run) != donor.actual:
            continue
        first = run[0]
        last = run[-1]
        start = voice.index(first)
        # Include an immediately-preceding Beam inside the tuplet (matches MuseScore).
        if start > 0 and voice[start - 1].tag == "Beam":
            start -= 1
        tuplet_el = deepcopy(donor.element)
        voice.insert(start, tuplet_el)
        voice.insert(voice.index(last) + 1, etree.Element("endTuplet"))
        return True
    return False
