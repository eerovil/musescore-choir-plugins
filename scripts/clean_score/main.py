#!/usr/bin/env python3

from copy import deepcopy
import os
from lxml import etree

import logging
from typing import List, Set, Optional

from src.globals import GLOBALS

from src.gemini_api import fix_lyrics
from src.lyrics import (
    add_lyrics_to_staff,
    load_lyrics,
    read_lyrics,
    remove_lyrics_from_chord_with_tie_prev,
    save_lyrics,
)
from src.missing_ties import add_missing_ties
from src.part_types import detect_part_types
from src.reversed_voices import (
    find_reversed_voices_by_staff_measure,
)

from src.corrupted_measures import preprocess_corrupted_measures

from src.utils import (
    delete_all_elements_by_selector,
    get_original_staff_id,
    default_timesig,
    default_keysig,
)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def handle_staff(staff: etree._Element, direction: Optional[str]) -> None:
    """
    Deletes notes not matching the specified direction and cleans up other elements.

    Args:
        staff (etree._Element): The staff XML element to process.
        direction (Optional[str]): The direction to keep notes ("up" or "down"), or None to keep all.
    """
    staff_id: int = int(staff.get("id", "0"))
    original_staff_id: int = get_original_staff_id(staff_id)

    logger.debug(f"Handling staff {staff_id} for direction {direction}")
    if direction is not None:
        index: int = -1
        for measure in staff.findall(".//Measure"):
            index += 1
            reversed_voices: bool = GLOBALS.REVERSED_VOICES_BY_STAFF_MEASURE.get(
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
            logger.debug(
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
    for from_staff, to_staff in GLOBALS.STAFF_MAPPING.items():
        # Update the staff ID in the new part
        for staff in new_part.findall(".//Staff"):
            if int(staff.get("id", "0")) == from_staff:
                staff.set("id", str(to_staff))
    return new_part


def main(input_path: str, output_path: str, pdf_path: str = None) -> None:
    """
    Converts a MuseScore XML file from a single-staff, two-voice structure
    to a two-staff, single-voice-per-staff structure, and duplicates the Part
    element, handling stem directions, location tags, lyrics, and specific
    time signature changes for medium_1.

    Args:
        input_path (str): Path to the input MuseScore XML file.
        output_path (str): Path where the converted XML file will be saved.
    """
    GLOBALS.STAFF_MAPPING = {}
    GLOBALS.REVERSED_VOICES_BY_STAFF_MEASURE = {}
    GLOBALS.LYRICS_BY_TIMEPOS = {}

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
        logger.debug(f"Processing staff with id {staff_id}")
        # Check each measure in the staff
        # If any has two voices, we need to split it
        for measure in staff.findall(".//Measure"):
            if len(measure.findall(".//voice")) > 1:
                staffs_to_split.add(staff_id)
                break

    logger.debug(f"Staffs to split: {staffs_to_split}")
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
        logger.debug(f"Updated staff id from {staff_id_orig} to {new_staff_id}")
        if staff_id_orig not in staffs_to_split:
            # If the staff does not need to be split, we can let the next id be next to it
            new_staff_id += 1
        else:
            new_staffs_to_split.add(new_staff_id)
            new_staff_id += 2

    for staff_id_current in new_staffs_to_split:
        GLOBALS.STAFF_MAPPING[staff_id_current] = int(str(staff_id_current + 1))

    logger.debug("Staff mapping: %s", GLOBALS.STAFF_MAPPING)

    # Find the Part elements
    parts: List[etree._Element] = root.findall(".//Part")
    if not parts:
        raise ValueError("No Part elements found in the input XML.")

    # Make sure each part only has one staff. If not, copy part and move staff there
    for part in parts:
        staffs_in_part: Optional[etree._Element] = part.findall(".//Staff")
        if len(staffs_in_part) <= 1:
            continue
        for extra_staff in staffs_in_part[1:]:
            # Split the part into two separate parts
            new_part: etree._Element = deepcopy(part)
            parent_of_part: Optional[etree._Element] = part.getparent()
            if parent_of_part is not None:
                parent_of_part.insert(parent_of_part.index(part) + 1, new_part)
            # Delete all except extra_staff from new_part
            for to_delete_staff in new_part.findall(".//Staff"):
                if to_delete_staff.get("id") == extra_staff.get("id"):
                    continue
                new_part.remove(to_delete_staff)
            part.remove(extra_staff)

    parts: List[etree._Element] = root.findall(".//Part")
    for part in parts:
        staff_in_part: Optional[etree._Element] = part.find(".//Staff")
        if staff_in_part is None:
            raise ValueError("No Staff element found in the Part element.")
        staff_id_in_part: int = int(staff_in_part.get("id", "0"))
        if staff_id_in_part not in GLOBALS.STAFF_MAPPING:
            continue
        # Split the part into two separate parts
        new_part: etree._Element = split_part(part)
        parent_of_part: Optional[etree._Element] = part.getparent()
        if parent_of_part is not None:
            parent_of_part.insert(parent_of_part.index(part) + 1, new_part)

    for staff_id_orig_split, new_staff_id_split in GLOBALS.STAFF_MAPPING.items():
        # Find <Staff> element with staff_id
        # Which is a direct child of <Score>
        staff_element_up: Optional[etree._Element] = root.find(
            f".//Score/Staff[@id='{staff_id_orig_split}']"
        )
        if staff_element_up is not None:
            find_reversed_voices_by_staff_measure(staff_element_up)
            # Read lyrics from the staff
            new_staff_element_down: etree._Element = deepcopy(staff_element_up)
            new_staff_element_down.set("id", str(new_staff_id_split))
            # Insert the new Staff element into the Score next to the original
            score_element: Optional[etree._Element] = root.find(".//Score")
            if score_element is not None:
                score_element.insert(
                    score_element.index(staff_element_up) + 1, new_staff_element_down
                )

    # Read lyrics from all staffs
    for staff in root.findall(".//Score/Staff"):
        read_lyrics(staff)

    for staff_id_orig_split, new_staff_id_split in GLOBALS.STAFF_MAPPING.items():
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
        if staff_id_current in GLOBALS.STAFF_MAPPING:
            # This staff is already handled as 'up' voice
            continue
        if staff_id_current in set(GLOBALS.STAFF_MAPPING.values()):
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
                        logger.debug(
                            f"Set concertClefType to {clef_type} for staff {staff_id}"
                        )
                    transposing_clef_type = clef.find(".//transposingClefType")
                    if transposing_clef_type is not None:
                        transposing_clef_type.text = clef_type

    if load_lyrics(input_path):
        logger.info("Loaded lyrics from fixed lyrics file.")
    else:
        logger.info("No fixed lyrics file found, saving current lyrics.")
        save_lyrics(input_path)
        # Try using gemini API to fix lyrics
        if pdf_path:
            fix_lyrics(input_path, pdf_path)
            load_lyrics(input_path)

    # add lyrics to the staff
    for staff in root.findall(".//Score/Staff"):
        add_lyrics_to_staff(staff)

    remove_lyrics_from_chord_with_tie_prev(root)
    # delete all bracket
    delete_all_elements_by_selector(root, ".//bracket")
    # delete all barLineSpan
    delete_all_elements_by_selector(root, ".//barLineSpan")

    # Serialize the output XML
    output_content: str = etree.tostring(
        root, pretty_print=True, encoding="UTF-8"
    ).decode("UTF-8")

    # Write the output XML to the specified file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_content)


if __name__ == "__main__":
    """
    How to use
    Create new folder here called "Your song"
    Insert into it a uncompressed MuseScore file (mscx) (NOT a mscz file)
    Also add the original PDF file if you want to fix the lyrics using Gemini API
    Run ./main.py "Your song"
    The output will be saved as "Your song/Your song_split.mscx"

    For gemini api, set .env variable GEMINI_API_KEY to your API key

    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert MuseScore XML from single-staff, two-voice to two-staff, single-voice-per-staff."
    )
    parser.add_argument("input", help="Path to the input MuseScore XML file.")
    parser.add_argument(
        "--output", help="Path to save the converted MuseScore XML file."
    )
    parser.add_argument(
        "--pdf",
        help="Path to the PDF file for lyrics extraction (optional).",
        default=None,
    )
    args = parser.parse_args()

    # Input can be a dir, in that case we use any input file that is a *.mscx file and does not end with _split.mscx
    if os.path.isdir(args.input):
        input_dir = os.path.abspath(args.input)
        input_files = [
            f
            for f in os.listdir(input_dir)
            if f.endswith(".mscx") and not f.endswith("_split.mscx")
        ]
        if not input_files:
            raise ValueError(
                "No valid MuseScore XML files found in the specified directory."
            )
        args.input = os.path.join(input_dir, input_files[0])
        if not args.output:
            args.output = args.input.replace(".mscx", "_split.mscx")
        if not args.pdf:
            # Find the PDF file in the same directory
            pdf_files = [f for f in os.listdir(input_dir) if f.endswith(".pdf")]
            if pdf_files:
                args.pdf = os.path.join(input_dir, pdf_files[0])
            else:
                args.pdf = None
        logger.info(f"Using input file: {args.input}")

    logger.info(f"Converting {args.input} to {args.output}")
    try:
        main(args.input, args.output, args.pdf)
        logger.info("Conversion completed successfully.")
        logger.info(f"Output written to {args.output}")
    except Exception as e:
        logger.error(f"An error occurred during conversion: {e}")
        raise
