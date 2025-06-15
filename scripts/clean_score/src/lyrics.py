#!/usr/bin/env python3

from collections import defaultdict
import csv
from lxml import etree

import logging
from typing import Dict, List, Optional, Any

from .globals import (
    LYRICS_BY_TIMEPOS,
    REVERSED_VOICES_BY_STAFF_MEASURE,
)

from .utils import get_original_staff_id, loop_staff

logger = logging.getLogger(__name__)


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
                logger.debug(
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
                logger.debug(
                    f"Found next matching lyric for staff {staff_id_found}, measure {lyric_item['measure_index']}: {next_matching_lyric}"
                )
                element_to_process.append(
                    create_lyric_element(
                        next_matching_lyric["syllabic"],
                        next_matching_lyric["text"],
                        next_matching_lyric["no"],
                    )
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
        logger.info(f"Saved lyrics by time position to {lyrics_by_timepos_path}")


def load_lyrics(input_path: str) -> None:
    """
    Try to open fixed lyrics file
    """
    global LYRICS_BY_TIMEPOS
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
        logger.info(f"Fixed lyrics file not found: {lyrics_by_timepos_path}")
        return False
