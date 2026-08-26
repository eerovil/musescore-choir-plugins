"""Forcing measure width to follow the music's length rather than its density.

Verovio spaces a measure by what is *in* it: a bar of sixteenths with lyrics
under every note comes out far wider per beat than a bar of two half notes. On
this repertoire that is a 1.9x spread, and since the scroll follows the notes,
the video speeds up and slows down with it.

The fix is the one `add_rest_track.qml` already used in MuseScore: a staff of
evenly spaced rests. Every measure then contains the same number of rest slots
per beat, and verovio's minimum spacing per slot sets a floor that makes width
follow beats. Here the staff is injected into the MusicXML — after MuseScore has
produced it, so it never reaches the MIDI or the audio — and cropped back off
the bottom of the rendered strip.

It does not make spacing exactly proportional: a measure whose own content needs
more room than the rest slots demand still gets it. A 32nd-note grid keeps the
ordinary 4-note/32-note case within ~1.3x, and `timing.smooth_scroll` absorbs what
is left.
"""

from __future__ import annotations

import copy
from typing import Optional

from lxml import etree

SPACER_ID = "P-SCROLLVIDEO-SPACER"

# Rests per quarter note. The default covers 32nd-note passages: a coarser floor
# still lets a busy measure become several times wider than an equally long sparse
# one, which is exactly the speed change this staff exists to prevent.
DEFAULT_PER_QUARTER = 8
_TYPE = {1: "quarter", 2: "eighth", 4: "16th", 8: "32nd"}


def _measure_length(measure: etree._Element) -> int:
    """Length of a MusicXML measure in divisions (chord notes don't advance time)."""
    total = 0
    for note in measure.findall("note"):
        if note.find("chord") is None:
            total += int(note.findtext("duration") or 0)
    return total


def add_spacer_staff(musicxml_path: str, out_path: str,
                     per_quarter: int = DEFAULT_PER_QUARTER) -> Optional[str]:
    """Write `musicxml_path` to `out_path` with a rest-only spacer part appended.

    Returns out_path, or None when no spacer could be built (in which case the
    caller should engrave the original unchanged).
    """
    if per_quarter not in _TYPE:
        raise ValueError(f"per_quarter must be one of {sorted(_TYPE)}, got {per_quarter}")

    tree = etree.parse(musicxml_path)
    root = tree.getroot()
    first = root.find("part")
    part_list = root.find("part-list")
    if first is None or part_list is None:
        return None

    measures = []
    source_divisions = None
    for source in first.findall("measure"):
        divisions_text = source.findtext("attributes/divisions")
        if divisions_text:
            source_divisions = int(divisions_text)
        if source_divisions is None:
            return None
        measures.append((source, source_divisions))

    score_part = etree.SubElement(part_list, "score-part")
    score_part.set("id", SPACER_ID)
    etree.SubElement(score_part, "part-name").text = "Spacer"

    part = etree.SubElement(root, "part")
    part.set("id", SPACER_ID)
    for index, (source, source_divisions) in enumerate(measures):
        measure = etree.SubElement(part, "measure")
        if source.get("number"):
            measure.set("number", source.get("number"))
        if index == 0:
            attributes = etree.SubElement(measure, "attributes")
            # A MusicXML part owns its divisions. Give the spacer one unit per
            # requested slot instead of inheriting a coarse source grid that may
            # be unable to express 32nd rests at all.
            etree.SubElement(attributes, "divisions").text = str(per_quarter)
            time = source.find(".//time")
            if time is not None:
                attributes.append(copy.deepcopy(time))
            clef = etree.SubElement(attributes, "clef")
            etree.SubElement(clef, "sign").text = "percussion"
        slots = _measure_length(source) * per_quarter // source_divisions
        for _ in range(max(1, slots)):
            note = etree.SubElement(measure, "note")
            etree.SubElement(note, "rest")
            etree.SubElement(note, "duration").text = "1"
            etree.SubElement(note, "voice").text = "1"
            etree.SubElement(note, "type").text = _TYPE[per_quarter]

    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path


def visible_height(layout) -> float:
    """Page height in verovio units with the spacer staff (the bottom one) cut off.

    Cut just above the spacer's top staff line. The last singing staff's lyrics sit
    in the gap above that line, so the margin has to be small — only enough to clear
    the line's own stroke. A generous margin eats the lyrics instead.
    """
    if len(layout.staff_tops) < 2:
        return layout.height
    spacer_top = layout.staff_tops[-1]
    spacing = next((g.staff_spacing for g in layout.notes.values()
                    if g.staff_top == layout.staff_tops[-2]), None)
    margin = 0.15 * spacing if spacing else 0.0
    return max(1.0, min(layout.height, spacer_top - margin))
