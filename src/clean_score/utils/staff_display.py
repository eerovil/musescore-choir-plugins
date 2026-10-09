"""What a staff of its own has to draw that a shared staff did not (#354).

A scan reads a choir staff two voices at a time, and some of what it writes is
right only while the two share that staff. Once each voice has a staff of its own
-- after the split, or the per-system rebuild -- three things draw wrong, and none
of them changes a note, a length or a word:

- **A rest the page prints once for both voices** is written into the second voice
  *hidden* (homr's ``print-object="no"``), which is right on a shared staff. On the
  lower part's own staff that rest is simply missing from the picture.
- **The bar's closing barline** is written where the voice that was read last
  stopped. When that voice ended early because the page prints its last rest once
  for both, MuseScore puts the barline part-way through the bar, and the rest after
  it reads as a bar of its own.
- **A double, final or repeat barline** belongs to the voice it was read with, so
  the staff that took the other voice draws a plain one. And homr reads some final
  barlines in a style MuseScore 3 imports as a plain ``<BarLine/>``, which on the
  last bar overrides the final barline MuseScore otherwise draws there by itself.

:func:`fix_staff_display` runs at the end of cleaning, and again on the copy every
preview and video renders from, so a score cleaned before it existed is drawn right
without being cleaned again (which would cost its lyrics).
"""

from __future__ import annotations

import copy
from typing import Dict, List, Optional

from lxml import etree

_MUSIC = ("Chord", "Rest")


def _voices_with_music(measure: etree._Element) -> List[etree._Element]:
    return [v for v in measure.findall("voice") if any(c.tag in _MUSIC for c in v)]


def show_lone_rests(root: etree._Element) -> int:
    """Make the hidden rests visible in a bar that has only one voice. Returns how many."""
    shown = 0
    for measure in root.iterfind(".//Score/Staff/Measure"):
        voices = _voices_with_music(measure)
        if len(voices) != 1:
            continue
        for rest in voices[0].findall("Rest"):
            hidden = [v for v in rest.iter("visible") if (v.text or "").strip() == "0"]
            if not hidden:
                continue
            for v in hidden:
                v.getparent().remove(v)
            shown += 1
    return shown


def barlines_to_bar_end(root: etree._Element) -> int:
    """Move a barline that has music after it in its voice to the end of the voice."""
    moved = 0
    for voice in root.iterfind(".//Score/Staff/Measure/voice"):
        for barline in voice.findall("BarLine"):
            after = barline.itersiblings()
            if any(sibling.tag in _MUSIC for sibling in after):
                voice.remove(barline)
                voice.append(barline)
                moved += 1
    return moved


def _subtype(barline: etree._Element) -> str:
    return (barline.findtext("subtype") or "normal").strip() or "normal"


def _end_barline(measure: etree._Element) -> Optional[etree._Element]:
    """The barline closing the bar: the last thing in its first voice."""
    voice = measure.find("voice")
    if voice is None or not len(voice):
        return None
    last = voice[-1]
    return last if last.tag == "BarLine" else None


def drop_plain_final_barlines(root: etree._Element) -> int:
    """Take a plain barline off the last bar, so MuseScore draws its final barline."""
    dropped = 0
    for staff in root.iterfind(".//Score/Staff"):
        measures = staff.findall("Measure")
        if not measures:
            continue
        for barline in measures[-1].iter("BarLine"):
            if _subtype(barline) == "normal":
                barline.getparent().remove(barline)
                dropped += 1
    return dropped


def share_end_barlines(root: etree._Element) -> int:
    """Give every staff of a bar the bar's double or repeat barline.

    In a choir score the style of a barline runs through the whole system, so a
    staff that has none in a bar where another has one lost it in the split.
    Only a staff with no barline of its own in that bar is given one. A final
    (``end``) barline is never shared: on the last bar MuseScore draws one by
    itself, and copying it there changed every ordinary score for nothing, so
    every video rendered from a copy. One inside the score is rare in this
    repertoire and left as the scan read it.
    """
    staves = [s.findall("Measure") for s in root.iterfind(".//Score/Staff")]
    staves = [m for m in staves if m]
    if len(staves) < 2:
        return 0
    added = 0
    for index in range(min(len(m) for m in staves)):
        bars = [m[index] for m in staves]
        styled = next((b for b in (_end_barline(bar) for bar in bars)
                       if b is not None and _subtype(b) != "normal"
                       and _subtype(b) != "end"), None)
        if styled is None:
            continue
        for bar in bars:
            voice = bar.find("voice")
            if voice is None or bar.find(".//BarLine") is not None:
                continue
            voice.append(copy.deepcopy(styled))
            added += 1
    return added


def fix_staff_display(root: etree._Element) -> Dict[str, int]:
    """All of the above, in the order they depend on. Running it twice changes nothing."""
    return {
        "rests": show_lone_rests(root),
        "moved": barlines_to_bar_end(root),
        "dropped": drop_plain_final_barlines(root),
        "shared": share_end_barlines(root),
    }
