"""Write a rest that fills its bar alone as a bar rest, so MuseScore centres it (#298).

MuseScore has two ways to say a voice rests through a bar: a bar rest
(`durationType` `measure`), which it centres, and an ordinary rest as long as the bar,
which it places at the start of the bar like a note. Its own MusicXML import writes the
second even when the file says `<rest measure="yes"/>` — Meri's source has 13 and its
converted score 13 ordinary whole rests — and the split copies each one onto both
staves. Deleting one in MuseScore puts a bar rest back, which is why it then jumps to
the middle.

Only the length is rewritten: a rest that is hidden or carries anything else keeps it.
A rest that does not fill the bar exactly (a whole rest under 3/4), a dotted one, one
in a tuplet, or one a `location` moves off the start of the bar is left alone; the bar-length passes before this one deal with those.
"""
from lxml import etree

from .rejected_bars import _DUR, _fraction


def _bar_lengths(staff: etree._Element):
    """Per bar: its length, spelt the way the score spells it (`4/4`, not `1/1`)."""
    spelt = "4/4"
    out = []
    for measure in staff.findall("Measure"):
        ts = measure.find(".//TimeSig")
        if ts is not None and ts.findtext("sigN") and ts.findtext("sigD"):
            spelt = f"{ts.findtext('sigN').strip()}/{ts.findtext('sigD').strip()}"
        own = measure.get("len")
        text = own.strip() if _fraction(own) else spelt
        out.append((_fraction(text), text))
    return out


def _lone_rest(voice: etree._Element):
    # A `location` moves the voice's position, so a rest after one does not start the
    # bar, and making it a bar rest would move it rather than only re-spell it.
    events = [el for el in voice if el.tag in ("Chord", "Rest", "Tuplet", "location")]
    if len(events) != 1 or events[0].tag != "Rest":
        return None
    rest = events[0]
    if rest.find("dots") is not None or rest.find("Tuplet") is not None:
        return None
    return rest


def centre_measure_rests(root: etree._Element) -> int:
    """Turn every rest that alone fills its bar into a bar rest; return how many."""
    changed = 0
    for staff in root.iter("Staff"):
        measures = staff.findall("Measure")
        if not measures:
            continue
        for measure, (bar, spelt) in zip(measures, _bar_lengths(staff)):
            for voice in measure.findall("voice"):
                rest = _lone_rest(voice)
                if rest is None:
                    continue
                kind = (rest.findtext("durationType") or "").strip()
                if _DUR.get(kind) != bar:
                    continue
                for child in rest.findall("durationType") + rest.findall("duration"):
                    rest.remove(child)
                etree.SubElement(rest, "durationType").text = "measure"
                etree.SubElement(rest, "duration").text = spelt
                changed += 1
    return changed
