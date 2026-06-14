"""Remove spurious OCR time-signature changes.

OCR sometimes inserts a TimeSig change (e.g. 2/4) at a measure whose actual note
content is a different, consistent meter (e.g. 4/4). MuseScore then renders that
measure and every measure until the next change as over/under-full, which the user
has to fix by hand.

This pass removes a TimeSig change only when there is STRONG evidence it's bogus: the
*run* of measures it governs (from the change until the next change) has at least
`_MIN_RUN` informative measures that all match the *prevailing* meter, and none that
match the *declared* one. A single contradicting measure is left alone — that's far
more likely an over-full/under-full content error in one bar (the timesig is real)
than a spurious marker, and deleting a genuine isolated change (e.g. a real 3/4 bar
whose notes were OCR'd as 4/4) would be worse than leaving it.

It never touches the first signature, and removes confirmed-bogus markers from every
staff so they stay consistent. All durations are exact whole-note Fractions so
tuplets/dots don't mis-measure.
"""

from __future__ import annotations

import logging
from fractions import Fraction
from typing import Optional

from lxml import etree

logger = logging.getLogger(__name__)

_DUR = {
    "whole": Fraction(1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}

# Minimum informative measures in a change's run that must contradict it (and match
# the prevailing meter) before we treat the change as a spurious OCR marker.
_MIN_RUN = 2


def _voice_timed_len(voice: etree._Element) -> Optional[Fraction]:
    """Summed length of timed notes/rests in a voice; None if it's only a measure rest."""
    total = Fraction(0)
    scale = Fraction(1)
    has_timed = False
    for el in voice:
        if el.tag == "Tuplet":
            try:
                a = int(el.findtext("actualNotes") or 1)
                n = int(el.findtext("normalNotes") or 1)
                scale = Fraction(n, a) if a else Fraction(1)
            except ValueError:
                scale = Fraction(1)
        elif el.tag == "endTuplet":
            scale = Fraction(1)
        elif el.tag in ("Chord", "Rest"):
            dt = (el.findtext("durationType") or "").strip()
            if dt == "measure":
                continue  # full-measure rest carries no meter information
            base = _DUR.get(dt)
            if base is None:
                continue
            de = el.find("dots")
            if de is not None and (de.text or "").strip().isdigit():
                base = base * _DOT.get(int(de.text.strip()), Fraction(1))
            total += base * scale
            has_timed = True
    return total if has_timed else None


def _measure_content_len(measure: etree._Element) -> Optional[Fraction]:
    """Best estimate of a measure's filled length (max over its note-bearing voices)."""
    best: Optional[Fraction] = None
    for voice in measure.findall("voice"):
        v = _voice_timed_len(voice)
        if v is not None and v > 0:
            best = v if best is None else max(best, v)
    return best


def fix_spurious_timesigs(root: etree._Element) -> int:
    """Remove OCR time-signature changes contradicted by the note content.

    Returns the number of (bogus) signature changes removed.
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    if score is None:
        return 0
    staves = score.findall("Staff")
    if not staves:
        return 0
    measures = [s.findall("Measure") for s in staves]
    n = min((len(m) for m in measures), default=0)

    def declared_at(mi: int) -> Optional[Fraction]:
        for ms in measures:
            ts = ms[mi].find(".//TimeSig")
            if ts is not None:
                try:
                    return Fraction(int(ts.findtext("sigN")), int(ts.findtext("sigD")))
                except (ValueError, TypeError):
                    return None
        return None

    def content_at(mi: int) -> Optional[Fraction]:
        best: Optional[Fraction] = None
        for ms in measures:
            c = _measure_content_len(ms[mi])
            if c is not None and c > 0:
                best = c if best is None else max(best, c)
        return best

    changes = [(mi, declared_at(mi)) for mi in range(n) if declared_at(mi) is not None]

    effective = Fraction(4, 4)
    removed = 0
    for k, (mi, declared) in enumerate(changes):
        if mi == 0:
            effective = declared  # the initial signature is always kept
            continue
        run_end = changes[k + 1][0] if k + 1 < len(changes) else n
        contents = [c for j in range(mi, run_end) if (c := content_at(j)) is not None]
        match_prev = sum(1 for c in contents if c == effective)
        match_decl = sum(1 for c in contents if c == declared)

        if declared != effective and match_prev >= _MIN_RUN and match_prev > match_decl:
            # The run is dominated by measures matching the prevailing meter rather
            # than the declared one — almost certainly a spurious OCR marker (one or
            # two stray bars matching the declared sig don't save it). A genuine change
            # (e.g. a real 3/4 section) instead has most of its run match the declared
            # sig, so match_decl wins and it's kept. Remove from every staff.
            for ms in measures:
                for ts in ms[mi].findall(".//TimeSig"):
                    parent = ts.getparent()
                    if parent is not None:
                        parent.remove(ts)
            removed += 1
            logger.info(
                "Removed spurious %s time signature at measure %d "
                "(%d measures of its run match the prevailing meter %s, %d match %s)",
                declared, mi + 1, match_prev, effective, match_decl, declared,
            )
        else:
            effective = declared  # genuine, isolated, or ambiguous — keep it
    return removed
