"""Voices that run past their barline: what MuseScore 4 refuses to open (#481).

MuseScore 4 checks a score as it reads it and refuses the whole file over one voice
longer than its bar ("Voice too long", or "Incomplete measure" for voice 1), and its
command line writes no `.mlog` saying where. MuseScore 3 has the same rule but runs
it only in the app, which `song_app.pipeline.musescore_check` asks it through. So for
MuseScore 4 the rule is kept here. A voice shorter than its bar passes: MuseScore
fills the gap with rests while reading.
"""

from fractions import Fraction
from typing import Dict, List

from lxml import etree

from .rejected_bars import _DOT, _DUR, _bar_lengths, _fraction


#: A chord carrying one of these is a grace note: it takes no time in the bar.
_GRACE = {"acciaccatura", "appoggiatura", "grace4", "grace8", "grace16", "grace32",
          "grace8after", "grace16after", "grace32after"}


def _voice_total(voice: etree._Element, bar: Fraction) -> Fraction:
    """A voice's length the way MuseScore 4 measures it after reading the bar.

    Every chord and rest counts its full length wherever it stands, and each stretch
    of the bar nothing covers counts too, because MuseScore fills it with a rest
    while reading. So a voice is longer than its bar when its notes run past the
    barline *or* overlap each other: a backward step (`location` with a negative
    fraction) that puts a note on top of the one before still adds that note's
    length. Measured on a real scan: staff 5 bar 33 reaches exactly the barline,
    yet MuseScore 4 calls it 8/2 long in a 6/2 bar, which is this sum.
    """
    total = Fraction(0)
    spans = []
    pos = Fraction(0)
    scales = [Fraction(1)]  # a tuplet inside a tuplet scales by both
    for el in voice:
        scale = scales[-1]
        if el.tag == "Tuplet":
            n, a = _fraction(el.findtext("normalNotes")), _fraction(el.findtext("actualNotes"))
            scales.append(scale * ((n / a) if (n and a) else Fraction(1)))
        elif el.tag == "endTuplet":
            if len(scales) > 1:
                scales.pop()
        elif el.tag == "location":
            pos += _fraction(el.findtext("fractions")) or 0
        elif el.tag in ("Chord", "Rest"):
            if el.tag == "Chord" and any(child.tag in _GRACE for child in el):
                continue
            kind = (el.findtext("durationType") or "").strip()
            if kind == "measure":
                # MuseScore 4 writes a bar rest's own length; 3 leaves it to the bar.
                length = _fraction(el.findtext("duration")) or bar
            elif kind in _DUR:
                dots = int((el.findtext("dots") or "0").strip() or 0)
                length = _DUR[kind] * _DOT.get(dots, Fraction(1)) * scale
            else:
                continue
            spans.append((pos, pos + length))
            total += length
            pos += length
    covered, reach = Fraction(0), Fraction(0)
    for start, end in sorted(spans):
        start, end = max(start, reach), min(end, bar)
        if end > start:
            covered += end - start
            reach = end
    return total + (bar - covered)


def _say(length: Fraction) -> str:
    return f"{length.numerator}/{length.denominator}"


def overlong_voices(root: etree._Element) -> List[Dict]:
    """Every voice that runs past its barline, in the order MuseScore reads them.

    MuseScore 4 checks a score as it reads it and refuses the whole file over one
    voice longer than its bar ("Voice too long" or, for voice 1, "Incomplete
    measure"), and its command line writes no `.mlog` saying where (#481). A voice
    shorter than its bar passes: MuseScore fills the gap with rests while reading.
    The bar's length is its `len`, else the time signature in force; a voice's is
    `_voice_total`'s. Grace notes take no time.

    Each finding is `{measure, staff, voice, found, expected, message}`, 1-based the way MuseScore
    counts (staff = position in the score, measure = every bar from the top), the
    shape `song_app.pipeline.musescore_check` returns.
    """
    found = []
    for staff_no, staff in enumerate(root.findall(".//Score/Staff"), start=1):
        lengths = _bar_lengths(staff)
        for measure_no, (measure, bar) in enumerate(zip(staff.findall("Measure"), lengths),
                                                    start=1):
            for voice_no, voice in enumerate(measure.findall("voice"), start=1):
                end = _voice_total(voice, bar)
                if end > bar:
                    found.append({
                        "measure": measure_no, "staff": staff_no, "voice": voice_no,
                        "found": _say(end), "expected": _say(bar),
                        "message": (f"Voice too long: measure {measure_no}, staff "
                                    f"{staff_no}, voice {voice_no}. Found: {_say(end)}. "
                                    f"Expected: {_say(bar)}."),
                    })
    return found
