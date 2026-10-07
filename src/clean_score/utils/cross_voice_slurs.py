"""Take out slurs the scan ran from one voice into another (#238).

homr pairs a slur's start and stop by the staff's number, not the voice's, so on a
staff two singers share it can start a slur in one voice and stop it in the other —
Sangerhilsen bars 10→11 and 26→27, where the page prints a tie on the lower voice
alone. After the split each voice is its own staff, so the two halves point at
nothing: the end half still makes its note a slur continuation (that singer loses a
syllable) and the start half makes the slur recorder call the note "already slurred".

A slur belongs to one singer, so a half whose other end is in another voice or on
another staff is wrong whatever the page says. Both halves are removed and both bars
are marked; which slur (or tie) the page really prints is a person's call.
"""
import json
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from lxml import etree

from .problem_marks import mark_bar
from .rejected_bars import _bar_lengths, _fraction, _walk, staff_names
from .score_fixes import note_name


def _offset(loc: etree._Element, tag: str) -> int:
    return int((loc.findtext(tag) or "0").strip() or 0)


def _resolve(home: int, pos: Fraction, loc: etree._Element,
             lengths: List[Fraction]) -> Tuple[int, Fraction]:
    """(bar, position) a location points at, carrying past either end of a bar."""
    index = home + _offset(loc, "measures")
    at = pos + (_fraction(loc.findtext("fractions")) or 0)
    while at < 0 and 0 < index <= len(lengths):
        index -= 1
        at += lengths[index]
    while 0 <= index < len(lengths) and at >= lengths[index]:
        at -= lengths[index]
        index += 1
    return index, at


def _first_note(chord: Optional[etree._Element]) -> str:
    note = chord.find("Note") if chord is not None else None
    pitch = note.findtext("pitch") if note is not None else None
    if not pitch or not pitch.strip().isdigit():
        return ""
    tpc = note.findtext("tpc")
    return note_name(int(pitch), int(tpc) if tpc and tpc.strip().lstrip("-").isdigit() else None)


def _halves(root: etree._Element) -> List[Dict]:
    """Every slur half whose other end is in another voice or on another staff."""
    found = []
    for si, staff in enumerate(root.findall(".//Score/Staff"), start=1):
        lengths = _bar_lengths(staff)
        for mi, measure in enumerate(staff.findall("Measure")):
            for voice in measure.findall("voice"):
                for pos, el in _walk(voice, lengths[mi]):
                    if el.tag == "Spanner" and el.get("type") == "Slur":
                        held = [(voice, el, None)]
                    elif el.tag == "Chord":
                        held = [(el, sp, el) for sp in el.findall("Spanner[@type='Slur']")]
                    else:
                        continue
                    for parent, spanner, chord in held:
                        for side in ("next", "prev"):
                            loc = spanner.find(f"{side}/location")
                            if loc is None or not (_offset(loc, "voices") or _offset(loc, "staves")):
                                continue
                            found.append({
                                "parent": parent, "spanner": spanner, "side": side,
                                "staff": si, "bar": mi, "pos": pos,
                                "target": _resolve(mi, pos, loc, lengths),
                                "note": _first_note(chord if chord is not None else
                                                    _next_chord(voice, el)),
                            })
    return found


def _next_chord(voice: etree._Element, el: etree._Element) -> Optional[etree._Element]:
    for sib in el.itersiblings():
        if sib.tag == "Chord":
            return sib
    return None


def drop_cross_voice_slurs(root: etree._Element) -> List[Dict]:
    """Remove those slurs and mark their bars. Returns one record per slur.

    A record is `{measure, staff, part, note, pos, end_measure, end_staff, end_part,
    end_note, end_pos}`, 1-based, `pos` a fraction of a whole note into the bar; a half whose partner is not found has its own record with the
    other side's fields `None` (`measure`... for a lone end half, `end_`... for a start).
    """
    halves = _halves(root)
    names = staff_names(root)
    starts = [h for h in halves if h["side"] == "next"]
    ends = [h for h in halves if h["side"] == "prev"]
    records = []
    for start in starts:
        end = next((e for e in ends
                    if e["target"] == (start["bar"], start["pos"])
                    and start["target"] == (e["bar"], e["pos"])), None)
        if end is not None:
            ends.remove(end)
        records.append(_record(start, end, names))
    records.extend(_record(None, end, names) for end in ends)

    for half in halves:
        if half["spanner"].getparent() is half["parent"]:
            half["parent"].remove(half["spanner"])
    staves = root.findall(".//Score/Staff")
    for rec in records:
        start_at = f"{rec['part']} bar {rec['measure']}" if rec["measure"] else None
        end_at = f"{rec['end_part']} bar {rec['end_measure']}" if rec["end_measure"] else None
        if rec["measure"]:
            mark_bar(staves[rec["staff"] - 1].findall("Measure")[rec["measure"] - 1],
                     f"slur to {end_at or 'another voice'} removed; check the page")
        if rec["end_measure"]:
            mark_bar(staves[rec["end_staff"] - 1].findall("Measure")[rec["end_measure"] - 1],
                     f"slur from {start_at or 'another voice'} removed; check the page")
    return records


def _record(start: Optional[Dict], end: Optional[Dict], names: Dict[int, str]) -> Dict:
    rec = {}
    for prefix, half in (("", start), ("end_", end)):
        rec[prefix + "measure"] = half["bar"] + 1 if half else None
        rec[prefix + "staff"] = half["staff"] if half else None
        rec[prefix + "part"] = names.get(half["staff"]) if half else None
        rec[prefix + "note"] = half["note"] if half else None
        rec[prefix + "pos"] = str(half["pos"]) if half else None
    return rec


#: The metaTag the removed slurs are kept in, so the app can offer them back (#290).
META = "removedSlurs"


def store_removed(root: etree._Element, records: List[Dict]) -> None:
    """Keep the removed slurs in the score itself, where the Fix stage reads them.

    Each record says where both halves stood (`pos` is how far into its bar), which
    is what offering "slur in this singer" or "in that one" needs, and what the red
    mark's sentence does not carry. Nothing is written when nothing was removed.
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    if score is None:
        return
    for tag in score.findall(f"metaTag[@name='{META}']"):
        score.remove(tag)
    if not records:
        return
    tag = etree.Element("metaTag", name=META)
    tag.text = json.dumps(records, ensure_ascii=False)
    existing = score.findall("metaTag")
    if existing:
        existing[-1].addnext(tag)
    else:
        score.insert(0, tag)


def removed_slurs(root: etree._Element) -> List[Dict]:
    """The slurs cleaning removed from this score, or none."""
    score = root if root.tag == "Score" else root.find(".//Score")
    tag = score.find(f"metaTag[@name='{META}']") if score is not None else None
    if tag is None or not tag.text:
        return []
    try:
        found = json.loads(tag.text)
    except ValueError:
        return []
    return [r for r in found if isinstance(r, dict)]
