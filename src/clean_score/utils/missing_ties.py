#!/usr/bin/env python3

from collections import defaultdict
from copy import deepcopy
from lxml import etree

import logging
from typing import Dict, List, Optional, Any, Tuple

from .utils import loop_staff, resolve_duration

logger = logging.getLogger(__name__)


Rhythm = Tuple[Tuple[int, str, str, str], ...]


def _bar_rhythms(root) -> Dict[Tuple[str, int, int], Rhythm]:
    """{(staff id, measure index, voice index): every chord and rest, in order}."""
    rhythms: Dict[Tuple[str, int, int], list] = defaultdict(list)
    for staff in root.findall(".//Score/Staff"):
        for el in loop_staff(staff):
            e = el["element"]
            if e.tag not in ("Chord", "Rest"):
                continue
            rhythms[(staff.get("id"), el["measure_index"], el["voice_index"])].append(
                (
                    el["time_pos"],
                    e.tag,
                    e.findtext(".//durationType") or "",
                    e.findtext(".//dots") or "0",
                )
            )
    return {key: tuple(events) for key, events in rhythms.items()}


def _shares_rhythm(rhythms, donor, target) -> bool:
    """The two voices strike the same rhythm up to the end of the tie.

    Equal pitch over an equal span is not enough: two voices can sound the same
    pitch on the same beats and still be printed as separate notes, as an
    ostinato against a held line is. A dropped tie is recovered only where the
    target sings the donor's rhythm across the whole bar the tie starts in and,
    when it crosses a barline, the next bar up to the note it ends on (#284).
    What follows the tie is not compared: voices that move together into a held
    note often part straight after it.
    """
    first, second = donor
    a = rhythms.get((first["staff_id"], first["measure_index"], first["voice_index"]))
    b = rhythms.get((target[0]["staff_id"], first["measure_index"], target[0]["voice_index"]))
    if a is None or a != b:
        return False
    if second["measure_index"] == first["measure_index"]:
        return True

    def upto_end(staff_id, voice_index):
        events = rhythms.get((staff_id, second["measure_index"], voice_index), ())
        return tuple(e for e in events if e[0] <= second["time_pos"])

    return upto_end(second["staff_id"], second["voice_index"]) == upto_end(
        target[1]["staff_id"], target[1]["voice_index"]
    )


def add_missing_ties(root) -> List[Dict[str, Any]]:
    """Copy a tie onto a voice that lost it, from a parallel voice that kept it.

    Only where the target has the same two durations at the same time, the same
    pitch on both notes, and the donor's rhythm up to the end of the tie
    (`_shares_rhythm`).
    Returns where a tie was added (staff id, measure index, time position).
    """
    rhythms = _bar_rhythms(root)
    tied_notes_by_measure_time_pos: Dict[
        Tuple[int, int], List[List[Dict[str, Any]]]
    ] = defaultdict(list)
    for staff in root.findall(".//Score/Staff"):
        open_tie = None
        for el in loop_staff(staff):
            if el["element"].tag == "Chord":
                measure_index: int = el["measure_index"]
                time_pos: int = el["time_pos"]
                record = {
                    "staff_id": staff.get("id"),
                    "measure_index": measure_index,
                    "voice_index": el["voice_index"],
                    "time_pos": time_pos,
                    "element": el["element"],
                }
                if open_tie is not None:
                    # We have a span starter, so this is the next note
                    open_tie.append(record)
                    open_tie = None
                    continue

                spanner: Optional[etree._Element] = el["element"].find(
                    ".//Spanner[@type='Tie']"
                )
                if spanner is not None:
                    if spanner.find(".//next") is not None:
                        open_tie = [record]
                        tied_notes_by_measure_time_pos[
                            (measure_index, time_pos)
                        ].append(open_tie)

    logger.debug(
        f"Found {tied_notes_by_measure_time_pos.keys()} tied notes by measure and time position"
    )
    added: List[Dict[str, Any]] = []
    for staff in root.findall(".//Score/Staff"):
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
                                "voice_index": el["voice_index"],
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
                                    "voice_index": el["voice_index"],
                                    "time_pos": time_pos,
                                    "element": el["element"],
                                }
                            ]
                        )

        logger.debug(f"new_tied_notes for staff {staff.get('id')}: {new_tied_notes}")

        # Check that each two notes match a parent pair in tied_notes_by_measure_time_pos
        for note_pair in new_tied_notes:
            if len(note_pair) != 2:
                continue
            note1: Dict[str, Any] = note_pair[0]
            note2: Dict[str, Any] = note_pair[1]

            # If notes are not same pitch, skip
            pitch1_el: Optional[etree._Element] = note1["element"].find(".//pitch")
            pitch2_el: Optional[etree._Element] = note2["element"].find(".//pitch")
            if (
                pitch1_el is not None
                and pitch1_el.text is not None
                and pitch2_el is not None
                and pitch2_el.text is not None
            ):
                pitch1 = int(pitch1_el.text)
                pitch2 = int(pitch2_el.text)
                if pitch1 != pitch2:
                    logger.debug(
                        f"Note pitches do not match: {pitch1} != {pitch2}, skipping adding tie"
                    )
                    continue

            note1_duration = resolve_duration(
                note1["element"].find(".//durationType").text
            )
            note2_duration = resolve_duration(
                note2["element"].find(".//durationType").text
            )
            parent_pair = None
            for candidate in tied_notes_by_measure_time_pos.get(
                (note1["measure_index"], note1["time_pos"]), []
            ):
                if len(candidate) != 2:
                    continue
                if (
                    resolve_duration(candidate[0]["element"].find(".//durationType").text)
                    != note1_duration
                    or resolve_duration(
                        candidate[1]["element"].find(".//durationType").text
                    )
                    != note2_duration
                ):
                    continue
                if not _shares_rhythm(rhythms, candidate, note_pair):
                    logger.debug(
                        f"Not copying tie to staff {staff.get('id')}, measure {note1['measure_index']}: "
                        f"staff {candidate[0]['staff_id']} has it but sings a different rhythm"
                    )
                    continue
                parent_pair = candidate
                break
            if parent_pair is None:
                continue

            # Clone the spanner from the parent pair to the note pair
            spanner1: Optional[etree._Element] = parent_pair[0]["element"].find(
                ".//Spanner[@type='Tie']"
            )
            spanner2: Optional[etree._Element] = parent_pair[1]["element"].find(
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
                    added.append(
                        {
                            "staff_id": note1["staff_id"],
                            "measure_index": note1["measure_index"],
                            "time_pos": note1["time_pos"],
                        }
                    )

                logger.debug(
                    f"Added spanner to note pair for staff {staff.get('id')}, measure {note1['measure_index']}, time position {note1['time_pos']}"
                )
            else:
                logger.warning(
                    f"Spanner not found in parent pair for staff {staff.get('id')}, measure {note1['measure_index']}, time position {note1['time_pos']}"
                )
    return added
