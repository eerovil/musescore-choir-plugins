"""Red notes in the score, on the bars cleaning had to change (#238).

Some damage a scan leaves cannot be repaired from the score alone: a slur joining two
singers, a bar running longer than its time signature. Cleaning takes the damage out so
the score plays and opens, and says so **in the bar** with a red staff text starting
with `⚠`. That is where a person fixing the score in MuseScore is looking, and deleting
the note is how they say the bar is done: the health check lists every one still there.

The video renderer strips them, so a forgotten one never reaches a practice track.
"""
from typing import Dict, List

from lxml import etree

#: What every mark starts with; it is how a mark is told apart from a printed text.
PREFIX = "⚠ "
_RED = {"r": "255", "g": "0", "b": "0", "a": "255"}


def _is_mark(el: etree._Element) -> bool:
    return el.tag == "StaffText" and (el.findtext("text") or "").startswith(PREFIX)


def mark_bar(measure: etree._Element, text: str) -> bool:
    """Put a red `⚠ text` at the head of this staff's bar. False if it is already there."""
    text = PREFIX + text
    voice = measure.find("voice")
    if voice is None:
        voice = etree.SubElement(measure, "voice")
    if any(_is_mark(el) and el.findtext("text") == text for el in voice):
        return False
    mark = etree.Element("StaffText")
    etree.SubElement(mark, "color", **_RED)
    etree.SubElement(mark, "text").text = text
    # Before the first thing that takes time, so it sits at the start of the bar.
    index = next((i for i, el in enumerate(voice)
                  if el.tag in ("Chord", "Rest", "location", "Tuplet")), len(voice))
    voice.insert(index, mark)
    return True


def marks(root: etree._Element) -> List[Dict]:
    """Every mark in the score: `{staff, measure, text}`, 1-based, text without `⚠`."""
    out = []
    score = root if root.tag == "Score" else root.find(".//Score")
    for si, staff in enumerate(score.findall("Staff") if score is not None else [], start=1):
        for mi, measure in enumerate(staff.findall("Measure"), start=1):
            for el in measure.iter("StaffText"):
                if _is_mark(el):
                    out.append({"staff": si, "measure": mi,
                                "text": el.findtext("text")[len(PREFIX):]})
    return out


def strip_marks(root: etree._Element) -> int:
    """Remove every mark. Returns how many."""
    found = [el for el in root.iter("StaffText") if _is_mark(el)]
    for el in found:
        el.getparent().remove(el)
    return len(found)
