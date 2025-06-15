#!/usr/bin/env python3

from collections import defaultdict
from lxml import etree

import logging
from typing import Dict, List, Optional, Any, Tuple

from .globals import RESOLUTION
from .utils import (
    get_rest_length,
    resolve_duration,
    shorten_rest_to,
)

logging.basicConfig(level=logging.DEBUG)


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
