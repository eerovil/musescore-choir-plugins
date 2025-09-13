#!/usr/bin/env python3

import json
from lxml import etree

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def detect_part_types(root: etree._Element) -> None:
    """
    For each staff, return a clef type and part name.
    E.g. TTBB
    T1, T2, B1, B2
    G8vb, G8vb, F, F
    """
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

        part_info[int(staff.get("id"))] = {
            "clef_type": clef_type,
            "highest_note": highest_note,
            "lowest_note": lowest_note,
            "part_name": part_name,
            "part_slug": part_name[0] if part_name else "",
        }

    sorted_staff_ids: List[int] = sorted(part_info.keys())
    index = 1
    prev_part_name: Optional[str] = None
    for staff_id in sorted_staff_ids:
        if prev_part_name and part_info[staff_id]["part_name"] != prev_part_name:
            index = 1
        part_info[staff_id]["part_index"] = index
        index += 1
        prev_part_name = part_info[staff_id]["part_name"]

    logger.debug(f"Part info: {json.dumps(part_info, indent=2)}")
    return part_info
