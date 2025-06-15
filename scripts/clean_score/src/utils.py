#!/usr/bin/env python3

from lxml import etree

import logging
from typing import Dict, List, Optional, Any

from .globals import RESOLUTION, STAFF_MAPPING

logger = logging.getLogger(__name__)


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
                logger.warning(
                    f"Could not find a matching duration type for {new_duration_in_ticks} ticks."
                )
            logger.debug(
                f"Shortened rest to {duration_type_el.text if duration_type_el.text else 'unknown'} in element {rest.tag}"
            )
