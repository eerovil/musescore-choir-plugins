"""Health check — surface OCR damage in the cleaned score as a punch list.

Validation only; never mutates the score. Findings:
  - malformed-measure: a voice whose note/rest durations don't fill the bar
    (the auto-fixers couldn't repair it — likely lost notes or a bad tuplet).
  - extra-voices: a staff measure with more than one note-bearing voice
    (the split didn't fully separate the voices).

Missing notes that *do* fill the bar (a half-rest standing in for lost notes)
aren't tick-detectable; they surface as lyric syllable overflow at import time.
Missing slurs are undetectable and stay manual.

All durations are computed as exact whole-note Fractions so tuplets don't cause
rounding false-positives.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Optional

from lxml import etree

# durationType (and fraction) -> whole-note fraction
_DUR = {
    "whole": Fraction(1, 1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT_MULT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}


def _parse_fraction(text: Optional[str]) -> Optional[Fraction]:
    if not text:
        return None
    try:
        if "/" in text:
            n, d = text.split("/")
            return Fraction(int(n), int(d))
        return Fraction(int(text), 1)
    except (ValueError, ZeroDivisionError):
        return None


def _chord_rest_len(el: etree._Element, tuplet_scale: Fraction) -> Optional[Fraction]:
    dt = el.findtext("durationType")
    if dt == "measure":
        return None  # measure-length rest; handled by caller as the full bar
    base = _DUR.get((dt or "").strip())
    if base is None:
        return Fraction(0)
    dots = 0
    de = el.find("dots")
    if de is not None and (de.text or "").strip().isdigit():
        dots = int(de.text.strip())
    return base * _DOT_MULT.get(dots, Fraction(1)) * tuplet_scale


def _voice_length(voice: etree._Element, nominal: Fraction) -> tuple[Fraction, bool, bool]:
    """Return (summed length, has_chord, is_measure_rest) for a voice element."""
    total = Fraction(0)
    has_chord = False
    tuplet_scale = Fraction(1)
    measure_rest = False
    for el in voice:
        if el.tag == "Tuplet":
            actual = _parse_fraction(el.findtext("actualNotes")) or Fraction(1)
            normal = _parse_fraction(el.findtext("normalNotes")) or Fraction(1)
            if actual:
                tuplet_scale = normal / actual
        elif el.tag == "endTuplet":
            tuplet_scale = Fraction(1)
        elif el.tag in ("Chord", "Rest"):
            if el.tag == "Chord":
                has_chord = True
            length = _chord_rest_len(el, tuplet_scale)
            if length is None:  # measure rest
                total += nominal
                measure_rest = True
            else:
                total += length
        elif el.tag == "location":
            frac = _parse_fraction(el.findtext("fractions"))
            if frac is not None:
                total += frac
    return total, has_chord, measure_rest


def scan(cleaned_path: str) -> List[Dict]:
    """Return a list of issue dicts (without status) for the cleaned score."""
    with open(cleaned_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    score = root if root.tag == "Score" else root.find(".//Score")

    # Map staff id -> part display name (for friendly labels).
    staff_name: Dict[int, str] = {}
    for part in score.findall("Part"):
        name = part.findtext("trackName") or part.findtext("Instrument/trackName") or ""
        for st in part.findall("Staff"):
            staff_name[int(st.get("id", "0"))] = name.strip()

    issues: List[Dict] = []
    for staff in score.findall("Staff"):
        sid = int(staff.get("id", "0"))
        label = staff_name.get(sid) or f"staff {sid}"
        sig = Fraction(4, 4)
        for mi, measure in enumerate(staff.findall("Measure"), start=1):
            # Time signature can change at a measure (in any voice).
            ts = measure.find(".//TimeSig")
            if ts is not None:
                n = _parse_fraction(ts.findtext("sigN"))
                d = _parse_fraction(ts.findtext("sigD"))
                if n and d:
                    sig = Fraction(int(n), int(d))
            # Anacrusis / pickup measures override the nominal length.
            nominal = sig
            len_attr = _parse_fraction(measure.get("len"))
            if len_attr is not None:
                nominal = len_attr

            voices = measure.findall("voice")
            note_bearing = 0
            for vi, voice in enumerate(voices):
                total, has_chord, _ = _voice_length(voice, nominal)
                if has_chord:
                    note_bearing += 1
                # Only flag voices that carry notes and don't fill the bar.
                if has_chord and total != nominal:
                    issues.append({
                        "id": f"malformed-m{mi}-s{sid}-v{vi}",
                        "kind": "malformed-measure",
                        "measure": mi,
                        "staff": label,
                        "detail": f"voice {vi + 1} fills {total} of {nominal}",
                    })
            if note_bearing > 1:
                issues.append({
                    "id": f"extra-voices-m{mi}-s{sid}",
                    "kind": "extra-voices",
                    "measure": mi,
                    "staff": label,
                    "detail": f"{note_bearing} note-bearing voices on one staff",
                })
    return issues


def merge_issues(found: List[Dict], previous: List[Dict]) -> List[Dict]:
    """Carry over dismissed status; mark previously-open issues that are gone as fixed.

    Returns the new issue list: current findings (status preserved if dismissed),
    plus previously-fixed/dismissed entries that no longer appear are dropped, and
    a previously-open issue absent from `found` is recorded as fixed (so the UI can
    show it ticked off briefly — callers may filter to status=='open').
    """
    prev_by_id = {i["id"]: i for i in previous}
    found_ids = {i["id"] for i in found}
    merged: List[Dict] = []
    for issue in found:
        prev = prev_by_id.get(issue["id"])
        issue = dict(issue)
        issue["status"] = "dismissed" if prev and prev.get("status") == "dismissed" else "open"
        merged.append(issue)
    # Previously-open issues now gone -> fixed.
    for prev in previous:
        if prev["id"] not in found_ids and prev.get("status") == "open":
            done = dict(prev)
            done["status"] = "fixed"
            merged.append(done)
    return merged
