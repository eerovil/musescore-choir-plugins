"""Repair measures where one voice falls short of the length the others agree on.

MuseScore writes `len="9/8"` on a measure whose content does not match the
prevailing meter. `preprocess_corrupted_measures` tries to fix these by shortening
final rests, but all-or-nothing: it gives up entirely if any over-long voice does
not end in a rest, and then the measure stays malformed for good.

This pass handles the other case, and takes the **prevailing time signature** as the
truth rather than a vote among the voices. A voice must already fill it exactly for
anything to happen — that witness is the evidence the override is wrong.

**It only touches things that are not music**: it removes a `location` gap (MuseScore
holding a voice's place) or a trailing rest, and only when that item accounts for the
overrun exactly. It never deletes a note, never shortens one, and never pads a voice
that has any.

One thing it writes rather than removes: a voice resting through the whole bar was
written to the length the override declared, so once the override goes that rest
overruns the corrected bar. MuseScore then *plays* the measure longer than it is
engraved, which no health check sees — it surfaced as a practice video the renderer
refused because a twelfth of the played notes had no highlight. Silence makes no
musical claim, so such a voice is re-lengthed to a measure rest of the real bar.

Those limits are the scars of getting this measure wrong twice. The first version
deleted a "repeated" notehead and destroyed a real note — the voice was singing six
syllables the page prints, and the lyric arithmetic caught it. The second decided a
majority of voices meant the bar really was 9/8 and padded the odd one out; that made
the file self-consistent at a length the page does not have. The bar is 4/4. What the
OCR actually did was add three unrelated things — a dot on one voice's first note, a
trailing rest on another, a gap on a third — and write `len="9/8"` to reconcile them.

A voice that can only be fixed by changing a note's duration is left alone and the
health check flags it. That is the honest outcome: the remaining issue points at the
one thing here that needs a person to look at the page.
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


def _drop_location_gap(voice: etree._Element, excess: Fraction) -> bool:
    """Remove a `location` gap that is exactly the overrun. A gap is not music."""
    for el in list(voice):
        if el.tag == "location" and _fraction(el.findtext("fractions")) == excess:
            voice.remove(el)
            return True
    return False


def _drop_trailing_rest(voice: etree._Element, excess: Fraction) -> bool:
    """Remove a final rest that is exactly the overrun. A rest carries no music."""
    kids = [e for e in voice if e.tag in ("Chord", "Rest")]
    if not kids or kids[-1].tag != "Rest":
        return False
    before = _voice_len(voice)
    voice.remove(kids[-1])
    if _voice_len(voice) == before - excess:
        return True
    voice.append(kids[-1])
    return False


def _resize_silent_voice(voice: etree._Element, target: Fraction) -> bool:
    """A voice of nothing but rests becomes one measure rest of `target`.

    Only for voices with no notes at all: a bar of silence says nothing about the
    music, so its length is bookkeeping rather than a musical claim.
    """
    rests = voice.findall("Rest")
    if not rests or voice.find("Chord") is not None:
        return False
    if _voice_len(voice) == target:
        return False
    for rest in rests[1:]:
        voice.remove(rest)
    keep = rests[0]
    # Only the length is ours to change: a hidden rest (<visible>0</visible>) or one
    # the engraver moved stays as it was, or a bar of silence would reappear on the
    # page because its duration was wrong.
    for child in keep.findall("durationType") + keep.findall("duration") + keep.findall("dots"):
        keep.remove(child)
    etree.SubElement(keep, "durationType").text = "measure"
    etree.SubElement(keep, "duration").text = f"{target.numerator}/{target.denominator}"
    return True


def fix_overfull_measures(root: etree._Element) -> int:
    """Strip non-musical padding from measures carrying a spurious `len`.

    Returns the number of measures whose override was removed.
    """
    staves = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None]
    if not staves:
        return 0
    count = max(len(s.findall("Measure")) for s in staves)

    # Prevailing meter per measure, carried forward from the last TimeSig seen.
    meters: Dict[int, Fraction] = {}
    meter = Fraction(4, 4)
    for mi in range(count):
        for staff in staves:
            ms = staff.findall("Measure")
            if mi < len(ms):
                ts = ms[mi].find(".//TimeSig")
                if ts is not None and ts.findtext("sigN") and ts.findtext("sigD"):
                    meter = Fraction(int(ts.findtext("sigN")), int(ts.findtext("sigD")))
                    break
        meters[mi] = meter

    fixed = 0
    for mi in range(count):
        target = meters[mi]
        marked = [(s, s.findall("Measure")[mi]) for s in staves
                  if mi < len(s.findall("Measure")) and s.findall("Measure")[mi].get("len")]
        if not marked:
            continue

        voices = [v for _, m in marked for v in (m.findall("voice") or [m])]
        lengths = [(v, _voice_len(v)) for v in voices if _has_notes(v)]
        lengths = [(v, n) for v, n in lengths if n is not None]
        if not any(n == target for _, n in lengths):
            logger.debug("Measure %d: no voice fills %s, len override kept", mi + 1, target)
            continue

        for voice, filled in lengths:
            if filled <= target:
                continue
            excess = filled - target
            if _drop_location_gap(voice, excess):
                logger.debug("Measure %d: dropped a %s location gap", mi + 1, excess)
            elif _drop_trailing_rest(voice, excess):
                logger.debug("Measure %d: dropped a %s trailing rest", mi + 1, excess)
            else:
                logger.debug("Measure %d: a voice overruns %s by %s and only a note "
                             "edit would fix it — left for the health check",
                             mi + 1, target, excess)

        now = [_voice_len(v) for v, _ in lengths]
        if sum(1 for n in now if n == target) * 2 > len(now):
            for _, m in marked:
                del m.attrib["len"]
                # A staff resting through the bar was written to the old, wrong length
                # (a whole rest under len="4/4"). Left alone it now overruns the bar the
                # override was hiding, and MuseScore plays the measure longer than it is
                # engraved — which is not a health issue, it is a video that drifts out
                # of sync. Silence carries no music, so re-length it to the real bar.
                for voice in m.findall("voice"):
                    _resize_silent_voice(voice, target)
            fixed += 1
            logger.debug("Measure %d: len override removed, nominal is %s", mi + 1, target)
    return fixed
