#!/usr/bin/env python3

import json
from lxml import etree

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


# What each voicing may contain, high to low.
VOICINGS = {
    "men": ("Tenor", "Bass"),
    "women": ("Soprano", "Alto"),
    "mixed": ("Soprano", "Alto", "Tenor", "Bass"),
}


def _forced_part_types(root: etree._Element, voicing: str) -> dict:
    """Name the staves from the voicing instead of guessing from pitch.

    Pitch cannot settle this on its own. A male-choir score is written in treble
    clef sounding an octave down, and editions routinely leave the 8 off the clef
    -- the reference case reads as 66-82, squarely soprano, and is a tenor line.
    Told it is a men's score, the answer is forced: every G staff is a tenor part,
    and it is marked G8vb whether the engraver marked it or not.
    """
    treble, bass = [], []
    for staff in root.findall(".//Score/Staff"):
        # A staff with no notes is not a voice -- the recording spacer is one, and
        # counting it would shift the split and hand a name to a click track.
        if staff.find(".//Note") is None:
            continue
        clef = staff.find(".//Clef/concertClefType")
        kind = (clef.text.strip() if clef is not None and clef.text else "G")
        (bass if kind.startswith("F") else treble).append((int(staff.get("id", "0")), kind))

    def split(staves, upper, lower):
        """Upper half takes the higher name; staves are already in musical order."""
        half = (len(staves) + 1) // 2
        return {sid: (upper if i < half else lower, kind)
                for i, (sid, kind) in enumerate(staves)}

    named = {}
    if voicing == "men":
        named.update({sid: ("Tenor", kind) for sid, kind in treble})
        named.update({sid: ("Bass", kind) for sid, kind in bass})
    elif voicing == "women":
        named.update(split(treble, "Soprano", "Alto"))
        named.update({sid: ("Alto", kind) for sid, kind in bass})
    else:  # mixed
        named.update(split(treble, "Soprano", "Alto"))
        named.update(split(bass, "Tenor", "Bass"))

    info = {}
    for staff in root.findall(".//Score/Staff"):
        sid = int(staff.get("id", "0"))
        if sid not in named:
            continue
        part_name, was = named[sid]
        pitches = [int(p.text) for p in staff.iter("pitch") if p.text]
        clef_type = "G8vb" if (part_name == "Tenor" and not was.startswith("F")) else was
        info[sid] = {
            "clef_type": clef_type,
            "highest_note": max(pitches) if pitches else None,
            "lowest_note": min(pitches) if pitches else None,
            "part_name": part_name,
            "part_slug": part_name[0],
            # A plain G staff that is really a tenor part was read an octave high:
            # the notes sit where an 8vb clef puts them, but were taken at face value.
            "octave_down": clef_type == "G8vb" and was == "G",
        }
    return info


def detect_part_types(root: etree._Element, voicing: Optional[str] = None) -> None:
    """
    For each staff, return a clef type and part name.
    E.g. TTBB
    T1, T2, B1, B2
    G8vb, G8vb, F, F

    `voicing` ("men"/"women"/"mixed") forces the answer instead of inferring it
    from clef and pitch range, which cannot tell a tenor line under an unmarked
    octave clef from a soprano one.
    """
    if voicing in VOICINGS:
        part_info = _forced_part_types(root, voicing)
        _number_parts(part_info)
        logger.debug(f"Part info ({voicing}): {json.dumps(part_info, indent=2)}")
        return part_info
    any_f_clef: bool = False
    # First pass: Find F clefs
    for staff in root.findall(".//Score/Staff"):
        clef: Optional[etree._Element] = staff.find(".//Clef")
        if clef is not None and clef.find(".//concertClefType") is not None:
            clef_type: str = clef.find(".//concertClefType").text.strip()
            if clef_type == "F":
                any_f_clef = True
                break

    logger.debug(f"Any F clef found: {any_f_clef}")
    # F clefs are male voices, either T, "Men", or "Baritone" or "Bass"

    part_info = {}

    for staff in root.findall(".//Score/Staff"):
        clef: Optional[etree._Element] = staff.find(".//Clef")
        clef_type = None
        if clef is not None and clef.find(".//concertClefType") is not None:
            clef_type: str = clef.find(".//concertClefType").text.strip()

        if clef_type is None:
            clef_type = "G"  # Default to G clef if not found

        # Find highest and lowest notes in the staff
        highest_note: Optional[int] = None
        lowest_note: Optional[int] = None
        for note in staff.findall(".//Note"):
            pitch_el: Optional[etree._Element] = note.find(".//pitch")
            if pitch_el is not None and pitch_el.text is not None:
                pitch: int = int(pitch_el.text)
                if highest_note is None or pitch > highest_note:
                    highest_note = pitch
                if lowest_note is None or pitch < lowest_note:
                    lowest_note = pitch

        part_name = ""
        if clef_type == "F":
            # lowest note < 43 == This is Bass
            # highest note > 65 == This is Tenor
            if lowest_note is not None and lowest_note < 50:
                part_name = "Bass"
            elif highest_note is not None and highest_note > 65:
                part_name = "Tenor"
        elif clef_type == "G":
            # lowest note < 55 == This is tenor
            if lowest_note is not None and lowest_note < 55:
                part_name = "Tenor"
                clef_type = "G8vb"  # Tenor clef is G8vb
            if highest_note is not None and highest_note > 72:
                part_name = "Soprano"
                clef_type = "G"
            elif highest_note is not None and highest_note > 68:
                # Only allow alto if soprano already exists
                if any(
                    [
                        part_info[staff_id]["part_name"] == "Soprano"
                        for staff_id in sorted(part_info.keys())
                    ]
                ):
                    part_name = "Alto"
                    clef_type = "G"

        if not part_name and clef_type == "G8vb":
            part_name = "Tenor"

        part_info[int(staff.get("id"))] = {
            "clef_type": clef_type,
            "highest_note": highest_note,
            "lowest_note": lowest_note,
            "part_name": part_name,
            "part_slug": part_name[0] if part_name else "",
        }

    _number_parts(part_info)
    logger.debug(f"Part info: {json.dumps(part_info, indent=2)}")
    return part_info


def _number_parts(part_info: dict) -> None:
    """Number the staves within each run of the same part name: T1, T2, B1, B2."""
    index = 1
    prev_part_name: Optional[str] = None
    for staff_id in sorted(part_info.keys()):
        if prev_part_name and part_info[staff_id]["part_name"] != prev_part_name:
            index = 1
        part_info[staff_id]["part_index"] = index
        index += 1
        prev_part_name = part_info[staff_id]["part_name"]
