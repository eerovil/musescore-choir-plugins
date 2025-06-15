#!/usr/bin/env python3

from collections import defaultdict
from lxml import etree

import logging
from typing import Dict, List, Set, Optional, Any

from .globals import REVERSED_VOICES_BY_STAFF_MEASURE
from .utils import loop_staff

logging.basicConfig(level=logging.DEBUG)


def find_reversed_voices_by_staff_measure(staff: etree._Element) -> None:
    """
    Find reversed voices for a given staff ID.
    This function should return a list of reversed voices for the specified staff.

    Args:
        staff (etree._Element): The staff XML element.
    """
    global REVERSED_VOICES_BY_STAFF_MEASURE
    staff_id: int = int(staff.get("id", "0"))
    REVERSED_VOICES_BY_STAFF_MEASURE[staff_id] = {}
    # First pass: add stem directions to measures that do not have them
    els_by_timepos: Dict[int, Dict[int, List[Dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    measures_with_stem_direction: Set[int] = set()
    for el in loop_staff(staff):
        staff_id_loop: int = int(el["staff_id"])
        measure_index: int = el["measure_index"]
        voice_index: int = el["voice_index"]
        element: etree._Element = el["element"]
        time_pos: int = el["time_pos"]

        if element.tag == "Chord":
            els_by_timepos[measure_index][time_pos].append(
                {
                    "voice_index": voice_index,
                    "element": element,
                }
            )
            if element.find(".//StemDirection") is not None:
                measures_with_stem_direction.add(measure_index)

    for measure_index, timepos_elements in els_by_timepos.items():
        if measure_index in measures_with_stem_direction:
            # If the measure already has stem directions, we don't need to process it
            continue
        for elements in timepos_elements.values():
            if len(elements) < 2:
                continue
            # Find which voice has the higher pitch in the elements
            highest_element_index: int = 0
            highest_element: Dict[str, Any] = elements[0]
            for i, el in enumerate(elements):
                pitch_el: Optional[etree._Element] = el["element"].find(".//pitch")
                if pitch_el is not None and pitch_el.text is not None:
                    pitch: int = int(pitch_el.text)
                    highest_pitch_el: Optional[etree._Element] = highest_element[
                        "element"
                    ].find(".//pitch")
                    if (
                        highest_pitch_el is not None
                        and highest_pitch_el.text is not None
                        and pitch > int(highest_pitch_el.text)
                    ):
                        highest_element_index = i
                        highest_element = el
            # Add stem direction up to the highest element
            stem_direction_up: etree._Element = etree.Element("StemDirection")
            stem_direction_up.text = "up"
            highest_element["element"].append(stem_direction_up)
            # Add stem direction down to the other elements
            for i, el in enumerate(elements):
                if i == highest_element_index:
                    continue
                stem_direction_down: etree._Element = etree.Element("StemDirection")
                stem_direction_down.text = "down"
                el["element"].append(stem_direction_down)

    index: int = -1
    for measure in staff.findall(".//Measure"):
        index += 1
        voice_index_in_measure: int = -1
        for voice in measure.findall(".//voice"):
            voice_index_in_measure += 1
            for chord in voice.findall(".//Chord"):
                stem_direction_el: Optional[etree._Element] = chord.find(
                    ".//StemDirection"
                )
                logging.debug(
                    f"Processing chord in staff {staff.get('id')}, measure {index}, voice {voice_index_in_measure}, stem direction: {stem_direction_el.text if stem_direction_el is not None else 'None'}"
                )
                if stem_direction_el is None or stem_direction_el.text is None:
                    continue  # No stem direction, skip this chord
                else:
                    stem_direction_text: str = stem_direction_el.text.strip().lower()
                stem_voice: int = 0 if stem_direction_text == "up" else 1
                if stem_voice != voice_index_in_measure:
                    # This voice is reversed (up stem but voice 2)
                    REVERSED_VOICES_BY_STAFF_MEASURE[staff_id][index] = True
