#!/usr/bin/env python3

from collections import defaultdict
from copy import deepcopy
from lxml import etree

import logging
from typing import Dict, List, Optional, Any, Tuple

from .utils import loop_staff, resolve_duration

logger = logging.getLogger(__name__)


def add_missing_ties(root):
    # Find all tied notes (two notes each)
    # Check all other staffs, if they have same lenght notes at the same time position
    # Add slurs to the notes in the other staffs
    tied_notes_by_measure_time_pos: Dict[Tuple[int, int], List[etree._Element]] = (
        defaultdict(list)
    )
    for staff in root.findall(".//Score/Staff"):
        span_index = None
        for el in loop_staff(staff):
            if el["element"].tag == "Chord":
                measure_index: int = el["measure_index"]
                time_pos: int = el["time_pos"]
                if span_index is not None:
                    # We have a span starter, so this is the next note
                    tied_notes_by_measure_time_pos[span_index].append(el["element"])
                    span_index = None
                    continue

                spanner: Optional[etree._Element] = el["element"].find(
                    ".//Spanner[@type='Tie']"
                )
                if spanner is not None:
                    if spanner.find(".//next") is not None:
                        span_index = (measure_index, time_pos)
                        tied_notes_by_measure_time_pos[(measure_index, time_pos)] = [
                            el["element"]
                        ]

    logger.debug(
        f"Found {tied_notes_by_measure_time_pos.keys()} tied notes by measure and time position"
    )
    for staff in root.findall(".//Score/Staff"):
        span_index = None
        new_tied_notes = []
        for el in loop_staff(staff):
            if el["element"].tag == "Chord":
                measure_index: int = el["measure_index"]
                time_pos: int = el["time_pos"]

                spanner: Optional[etree._Element] = el["element"].find(
                    ".//Spanner[@type='Tie']"
                )
                if spanner is None:
                    if new_tied_notes and len(new_tied_notes[-1]) == 1:
                        new_tied_notes[-1].append(
                            {
                                "staff_id": staff.get("id"),
                                "measure_index": measure_index,
                                "time_pos": time_pos,
                                "element": el["element"],
                            }
                        )
                    matching_tie_start = tied_notes_by_measure_time_pos.get(
                        (measure_index, time_pos)
                    )
                    if matching_tie_start:
                        logger.debug(
                            f"Found matching tie start for staff {staff.get('id')}, measure {measure_index}, time position {time_pos}"
                        )
                        new_tied_notes.append(
                            [
                                {
                                    "staff_id": staff.get("id"),
                                    "measure_index": measure_index,
                                    "time_pos": time_pos,
                                    "element": el["element"],
                                }
                            ]
                        )

        logger.debug(f"new_tied_notes for staff {staff.get('id')}: {new_tied_notes}")

        # Check that each two notes match their parents in the tied_notes_by_measure_time_pos
        for note_pair in new_tied_notes:
            if len(note_pair) != 2:
                continue
            note1: Dict[str, Any] = note_pair[0]
            note2: Dict[str, Any] = note_pair[1]
            parent_pair: List[etree._Element] = tied_notes_by_measure_time_pos.get(
                (note1["measure_index"], note1["time_pos"]), []
            )
            if len(parent_pair) != 2:
                logger.warning(
                    f"Found a note pair with no matching parent pair: {note1}, {note2}"
                )
                continue

            note1_duration = resolve_duration(
                note1["element"].find(".//durationType").text
            )
            note2_duration = resolve_duration(
                note2["element"].find(".//durationType").text
            )
            parent1_duration = resolve_duration(
                parent_pair[0].find(".//durationType").text
            )
            parent2_duration = resolve_duration(
                parent_pair[1].find(".//durationType").text
            )
            if note1_duration != parent1_duration or note2_duration != parent2_duration:
                logger.warning(
                    f"Note durations do not match parent pair: {note1_duration}, {note2_duration} != {parent1_duration}, {parent2_duration}"
                )
                continue

            # Clone the spanner from the parent pair to the note pair
            spanner1: Optional[etree._Element] = parent_pair[0].find(
                ".//Spanner[@type='Tie']"
            )
            spanner2: Optional[etree._Element] = parent_pair[1].find(
                ".//Spanner[@type='Tie']"
            )
            if spanner1 is not None and spanner2 is not None:
                new_spanner1: etree._Element = deepcopy(spanner1)
                new_spanner2: etree._Element = deepcopy(spanner2)
                # Set the next and prev elements to the note pair
                note_e1 = note1["element"].find(".//Note")
                note_e2 = note2["element"].find(".//Note")
                if note_e1 is not None and note_e2 is not None:
                    note_e1.append(new_spanner1)
                    note_e2.append(new_spanner2)
                logger.debug(
                    f"Added spanner to note pair for staff {staff.get('id')}, measure {note1['measure_index']}, time position {note1['time_pos']}"
                )
            else:
                logger.warning(
                    f"Spanner not found in parent pair for staff {staff.get('id')}, measure {note1['measure_index']}, time position {note1['time_pos']}"
                )
