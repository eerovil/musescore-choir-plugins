#!/usr/bin/env python3

from collections import defaultdict
from copy import deepcopy
import csv
import json
from lxml import etree

import logging
from typing import Dict, List, Set, Optional, Any, Tuple

logging.basicConfig(level=logging.DEBUG)


STAFF_MAPPING: Dict[int, int] = {}
REVERSED_VOICES_BY_STAFF_MEASURE: Dict[int, Dict[int, bool]] = {}
LYRICS_BY_TIMEPOS: Dict[str, List[Dict[str, Any]]] = {}
RESOLUTION: int = 128  # Default resolution for durations in MuseScore XML


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

    logging.debug(f"Any F clef found: {any_f_clef}")
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
            if lowest_note is not None and lowest_note < 43:
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

    logging.debug(f"Part info: {json.dumps(part_info, indent=2)}")
    return part_info


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

    logging.debug(
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
                        logging.debug(
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

        logging.debug(f"new_tied_notes for staff {staff.get('id')}: {new_tied_notes}")

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
                logging.warning(
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
                logging.warning(
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
                logging.debug(
                    f"Added spanner to note pair for staff {staff.get('id')}, measure {note1['measure_index']}, time position {note1['time_pos']}"
                )
            else:
                logging.warning(
                    f"Spanner not found in parent pair for staff {staff.get('id')}, measure {note1['measure_index']}, time position {note1['time_pos']}"
                )


def resolve_duration(fraction_or_duration: str, dots: str = "0") -> int:
    """
    Resolves a duration string (either a fraction like "1/4" or a MuseScore duration type like "quarter")
    into its equivalent duration in ticks.

    Args:
        fraction_or_duration (str): A string representing a musical duration.
                                   Examples: "1/4", "half", "quarter", "eighth", "16th".
        dots (str): The number of dots as a string ("0", "1", "2", "3").

    Returns:
        int: The duration in ticks. Returns 0 if the input is not recognized.
    """
    if "/" in fraction_or_duration:
        try:
            numerator, denominator = map(int, fraction_or_duration.split("/"))
            return int(RESOLUTION * (numerator / denominator))
        except ValueError:
            return 0  # Invalid fraction format
    else:
        # Handle MuseScore's standard duration type strings
        duration_map: Dict[str, int] = {
            "whole": RESOLUTION,
            "half": RESOLUTION // 2,
            "quarter": RESOLUTION // 4,
            "eighth": RESOLUTION // 8,
            "16th": RESOLUTION // 16,
            "32nd": RESOLUTION // 32,
            "64th": RESOLUTION // 64,
            "128th": RESOLUTION // 128,
            # Add more as needed
        }
        ret: int = duration_map.get(fraction_or_duration.lower(), 0)
        if dots == "1":
            ret += ret // 2  # Add half of the duration for one dot
        elif dots == "2":
            ret += (ret // 2) + (ret // 4)
        elif dots == "3":
            ret += (ret // 2) + (ret // 4) + (ret // 8)
        return ret


def lyric_to_dict(lyric: etree._Element) -> Dict[str, str]:
    """
    Converts a <Lyrics> etree element into a dictionary.

    Args:
        lyric (etree._Element): The <Lyrics> XML element.

    Returns:
        Dict[str, str]: A dictionary containing 'syllabic', 'text', and 'no' fields.
    """
    return {
        "syllabic": (
            lyric.find(".//syllabic").text
            if lyric.find(".//syllabic") is not None
            else ""
        ),
        "text": (
            lyric.find(".//text").text if lyric.find(".//text") is not None else ""
        ),
        "no": (lyric.find(".//no").text if lyric.find(".//no") is not None else ""),
    }


def find_lyric(
    staff_id: Optional[int] = None,
    measure_index: Optional[int] = None,
    voice_index: Optional[int] = None,
    time_pos: Optional[int] = None,
) -> Optional[Dict[str, str]]:
    """
    Find a lyric for the given staff ID, measure index, voice index, and time position.
    Returns the first lyric found or None if no lyric is found.

    Args:
        staff_id (Optional[int]): The ID of the staff.
        measure_index (Optional[int]): The index of the measure.
        voice_index (Optional[int]): The index of the voice.
        time_pos (Optional[int]): The time position within the measure.

    Returns:
        Optional[Dict[str, str]]: The lyric dictionary if found, otherwise None.
    """
    global LYRICS_BY_TIMEPOS
    if measure_index is None or time_pos is None:
        return None
    key: str = f"{measure_index}-{time_pos}"
    if key in LYRICS_BY_TIMEPOS:
        lyric_choices: List[Dict[str, Any]] = LYRICS_BY_TIMEPOS[key]
        # Try to find the most correct lyric.
        # Sometimes there is verse 2 lyric in the staff above
        # That would mean the lyric is for the upper voice in the lower staff
        original_staff_id: int = (
            get_original_staff_id(staff_id) if staff_id is not None else -1
        )
        upper_staff_id: int = original_staff_id - 2
        if voice_index == 0:
            for lyric_choice in lyric_choices:
                if (
                    lyric_choice["staff_id"] == upper_staff_id
                    and lyric_choice["lyric"]["no"] == "1"
                ):
                    # Force "no" to be empty
                    lyric_choice["lyric"]["no"] = ""
                    return lyric_choice["lyric"]

        # If voice_index and original_staff_id matches, that's the best match.
        for lyric_choice in lyric_choices:
            if (
                lyric_choice["voice_index"] == voice_index
                and lyric_choice["staff_id"] == original_staff_id
            ):
                return lyric_choice["lyric"]
        # if staff_id matches, that's the next best match.
        for lyric_choice in lyric_choices:
            if lyric_choice["staff_id"] == original_staff_id:
                return lyric_choice["lyric"]
        # If no staff_id match, try to find a lyric with the same voice_index
        # If voice_index matches, that's the best match.
        for lyric_choice in lyric_choices:
            if lyric_choice["voice_index"] == voice_index:
                return lyric_choice["lyric"]
        # If no exact match, return the first lyric found
        for lyric_choice in lyric_choices:
            return lyric_choice["lyric"]

    return None


def default_keysig() -> etree._Element:
    """
    Returns a default key signature element.
    This is used to ensure that the key signature is set correctly in the output.
    """
    keysig: etree._Element = etree.Element("KeySig")
    accidental: etree._Element = etree.Element("accidental")
    accidental.text = "0"
    keysig.append(accidental)
    return keysig


def default_timesig() -> etree._Element:
    """
    Returns a default time signature element.
    This is used to ensure that the time signature is set correctly in the output.
    """
    timesig: etree._Element = etree.Element("TimeSig")
    sigN: etree._Element = etree.Element("sigN")
    sigN.text = "4"
    sigD: etree._Element = etree.Element("sigD")
    sigD.text = "4"
    timesig.append(sigN)
    timesig.append(sigD)
    return timesig


def loop_staff(staff: etree._Element) -> Any:
    """
    Generator function to loop through the staff and yield elements
    with their time positions.

    Args:
        staff (etree._Element): The staff XML element.

    Yields:
        Dict[str, Any]: A dictionary containing 'staff_id', 'measure_index',
                        'voice_index', 'time_pos', and 'element'.
    """
    staff_id: int = int(staff.get("id", "0"))
    measure_index: int = -1
    for measure in staff.findall(".//Measure"):
        measure_index += 1
        voice_index: int = -1
        for voice in measure.findall(".//voice"):
            voice_index += 1
            time_pos: int = 0
            for el in voice:
                yield {
                    "staff_id": staff_id,
                    "measure_index": measure_index,
                    "voice_index": voice_index,
                    "time_pos": time_pos,
                    "element": el,
                }
                if el.tag in ["Chord", "Rest"]:
                    duration_type: Optional[etree._Element] = el.find(".//durationType")
                    dots: Optional[etree._Element] = el.find(".//dots")
                    time_pos += resolve_duration(
                        duration_type.text if duration_type is not None else "0",
                        dots.text if dots is not None else "0",
                    )
                if el.tag == "location":
                    fractions: Optional[etree._Element] = el.find(".//fractions")
                    if fractions is not None:
                        time_pos += resolve_duration(
                            fractions.text if fractions is not None else "0"
                        )


def read_lyrics(staff: etree._Element) -> None:
    """
    Read lyrics from the staff and store them in a dictionary.
    The dictionary is keyed by staff ID and time position.

    Args:
        staff (etree._Element): The staff XML element.
    """
    staff_id: int = int(staff.get("id", "0"))
    global LYRICS_BY_TIMEPOS, REVERSED_VOICES_BY_STAFF_MEASURE
    for el in loop_staff(staff):
        staff_id_loop: int = int(el["staff_id"])
        measure_index: int = el["measure_index"]
        voice_index: int = el["voice_index"]
        time_pos: int = el["time_pos"]
        element: etree._Element = el["element"]

        reversed_voices: bool = REVERSED_VOICES_BY_STAFF_MEASURE.get(
            staff_id_loop, {}
        ).get(measure_index, False)
        if reversed_voices:
            # If the voices are reversed, we need to adjust the voice index
            voice_index = 1 if voice_index == 0 else 0

        if element.tag == "Chord":
            for lyric in element.findall(".//Lyrics"):
                LYRICS_BY_TIMEPOS.setdefault(f"{measure_index}-{time_pos}", []).append(
                    {
                        "staff_id": staff_id_loop,
                        "measure_index": measure_index,
                        "voice_index": voice_index,
                        "lyric": lyric_to_dict(lyric),
                    }
                )


def find_reversed_voices_by_staff_measure(staff: etree._Element) -> None:
    """
    Find reversed voices for a given staff ID.
    This function should return a list of reversed voices for the specified staff.

    Args:
        staff (etree._Element): The staff XML element.
    """
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


def get_original_staff_id(staff_id: int) -> int:
    """
    Gets the original staff ID before any remapping.

    Args:
        staff_id (int): The current staff ID.

    Returns:
        int: The original staff ID.
    """
    original_staff_id: int = staff_id
    for parent_staff_id, child_staff_id in STAFF_MAPPING.items():
        if child_staff_id == staff_id:
            original_staff_id = parent_staff_id
            break
    return original_staff_id


def delete_all_elements_by_selector(staff: etree._Element, selector: str) -> None:
    """
    Delete all elements with the specified tag from the staff.

    Args:
        staff (etree._Element): The staff XML element.
        selector (str): The XPath selector for the elements to delete.
    """
    for element in staff.findall(selector):
        parent: Optional[etree._Element] = element.getparent()
        if parent is not None:
            parent.remove(element)


def handle_staff(staff: etree._Element, direction: Optional[str]) -> None:
    """
    Deletes notes not matching the specified direction and cleans up other elements.

    Args:
        staff (etree._Element): The staff XML element to process.
        direction (Optional[str]): The direction to keep notes ("up" or "down"), or None to keep all.
    """
    staff_id: int = int(staff.get("id", "0"))
    original_staff_id: int = get_original_staff_id(staff_id)

    logging.debug(f"Handling staff {staff_id} for direction {direction}")
    if direction is not None:
        index: int = -1
        for measure in staff.findall(".//Measure"):
            index += 1
            reversed_voices: bool = REVERSED_VOICES_BY_STAFF_MEASURE.get(
                original_staff_id, {}
            ).get(index, False)
            if reversed_voices:
                voice_to_remove: int = 1 if direction == "down" else 0
            else:
                voice_to_remove: int = 1 if direction == "up" else 0
            voice_index: int = -1
            voices: List[etree._Element] = list(measure.findall(".//voice"))
            keysig: Optional[etree._Element] = deepcopy(measure.find(".//KeySig"))
            timesig: Optional[etree._Element] = deepcopy(measure.find(".//TimeSig"))
            clef: Optional[etree._Element] = deepcopy(measure.find(".//Clef"))
            logging.debug(
                f"Processing measure {index} in staff {staff_id}, original_staff_id {original_staff_id}, time signature: {timesig}, key signature: {keysig}, voice to remove: {voice_to_remove}, reversed_voices: {reversed_voices}"
            )

            for voice in voices:
                voice_index += 1
                # First measure requires TimeSig and KeySig
                if index == 0:
                    timesig = voice.find(".//TimeSig") if timesig is None else timesig
                    if timesig is None:
                        timesig = default_timesig()

                    keysig = voice.find(".//KeySig") if keysig is None else keysig
                    if keysig is None:
                        keysig = default_keysig()

                if timesig is not None:
                    delete_all_elements_by_selector(voice, ".//TimeSig")
                    voice.insert(0, deepcopy(timesig))
                if keysig is not None:
                    delete_all_elements_by_selector(voice, ".//KeySig")
                    voice.insert(0, deepcopy(keysig))
                if clef is not None:
                    delete_all_elements_by_selector(voice, ".//Clef")
                    voice.insert(0, deepcopy(clef))
                if voice_index == voice_to_remove or len(voices) == 1:
                    # Remove the voice that does not match the direction
                    # Unless only one voice is present, then we keep it
                    if len(voices) > 1:
                        measure.remove(voice)
                    else:
                        # We must try to remove the upper/lower notes from each chord, if possible
                        for chord in voice.findall(".//Chord"):
                            notes: List[etree._Element] = sorted(
                                chord.findall(".//Note"),
                                key=lambda n: (
                                    int(n.find(".//pitch").text)
                                    if n.find(".//pitch") is not None
                                    and n.find(".//pitch").text is not None
                                    else 0
                                ),
                            )
                            if voice_to_remove == 0:
                                # Remove the upper note
                                if len(notes) > 1:
                                    chord.remove(notes[-1])
                            else:
                                # Remove the lower note
                                if len(notes) > 1:
                                    chord.remove(notes[0])

    # Finally, set StemDirection up for all Chords in the staff
    for chord in staff.findall(".//Chord"):
        stem_direction: Optional[etree._Element] = chord.find(".//StemDirection")
        if stem_direction is not None:
            stem_direction.text = "up"

    # Delete all <offset> elements in the staff
    delete_all_elements_by_selector(staff, ".//offset")
    delete_all_elements_by_selector(staff, ".//Dynamic")
    delete_all_elements_by_selector(staff, ".//LayoutBreak")
    # Delete all <Spanner type="HairPin">
    delete_all_elements_by_selector(staff, ".//Spanner[@type='HairPin']")
    # Delete all StemDirection elements
    delete_all_elements_by_selector(staff, ".//StemDirection")
    # Delete all Articulation elements
    delete_all_elements_by_selector(staff, ".//Articulation")
    # Delete all Tempo elements
    delete_all_elements_by_selector(staff, ".//Tempo")
    # Delete all Harmony
    delete_all_elements_by_selector(staff, ".//Harmony")

    # Add <timeStretch>3</timeStretch>
    # to each <Fermata>
    for fermata in staff.findall(".//Fermata"):
        time_stretch: etree._Element = etree.Element("timeStretch")
        time_stretch.text = "3"
        fermata.append(time_stretch)


def create_lyric_element(syllabic: str, text: str, no: str) -> etree._Element:
    """
    Create a new Lyrics element with the given syllabic, text, and no.
    """
    lyric_el: etree._Element = etree.Element("Lyrics")
    syllabic_el: etree._Element = etree.Element("syllabic")
    syllabic_el.text = syllabic
    lyric_el.append(syllabic_el)
    text_el: etree._Element = etree.Element("text")
    text_el.text = text
    lyric_el.append(text_el)
    no_el: etree._Element = etree.Element("no")
    no_el.text = no
    lyric_el.append(no_el)
    return lyric_el


def add_lyrics_to_staff(staff: etree._Element) -> None:
    found_lyrics: Dict[int, List[Dict[str, Any]]] = defaultdict(list)

    # Try to find a lyric for each Chord in the staff
    for el in loop_staff(staff):
        staff_id_loop: int = int(el["staff_id"])
        measure_index_loop: int = el["measure_index"]
        voice_index_loop: int = el["voice_index"]
        time_pos_loop: int = el["time_pos"]
        element_loop: etree._Element = el["element"]

        if element_loop.tag == "Chord":
            lyric_data: Optional[Dict[str, str]] = find_lyric(
                staff_id=staff_id_loop,
                measure_index=measure_index_loop,
                voice_index=voice_index_loop,
                time_pos=time_pos_loop,
            )
            if lyric_data:
                # Delete old lyrics
                for old_lyric in element_loop.findall(".//Lyrics"):
                    element_loop.remove(old_lyric)
                element_loop.append(
                    create_lyric_element(
                        lyric_data["syllabic"], lyric_data["text"], lyric_data["no"]
                    )
                )

            found_lyrics[staff_id_loop].append(
                {
                    "staff_id": staff_id_loop,
                    "measure_index": measure_index_loop,
                    "voice_index": voice_index_loop,
                    "lyric": lyric_data,
                    "element": element_loop,
                    "time_pos": time_pos_loop,
                }
            )

    for staff_id_found, lyrics_list in found_lyrics.items():
        for index, lyric_item in enumerate(lyrics_list):
            if lyric_item["lyric"] is not None:
                continue
            element_to_process: etree._Element = lyric_item["element"]
            # If element has Spanner type="Tie" and a <prev> inside it, skip.
            spanner: Optional[etree._Element] = element_to_process.find(
                ".//Spanner[@type='Tie']"
            )
            if spanner is not None:
                prev_el_spanner: Optional[etree._Element] = spanner.find(".//prev")
                if prev_el_spanner is not None:
                    continue
            prev_lyric_item: Optional[Dict[str, Any]] = (
                lyrics_list[index - 1] if index > 0 else None
            )
            prev_lyric_data: Optional[Dict[str, str]] = (
                prev_lyric_item["lyric"] if prev_lyric_item else None
            )
            next_lyric_item: Optional[Dict[str, Any]] = (
                lyrics_list[index + 1] if index < len(lyrics_list) - 1 else None
            )
            next_lyric_data: Optional[Dict[str, str]] = (
                next_lyric_item["lyric"] if next_lyric_item else None
            )
            # Go to previous time position in LYRICS_BY_TIMEPOS
            lyric_time_positions: List[str] = list(LYRICS_BY_TIMEPOS.keys())
            lyric_time_positions.sort(
                key=lambda x: (int(x.split("-")[0]), int(x.split("-")[1]))
            )
            time_pos_key: str = (
                f"{lyric_item['measure_index']}-{lyric_item['time_pos']}"
            )
            prev_time_pos_key: Optional[str] = None
            next_time_pos_key: Optional[str] = None

            def cmp_keys(key1: str, key2: str) -> int:
                """
                Compare two keys based on measure index and time position.
                """
                measure1, time1 = map(int, key1.split("-"))
                measure2, time2 = map(int, key2.split("-"))
                if measure1 < measure2:
                    return -1
                elif measure1 > measure2:
                    return 1
                else:
                    return time1 - time2

            for i, key in enumerate(lyric_time_positions):
                # compare key with time_pos_key
                # if we went past, we found the next time position
                if cmp_keys(key, time_pos_key) > 0:
                    # Found the key, now try to find a lyric in the previous time positions
                    prev_time_pos_key = lyric_time_positions[i - 1] if i > 0 else None
                    next_time_pos_key = lyric_time_positions[i]
                    break
            else:  # If the loop completes without a break (meaning time_pos_key is the last or only key)
                if len(lyric_time_positions) > 0:
                    if (
                        cmp_keys(time_pos_key, lyric_time_positions[-1]) == 0
                    ):  # If it's the last key
                        prev_time_pos_key = (
                            lyric_time_positions[-2]
                            if len(lyric_time_positions) > 1
                            else None
                        )
                        next_time_pos_key = None  # No next key
                    elif (
                        cmp_keys(time_pos_key, lyric_time_positions[0]) < 0
                    ):  # If it's before the first key
                        prev_time_pos_key = None
                        next_time_pos_key = lyric_time_positions[0]
                    else:  # Handle cases where time_pos_key isn't found or is exactly the last one
                        # This case is tricky, might need more specific logic depending on expected behavior
                        pass

            prev_matching_lyric: Optional[Dict[str, str]] = (
                find_lyric(
                    staff_id=staff_id_found,
                    measure_index=int(prev_time_pos_key.split("-")[0]),
                    voice_index=lyric_item["voice_index"],
                    time_pos=int(prev_time_pos_key.split("-")[1]),
                )
                if prev_time_pos_key
                else None
            )
            next_matching_lyric: Optional[Dict[str, str]] = (
                find_lyric(
                    staff_id=staff_id_found,
                    measure_index=int(next_time_pos_key.split("-")[0]),
                    voice_index=lyric_item["voice_index"],
                    time_pos=int(next_time_pos_key.split("-")[1]),
                )
                if next_time_pos_key
                else None
            )
            if prev_matching_lyric is not None and prev_matching_lyric["text"] != (
                prev_lyric_data["text"] if prev_lyric_data else None
            ):
                # This is good!
                logging.debug(
                    f"Found previous matching lyric for staff {staff_id_found}, measure {lyric_item['measure_index']}: {prev_matching_lyric}"
                )
                element_to_process.append(
                    create_lyric_element(
                        prev_matching_lyric["syllabic"],
                        prev_matching_lyric["text"],
                        prev_matching_lyric["no"],
                    )
                )
            elif next_matching_lyric is not None and next_matching_lyric["text"] != (
                next_lyric_data["text"] if next_lyric_data else None
            ):
                # This is good!
                logging.debug(
                    f"Found next matching lyric for staff {staff_id_found}, measure {lyric_item['measure_index']}: {next_matching_lyric}"
                )
                element_to_process.append(
                    create_lyric_element(
                        next_matching_lyric["syllabic"],
                        next_matching_lyric["text"],
                        next_matching_lyric["no"],
                    )
                )


def split_part(part: etree._Element) -> etree._Element:
    """
    Create a new Part element based on the original part.

    Args:
        part (etree._Element): The original Part XML element.

    Returns:
        etree._Element: A deep copy of the original Part element with updated staff IDs.
    """
    new_part: etree._Element = deepcopy(part)
    # Modify the new_part as needed
    for from_staff, to_staff in STAFF_MAPPING.items():
        # Update the staff ID in the new part
        for staff in new_part.findall(".//Staff"):
            if int(staff.get("id", "0")) == from_staff:
                staff.set("id", str(to_staff))
    return new_part


def get_rest_length(rest: etree._Element, tick_diff: int) -> int:
    """
    Get the length of a Rest element in ticks.

    Args:
        rest (etree._Element): The Rest XML element.
        tick_diff (int): The difference in ticks to subtract from the rest's duration.

    Returns:
        int: The adjusted duration of the rest in ticks.
    """
    duration_type: Optional[etree._Element] = rest.find(".//durationType")
    dots: Optional[etree._Element] = rest.find(".//dots")
    if duration_type is not None:
        return (
            resolve_duration(
                duration_type.text if duration_type is not None else "0",
                dots.text if dots is not None else "0",
            )
            - tick_diff
        )
    return 0


def shorten_rest_to(rest: etree._Element, new_duration_in_ticks: int) -> None:
    """
    Shorten a Rest element to a new duration in ticks.

    Args:
        rest (etree._Element): The Rest XML element to shorten.
        new_duration_in_ticks (int): The target duration in ticks.
    """
    BASE_NOTE_VALUES: Dict[str, List[float]] = {
        "whole": [1.0, 1.5, 1.75, 1.875],
        "half": [0.5, 0.75, 0.875, 0.9375],
        "quarter": [0.25, 0.375, 0.4375, 0.46875],
        "eighth": [0.125, 0.1875, 0.21875, 0.234375],
        "16th": [0.0625, 0.09375, 0.109375, 0.1171875],
        "32nd": [0.03125, 0.046875, 0.0546875, 0.05859375],
        "64th": [0.015625, 0.0234375, 0.02734375, 0.029296875],
        # Add more if needed
    }

    duration_type_el: Optional[etree._Element] = rest.find(".//durationType")
    if duration_type_el is not None:
        # Convert the new duration to a fraction
        if new_duration_in_ticks == 0:
            # If the new duration is 0, remove the rest
            parent: Optional[etree._Element] = rest.getparent()
            if parent is not None:
                parent.remove(rest)
        else:
            found_match: bool = False
            for note_type, values in BASE_NOTE_VALUES.items():
                for i, value in enumerate(values):
                    if int(value * RESOLUTION) == new_duration_in_ticks:
                        # Found the correct value
                        duration_type_el.text = note_type
                        # If there are dots, we need to adjust them
                        dots_el: Optional[etree._Element] = rest.find(".//dots")
                        if i > 0:
                            if dots_el is None:
                                dots_el = etree.Element("dots")
                                rest.append(dots_el)
                            # Set the number of dots based on the index
                            dots_el.text = str(i)
                        elif dots_el is not None:
                            # Remove dots if no longer needed
                            rest.remove(dots_el)
                        found_match = True
                        break
                if found_match:
                    break
            if not found_match:
                logging.warning(
                    f"Could not find a matching duration type for {new_duration_in_ticks} ticks."
                )
            logging.debug(
                f"Shortened rest to {duration_type_el.text if duration_type_el.text else 'unknown'} in element {rest.tag}"
            )


def preprocess_corrupted_measures(root: etree._Element) -> None:
    """
    Try to find measures with len="17/16" or similar
    and try to fix them.

    Args:
        root (etree._Element): The root XML element of the MuseScore file.
    """
    problem_measures: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for staff in root.findall(".//Score/Staff"):
        staff_id: int = int(staff.get("id", "0"))
        measure_index: int = -1
        time_sig: Optional[str] = None
        for measure in staff.findall(".//Measure"):
            new_time_sig_el: Optional[etree._Element] = measure.find(".//TimeSig")
            if new_time_sig_el is not None:
                sigN_el: Optional[etree._Element] = new_time_sig_el.find(".//sigN")
                sigD_el: Optional[etree._Element] = new_time_sig_el.find(".//sigD")
                if (
                    sigN_el is not None
                    and sigN_el.text is not None
                    and sigD_el is not None
                    and sigD_el.text is not None
                ):
                    time_sig = f"{sigN_el.text}/{sigD_el.text}"
            measure_index += 1
            problem_measure_flag: bool = measure.get(
                "len"
            ) is not None and "/" in measure.get("len", "")
            if problem_measure_flag:
                problem_measures[measure_index].append(
                    {
                        "staff_id": staff_id,
                        "measure": measure,
                        "len": measure.get("len"),
                        "elements": {},
                        "time_sig": time_sig,
                    }
                )

            for voice in measure.findall(".//voice"):
                voice_index: int = -1
                voice_index += 1
                time_pos: int = 0
                if problem_measure_flag:
                    if (
                        voice_index
                        not in problem_measures[measure_index][-1]["elements"]
                    ):
                        problem_measures[measure_index][-1]["elements"][voice_index] = {
                            "elements": {},
                            "max_time_pos": 0,
                        }
                for el in voice:
                    if problem_measure_flag:
                        problem_measures[measure_index][-1]["elements"][voice_index][
                            "elements"
                        ][time_pos] = el
                    if el.tag in ["Chord", "Rest"]:
                        duration_type: Optional[etree._Element] = el.find(
                            ".//durationType"
                        )
                        dots: Optional[etree._Element] = el.find(".//dots")
                        time_pos += resolve_duration(
                            duration_type.text if duration_type is not None else "0",
                            dots.text if dots is not None else "0",
                        )
                    if el.tag == "location":
                        fractions: Optional[etree._Element] = el.find(".//fractions")
                        if fractions is not None:
                            time_pos += resolve_duration(
                                fractions.text if fractions is not None else "0"
                            )

                    if problem_measure_flag:
                        problem_measures[measure_index][-1]["elements"][voice_index][
                            "max_time_pos"
                        ] = max(
                            problem_measures[measure_index][-1]["elements"][
                                voice_index
                            ]["max_time_pos"],
                            time_pos,
                        )

                if problem_measure_flag:
                    problem_measures[measure_index][-1]["elements"][voice_index][
                        "elements"
                    ][time_pos] = None

    # For each corrupted measure, try to fix it by adjusting the final rest in each voice
    # If all voices don't have a final rest, we can't fix it
    for measure_index, staff_list in problem_measures.items():
        possible_to_fix: bool = True
        max_time_pos_in_measure: int = 0
        for staff_values in staff_list:
            for voice_values in staff_values["elements"].values():
                max_time_pos_in_measure = max(
                    max_time_pos_in_measure, voice_values["max_time_pos"]
                )
        for staff_values in staff_list:
            for voice_values in staff_values["elements"].values():
                if voice_values["max_time_pos"] < max_time_pos_in_measure:
                    # Ignore this voice, it is not complete any way
                    continue
                # Check last element
                elements_in_voice: List[Any] = list(voice_values["elements"].values())
                if len(elements_in_voice) < 2:
                    possible_to_fix = False
                    break
                last_element_in_voice: Optional[etree._Element] = elements_in_voice[-2]
                if last_element_in_voice is None or last_element_in_voice.tag != "Rest":
                    possible_to_fix = False
                    break

        logging.debug(
            f"Measure {measure_index} is {'possible' if possible_to_fix else 'not possible'} to fix"
        )
        if possible_to_fix:
            time_sig_str: Optional[str] = staff_list[0]["time_sig"]
            correct_measure_len: float = 0.0
            if time_sig_str is not None and "/" in time_sig_str:
                sig_n_str, sig_d_str = time_sig_str.split("/")
                sig_n: int = int(sig_n_str)
                sig_d: int = int(sig_d_str)
                correct_measure_len = RESOLUTION * (sig_n / sig_d)
            elif time_sig_str is not None:
                correct_measure_len = int(time_sig_str) * RESOLUTION

            cant_fix_current_measure: bool = False
            elements_to_remove: List[etree._Element] = []
            rests_to_shorten: List[Tuple[etree._Element, int]] = []
            for staff_values in staff_list:
                if cant_fix_current_measure:
                    break
                for voice_index, voice_values in staff_values["elements"].items():
                    prev_el: Optional[etree._Element] = None
                    prev_prev_el: Optional[etree._Element] = None
                    remove_rest_of_elements: bool = False

                    elements_list_in_voice: List[
                        Tuple[int, Optional[etree._Element]]
                    ] = list(voice_values["elements"].items())
                    for time_pos, element in elements_list_in_voice:
                        element_tag: Optional[str] = (
                            element.tag if element is not None else None
                        )
                        if remove_rest_of_elements:
                            if element_tag == "Chord":
                                cant_fix_current_measure = True
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} voice {voice_index} has a chord after prev deleted, cannot fix."
                                )
                                logging.debug(
                                    f"element xml: {etree.tostring(element, pretty_print=True).decode('utf-8')}"
                                )
                                break
                            # We have started removing elements, so we will remove all after it
                            if element is not None:
                                elements_to_remove.append(element)
                            continue

                        if element is not None and time_pos == correct_measure_len:
                            # Nice, there is a rest at the end of the measure.
                            # Just remove this element and all after it.
                            if element_tag == "Chord":
                                cant_fix_current_measure = True
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has a chord at the end, cannot fix."
                                )
                                break
                            elements_to_remove.append(element)
                            remove_rest_of_elements = True
                            continue

                        if time_pos > correct_measure_len:
                            # We have passed the correct measure length
                            # We need to shorten the previous rest and remove all after it
                            if prev_el is None:
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has no previous element to shorten."
                                )
                                cant_fix_current_measure = True
                                break
                            if prev_el.tag == "Chord":
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has no previous rest to shorten."
                                )
                                cant_fix_current_measure = True
                                break
                            # Shorten the previous rest
                            if correct_measure_len - time_pos <= 0:
                                # Can't shorten it enough, we need to remove it
                                elements_to_remove.append(prev_el)
                                if prev_prev_el is not None:
                                    if prev_prev_el.tag != "Rest":
                                        logging.warning(
                                            f"Measure {measure_index} in staff {staff_values['staff_id']} has no prev previous rest to shorten."
                                        )
                                        cant_fix_current_measure = True
                                        break
                                    # If there is a previous element, we can shorten it
                                    # By a delta...
                                    logging.debug(
                                        f"Shortening prev_prev rest in time_pos {time_pos} in staff {staff_values['staff_id']}, measure {measure_index}, voice {voice_index} to 0 ticks"
                                    )
                                    rests_to_shorten.append(
                                        (
                                            prev_prev_el,
                                            get_rest_length(
                                                prev_prev_el,
                                                int(time_pos - correct_measure_len),
                                            ),
                                        )
                                    )
                            else:
                                logging.debug(
                                    f"Shortening rest in time_pos {time_pos} in staff {staff_values['staff_id']}, measure {measure_index}, voice {voice_index} to {int(correct_measure_len - time_pos)} ticks"
                                )
                                rests_to_shorten.append(
                                    (prev_el, int(correct_measure_len - time_pos))
                                )
                            if element_tag == "Chord":
                                cant_fix_current_measure = True
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has a chord after the rest, cannot fix."
                                )
                                break
                            if element is not None:
                                elements_to_remove.append(element)
                            remove_rest_of_elements = True
                            continue

                        prev_prev_el = prev_el
                        prev_el = element

                    if cant_fix_current_measure:
                        logging.warning(
                            f"Measure {measure_index} in staff {staff_values['staff_id']} cannot be fixed."
                        )
                        break

            if cant_fix_current_measure:
                continue

            if rests_to_shorten:
                for el, new_duration in rests_to_shorten:
                    logging.debug(
                        f"Shortening rest {el.tag} in, measure {measure_index} to {new_duration} ticks"
                    )
                    shorten_rest_to(el, new_duration)
            if elements_to_remove:
                logging.debug(
                    f"Removing elements {elements_to_remove} from, measure {measure_index}"
                )
                for element_to_remove in elements_to_remove:
                    if element_to_remove is not None:
                        parent: Optional[etree._Element] = element_to_remove.getparent()
                        if parent is not None:
                            parent.remove(element_to_remove)

                # remove len attribute from the measure
                for staff_values in staff_list:
                    measure: Optional[etree._Element] = staff_values["measure"]
                    if measure is not None:
                        if "len" in measure.attrib:
                            del measure.attrib["len"]
                        logging.debug(
                            f"Removed len attribute from measure {measure_index} in staff {staff_values['staff_id']}"
                        )


def save_lyrics(input_path: str) -> None:
    # save lyrics by time position tsv file
    if LYRICS_BY_TIMEPOS:
        keys = [
            "staff_id",
            "measure_index",
            "voice_index",
            "time_pos",
            "text",
            "syllabic",
            "no",
        ]
        lyrics_by_timepos_path: str = input_path.replace(".mscx", "_lyrics.tsv")
        with open(lyrics_by_timepos_path, "w", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, delimiter="\t")
            writer.writeheader()
            sorted_lyrics_by_timepos: List[str] = sorted(
                LYRICS_BY_TIMEPOS.keys(),
                key=lambda x: (int(x.split("-")[0]), int(x.split("-")[1])),
            )
            for measure_time_pos in sorted_lyrics_by_timepos:
                lyrics_list: List[Dict[str, Any]] = LYRICS_BY_TIMEPOS[measure_time_pos]
                time_pos = measure_time_pos.split("-")[1]
                for lyric_item in lyrics_list:
                    writer.writerow(
                        {
                            "staff_id": lyric_item["staff_id"],
                            "measure_index": lyric_item["measure_index"],
                            "voice_index": lyric_item["voice_index"],
                            "time_pos": time_pos,
                            "text": lyric_item["lyric"]["text"],
                            "syllabic": lyric_item["lyric"]["syllabic"],
                            "no": lyric_item["lyric"]["no"],
                        }
                    )
        logging.info(f"Saved lyrics by time position to {lyrics_by_timepos_path}")


def load_lyrics(input_path: str) -> None:
    """
    Try to open fixed lyrics file
    """
    lyrics_by_timepos_path: str = input_path.replace(".mscx", "_lyrics_fixed.tsv")
    try:
        with open(lyrics_by_timepos_path, "r", encoding="utf-8") as f:
            LYRICS_BY_TIMEPOS.clear()
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                measure_time_pos = f"{row['measure_index']}-{row['time_pos']}"
                if measure_time_pos not in LYRICS_BY_TIMEPOS:
                    LYRICS_BY_TIMEPOS[measure_time_pos] = []
                LYRICS_BY_TIMEPOS[measure_time_pos].append(
                    {
                        "staff_id": int(row["staff_id"]),
                        "measure_index": int(row["measure_index"]),
                        "voice_index": int(row["voice_index"]),
                        "lyric": {
                            "text": row["text"],
                            "syllabic": row["syllabic"],
                            "no": row["no"],
                        },
                    }
                )

        return True
    except FileNotFoundError:
        logging.info(f"Fixed lyrics file not found: {lyrics_by_timepos_path}")
        return False


def main(input_path: str, output_path: str) -> None:
    """
    Converts a MuseScore XML file from a single-staff, two-voice structure
    to a two-staff, single-voice-per-staff structure, and duplicates the Part
    element, handling stem directions, location tags, lyrics, and specific
    time signature changes for medium_1.

    Args:
        input_path (str): Path to the input MuseScore XML file.
        output_path (str): Path where the converted XML file will be saved.
    """
    global STAFF_MAPPING, REVERSED_VOICES_BY_STAFF_MEASURE, LYRICS_BY_TIMEPOS
    STAFF_MAPPING = {}
    REVERSED_VOICES_BY_STAFF_MEASURE = {}
    LYRICS_BY_TIMEPOS = {}

    with open(input_path, "r", encoding="utf-8") as f:
        input_content: str = f.readlines()

    # Parse the input XML
    root: etree._Element = etree.fromstringlist(input_content)

    # Perform the conversion
    staffs: List[etree._Element] = root.findall(".//Staff")
    if not staffs:
        raise ValueError("No Staff elements found in the input XML.")

    preprocess_corrupted_measures(root)
    # Convert staff ids to make space after each staff
    # id="1" becomes id="1" and
    # id="2" becomes id="3"
    # so 2n - 1
    # ... unless the staff only has one voice, then we don't even split it.

    staffs_to_split: Set[int] = set()
    for staff in staffs:
        staff_id: int = int(staff.get("id", "0"))
        logging.debug(f"Processing staff with id {staff_id}")
        # Check each measure in the staff
        # If any has two voices, we need to split it
        for measure in staff.findall(".//Measure"):
            if len(measure.findall(".//voice")) > 1:
                staffs_to_split.add(staff_id)
                break

    logging.debug(f"Staffs to split: {staffs_to_split}")
    # e.g.
    # If we have staffs with ids 1, 2, 3, 4, 5
    # and we need to split 1, 2 and 4, we will end up with
    # 1 -> 1,2
    # 2 -> 3,4
    # 3 -> 5
    # 4 -> 6,7
    # 5 -> 8
    new_staff_id: int = 1
    new_staffs_to_split: Set[int] = set()
    for staff in staffs:
        staff_id_orig: int = int(staff.get("id", "0"))
        if staff_id_orig == 1:
            # Reset the new_staff_id to 1 for the first staff
            # since there are two lists of staffs in the xml
            new_staff_id = 1

        staff.set("id", str(new_staff_id))
        logging.debug(f"Updated staff id from {staff_id_orig} to {new_staff_id}")
        if staff_id_orig not in staffs_to_split:
            # If the staff does not need to be split, we can let the next id be next to it
            new_staff_id += 1
        else:
            new_staffs_to_split.add(new_staff_id)
            new_staff_id += 2

    for staff_id_current in new_staffs_to_split:
        STAFF_MAPPING[staff_id_current] = int(str(staff_id_current + 1))

    logging.debug("Staff mapping: %s", STAFF_MAPPING)

    # Find the Part elements
    parts: List[etree._Element] = root.findall(".//Part")
    if not parts:
        raise ValueError("No Part elements found in the input XML.")

    for part in parts:
        staff_in_part: Optional[etree._Element] = part.find(".//Staff")
        if staff_in_part is None:
            raise ValueError("No Staff element found in the Part element.")
        staff_id_in_part: int = int(staff_in_part.get("id", "0"))
        if staff_id_in_part not in STAFF_MAPPING:
            continue
        # Split the part into two separate parts
        new_part: etree._Element = split_part(part)
        parent_of_part: Optional[etree._Element] = part.getparent()
        if parent_of_part is not None:
            parent_of_part.insert(parent_of_part.index(part) + 1, new_part)

    for staff_id_orig_split, new_staff_id_split in STAFF_MAPPING.items():
        # Find <Staff> element with staff_id
        # Which is a direct child of <Score>
        staff_element_up: Optional[etree._Element] = root.find(
            f".//Score/Staff[@id='{staff_id_orig_split}']"
        )
        if staff_element_up is not None:
            find_reversed_voices_by_staff_measure(staff_element_up)
            # Read lyrics from the staff
            read_lyrics(staff_element_up)
            new_staff_element_down: etree._Element = deepcopy(staff_element_up)
            new_staff_element_down.set("id", str(new_staff_id_split))
            # Insert the new Staff element into the Score next to the original
            score_element: Optional[etree._Element] = root.find(".//Score")
            if score_element is not None:
                score_element.insert(
                    score_element.index(staff_element_up) + 1, new_staff_element_down
                )

    for staff_id_orig_split, new_staff_id_split in STAFF_MAPPING.items():
        up_staff_element: Optional[etree._Element] = root.find(
            f".//Score/Staff[@id='{staff_id_orig_split}']"
        )
        if up_staff_element is not None:
            handle_staff(up_staff_element, "up")
        down_staff_element: Optional[etree._Element] = root.find(
            f".//Score/Staff[@id='{new_staff_id_split}']"
        )
        if down_staff_element is not None:
            handle_staff(down_staff_element, "down")

    # Handle rest of staffs to remove extra elements
    for staff in root.findall(".//Score/Staff"):
        staff_id_current: int = int(staff.get("id", "0"))
        if staff_id_current in STAFF_MAPPING:
            # This staff is already handled as 'up' voice
            continue
        if staff_id_current in set(STAFF_MAPPING.values()):
            # This staff is a new staff created by the split (handled as 'down' voice)
            continue
        # Handle the staff (for staffs that were not split)
        handle_staff(staff, None)

    add_missing_ties(root)

    part_types = detect_part_types(root)
    # Apply part name
    for part in root.findall(".//Part"):
        staff: Optional[etree._Element] = part.find(".//Staff")
        if staff is not None:
            staff_id: int = int(staff.get("id"))
            if staff_id in part_types:
                part_name = part_types[staff_id].get("part_name", "")
                part_slug = part_types[staff_id].get("part_slug", "")
                part_index = part_types[staff_id].get("part_index", 1)
                track_name = part.find(".//trackName")
                if track_name is not None:
                    track_name.text = f"{part_slug}{part_index}"
                long_name = part.find(".//longName")
                if long_name is not None:
                    long_name.text = f"{part_name} {part_index}"

    # apply clef
    for staff in root.findall(".//Score/Staff"):
        staff_id: int = int(staff.get("id", "0"))
        if staff_id in part_types:
            clef_type: Optional[str] = part_types[staff_id].get("clef_type", None)
            if clef_type is not None:
                clef = staff.find(".//Clef")
                if clef is not None:
                    concert_clef_type = clef.find(".//concertClefType")
                    if concert_clef_type is not None:
                        concert_clef_type.text = clef_type
                        logging.debug(
                            f"Set concertClefType to {clef_type} for staff {staff_id}"
                        )
                    transposing_clef_type = clef.find(".//transposingClefType")
                    if transposing_clef_type is not None:
                        transposing_clef_type.text = clef_type

    if load_lyrics(input_path):
        logging.info("Loaded lyrics from fixed lyrics file.")
    else:
        logging.info("No fixed lyrics file found, saving current lyrics.")
        save_lyrics(input_path)

    # add lyrics to the staff
    for staff in root.findall(".//Score/Staff"):
        add_lyrics_to_staff(staff)

    # Serialize the output XML
    output_content: str = etree.tostring(
        root, pretty_print=True, encoding="UTF-8"
    ).decode("UTF-8")

    # Write the output XML to the specified file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_content)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert MuseScore XML from single-staff, two-voice to two-staff, single-voice-per-staff."
    )
    parser.add_argument("input", help="Path to the input MuseScore XML file.")
    parser.add_argument("output", help="Path to save the converted MuseScore XML file.")
    args = parser.parse_args()

    logging.info(f"Converting {args.input} to {args.output}")
    try:
        main(args.input, args.output)
        logging.info("Conversion completed successfully.")
        logging.info(f"Output written to {args.output}")
    except Exception as e:
        logging.error(f"An error occurred during conversion: {e}")
        raise
