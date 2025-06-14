#!/usr/bin/env python3

from collections import defaultdict
from copy import deepcopy
import json
from lxml import etree

import logging

logging.basicConfig(level=logging.DEBUG)


STAFF_MAPPING = {}
REVERSED_VOICES_BY_STAFF_MEASURE = {}
LYRICS_BY_TIMEPOS = {}
RESOLUTION = 128  # Default resolution for durations in MuseScore XML


def resolve_duration(fraction_or_duration, dots="0"):
    """
    Resolves a duration string (either a fraction like "1/4" or a MuseScore duration type like "quarter")
    into its equivalent duration in ticks.

    Args:
        fraction_or_duration (str): A string representing a musical duration.
                                   Examples: "1/4", "half", "quarter", "eighth", "16th".

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
        duration_map = {
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
        ret = duration_map.get(fraction_or_duration.lower(), 0)
        if dots == "1":
            ret += ret // 2  # Add half of the duration for one dot
        elif dots == "2":
            ret += (ret // 2) + (ret // 4)
        elif dots == "3":
            ret += (ret // 2) + (ret // 4) + (ret // 8)
        return ret


def lyric_to_dict(lyric):
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


def find_lyric(staff_id=None, measure_index=None, voice_index=None, time_pos=None):
    """
    Find a lyric for the given staff ID, measure index, voice index, and time position.
    Returns the first lyric found or None if no lyric is found.
    """
    global LYRICS_BY_TIMEPOS
    key = f"{measure_index}-{time_pos}"
    if key in LYRICS_BY_TIMEPOS:
        lyric_choices = LYRICS_BY_TIMEPOS[key]
        # Try to find the most correct lyric.
        # Sometimes there is verse 2 lyric in the staff above
        # That would mean the lyric is for the upper voice in the lower staff
        original_staff_id = get_original_staff_id(staff_id)
        upper_staff_id = str(int(original_staff_id) - 2)
        if voice_index == 0:
            for lyric in lyric_choices:
                if lyric["staff_id"] == upper_staff_id and lyric["lyric"]["no"] == "1":
                    # Force "no" to be empty
                    lyric["lyric"]["no"] = ""
                    return lyric["lyric"]

        # If voice_index and original_staff_id matches, that's the best match.
        for lyric in lyric_choices:
            if (
                lyric["voice_index"] == voice_index
                and lyric["staff_id"] == original_staff_id
            ):
                return lyric["lyric"]
        # if staff_id matches, that's the next best match.
        for lyric in lyric_choices:
            if lyric["staff_id"] == original_staff_id:
                return lyric["lyric"]
        # If no staff_id match, try to find a lyric with the same voice_index
        # If voice_index matches, that's the best match.
        for lyric in lyric_choices:
            if lyric["voice_index"] == voice_index:
                return lyric["lyric"]
        # If no exact match, return the first lyric found
        for lyric in lyric_choices:
            return lyric["lyric"]

    return None


def default_keysig():
    """
    Returns a default key signature element.
    This is used to ensure that the key signature is set correctly in the output.
    """
    keysig = etree.Element("KeySig")
    accidental = etree.Element("accidental")
    accidental.text = "0"
    keysig.append(accidental)
    return keysig


def default_timesig():
    """
    Returns a default time signature element.
    This is used to ensure that the time signature is set correctly in the output.
    """
    timesig = etree.Element("TimeSig")
    sigN = etree.Element("sigN")
    sigN.text = "4"
    sigD = etree.Element("sigD")
    sigD.text = "4"
    timesig.append(sigN)
    timesig.append(sigD)
    return timesig


def loop_staff(staff):
    """
    Generator function to loop through the staff and yield elements
    with their time positions.
    """
    staff_id = int(staff.get("id"))
    measure_index = -1
    for measure in staff.findall(".//Measure"):
        measure_index += 1
        voice_index = -1
        for voice in measure.findall(".//voice"):
            voice_index += 1
            time_pos = 0
            for el in voice:
                # logging.debug(
                #     f"Yielding element {el.tag} in staff {staff_id}, measure {measure_index}, voice {voice_index}, time position {time_pos}"
                # )
                yield {
                    "staff_id": staff_id,
                    "measure_index": measure_index,
                    "voice_index": voice_index,
                    "time_pos": time_pos,
                    "element": el,
                }
                if el.tag in ["Chord", "Rest"]:
                    duration_type = el.find(".//durationType")
                    dots = el.find(".//dots")
                    time_pos += resolve_duration(
                        duration_type.text if duration_type is not None else "0",
                        dots.text if dots is not None else "0",
                    )
                if el.tag == "location":
                    fractions = el.find(".//fractions")
                    if fractions is not None:
                        time_pos += resolve_duration(
                            fractions.text if fractions is not None else "0"
                        )


def read_lyrics(staff):
    """
    Read lyrics from the staff and store them in a dictionary.
    The dictionary is keyed by staff ID and time position.
    """
    staff_id = int(staff.get("id"))
    global LYRICS_BY_TIMEPOS, REVERSED_VOICES_BY_STAFF_MEASURE
    for el in loop_staff(staff):
        staff_id = int(el["staff_id"])
        measure_index = el["measure_index"]
        voice_index = el["voice_index"]
        time_pos = el["time_pos"]
        element = el["element"]

        reversed_voices = REVERSED_VOICES_BY_STAFF_MEASURE.get(staff_id, {}).get(
            measure_index, False
        )
        if reversed_voices:
            # If the voices are reversed, we need to adjust the voice index
            voice_index = 1 if voice_index == 0 else 0

        if element.tag == "Chord":
            for lyric in element.findall(".//Lyrics"):
                # logging.debug(
                #     f"Found lyric in staff {staff_id}, measure {measure_index}, voice {voice_index}, time position {time_pos}: {lyric_to_dict(lyric)}"
                # )
                LYRICS_BY_TIMEPOS[f"{measure_index}-{time_pos}"] = (
                    LYRICS_BY_TIMEPOS.get(f"{measure_index}-{time_pos}", [])
                )
                LYRICS_BY_TIMEPOS[f"{measure_index}-{time_pos}"].append(
                    {
                        "staff_id": staff_id,
                        "measure_index": measure_index,
                        "voice_index": voice_index,
                        "lyric": lyric_to_dict(lyric),
                    }
                )


def find_reversed_voices_by_staff_measure(staff):
    """
    Find reversed voices for a given staff ID.
    This function should return a list of reversed voices for the specified staff.
    """
    REVERSED_VOICES_BY_STAFF_MEASURE[int(staff.get("id"))] = {}
    index = -1
    for measure in staff.findall(".//Measure"):
        index += 1
        voice_index = -1
        for voice in measure.findall(".//voice"):
            voice_index += 1
            for chord in voice.findall(".//Chord"):
                stem_direction = chord.find(".//StemDirection")
                if stem_direction is None:
                    continue  # No stem direction, skip this chord
                else:
                    stem_direction = stem_direction.text.strip().lower()
                stem_voice = 0 if stem_direction == "up" else 1
                if stem_voice != voice_index:
                    # This voice is reversed (up stem but voice 2)
                    REVERSED_VOICES_BY_STAFF_MEASURE[int(staff.get("id"))][index] = True


def get_original_staff_id(staff_id):
    original_staff_id = staff_id
    for parent_staff_id, child_staff_id in STAFF_MAPPING.items():
        if child_staff_id == staff_id:
            original_staff_id = parent_staff_id
            break
    return original_staff_id


def delete_all_elements_by_selector(staff, selector):
    """
    Delete all elements with the specified tag from the staff.
    """
    for element in staff.findall(selector):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)


def handle_staff(staff, direction):
    """
    Delete notes not matching the specified direction
    """
    staff_id = int(staff.get("id"))
    original_staff_id = get_original_staff_id(staff_id)

    logging.debug(f"Handling staff {staff_id} for direction {direction}")
    if direction is not None:
        index = -1
        for measure in staff.findall(".//Measure"):
            index += 1
            reversed_voices = REVERSED_VOICES_BY_STAFF_MEASURE.get(
                original_staff_id, {}
            ).get(index, False)
            if reversed_voices:
                voice_to_remove = 1 if direction == "down" else 0
            else:
                voice_to_remove = 1 if direction == "up" else 0
            voice_index = -1
            voices = list(measure.findall(".//voice"))
            keysig = deepcopy(measure.find(".//KeySig"))
            timesig = deepcopy(measure.find(".//TimeSig"))
            clef = deepcopy(measure.find(".//Clef"))
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
                            notes = sorted(
                                chord.findall(".//Note"),
                                key=lambda n: int(n.find(".//pitch").text),
                            )
                            if voice_to_remove == 0:
                                # Remove the upper note
                                if len(notes) > 1:
                                    chord.remove(notes[-1])
                            else:
                                # Remove the lower note
                                if len(notes) > 1:
                                    chord.remove(notes[0])

    # Finally, set StemDiretion up for all Chords in the staff
    for chord in staff.findall(".//Chord"):
        stem_direction = chord.find(".//StemDirection")
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
        time_stretch = etree.Element("timeStretch")
        time_stretch.text = "3"
        fermata.append(time_stretch)

    # Try to find a lyric for each Chord in the staff
    for el in loop_staff(staff):
        staff_id = int(el["staff_id"])
        measure_index = el["measure_index"]
        voice_index = el["voice_index"]
        time_pos = el["time_pos"]
        element = el["element"]

        if element.tag == "Chord":
            lyric = find_lyric(
                staff_id=staff_id,
                measure_index=measure_index,
                voice_index=voice_index,
                time_pos=time_pos,
            )
            # logging.debug(
            #     f"Found lyric for staff {staff_id}, measure {measure_index}, voice {voice_index}, time position {time_pos}: {lyric}"
            # )
            if lyric:
                # Delete old lyrics
                for old_lyric in element.findall(".//Lyrics"):
                    element.remove(old_lyric)
                    # logging.debug(
                    #     f"Removed old lyric from staff {staff_id}, measure {measure_index}, voice {voice_index}"
                    # )
                # Add the new lyric
                new_lyric = etree.Element("Lyrics")
                syllabic = etree.Element("syllabic")
                syllabic.text = lyric["syllabic"]
                new_lyric.append(syllabic)
                text = etree.Element("text")
                text.text = lyric["text"]
                new_lyric.append(text)
                no = etree.Element("no")
                no.text = lyric["no"]
                new_lyric.append(no)
                element.append(new_lyric)


def split_part(part):
    """
    Create a new Part element based on the original part
    """
    new_part = deepcopy(part)
    # Modify the new_part as needed
    for from_staff, to_staff in STAFF_MAPPING.items():
        # Update the staff ID in the new part
        for staff in new_part.findall(".//Staff"):
            if int(staff.get("id")) == from_staff:
                staff.set("id", str(to_staff))
    return new_part


def get_rest_length(rest, tick_diff):
    """
    Get the length of a Rest element in ticks.
    """
    duration_type = rest.find(".//durationType")
    dots = rest.find(".//dots")
    if duration_type is not None:
        return (
            resolve_duration(
                duration_type.text, dots=dots.text if dots is not None else "0"
            )
            - tick_diff
        )
    return 0


def shorten_rest_to(rest, new_duration_in_ticks):
    """
    Shorten a Rest element to a new duration in ticks.
    """
    BASE_NOTE_VALUES = {
        "whole": [
            1.0,
            1.5,
            1.75,
            1.875,
        ],  # whole, dotted whole, double dotted whole, triple dotted whole
        "half": [
            0.5,
            0.75,
            0.875,
            0.9375,
        ],  # half, dotted half, double dotted half, triple dotted half
        "quarter": [0.25, 0.375, 0.4375, 0.46875],  # quarter, dotted quarter, etc.
        "eighth": [0.125, 0.1875, 0.21875, 0.234375],
        "16th": [0.0625, 0.09375, 0.109375, 0.1171875],
        "32nd": [0.03125, 0.046875, 0.0546875, 0.05859375],
        "64th": [0.015625, 0.0234375, 0.02734375, 0.029296875],
        # Add more if needed
    }

    duration_type = rest.find(".//durationType")
    if duration_type is not None:
        # Convert the new duration to a fraction
        if new_duration_in_ticks == 0:
            # If the new duration is 0, remove the rest
            parent = rest.getparent()
            if parent is not None:
                parent.remove(rest)
        else:
            # Find whick value in the map multiplied by RESOLUTION
            # is the correct value
            for note_type, values in BASE_NOTE_VALUES.items():
                for i, value in enumerate(values):
                    if int(value * RESOLUTION) == new_duration_in_ticks:
                        # Found the correct value
                        duration_type.text = note_type
                        # If there are dots, we need to adjust them
                        if i > 0:
                            dots = rest.find(".//dots")
                            if dots is None:
                                dots = etree.Element("dots")
                                rest.append(dots)
                            # Set the number of dots based on the index
                            dots.text = str(i)
            logging.debug(
                f"Shortened rest to {duration_type.text} in element {rest.tag}"
            )


def preprocess_corrupted_measures(root):
    """
    Try to find measures with len="17/16" or similar
    and try to fix them.
    """
    problem_measures = defaultdict(list)
    for staff in root.findall(".//Score/Staff"):
        staff_id = int(staff.get("id"))
        measure_index = -1
        time_sig = None
        for measure in staff.findall(".//Measure"):
            new_time_sig = measure.find(".//TimeSig")
            if new_time_sig is not None:
                time_sig = f"{new_time_sig.find('.//sigN').text}/{new_time_sig.find('.//sigD').text}"
            measure_index += 1
            voice_index = -1
            problem_measure = measure.get("len") is not None and "/" in measure.get(
                "len"
            )
            if problem_measure:

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
                voice_index += 1
                time_pos = 0
                if problem_measure:
                    if (
                        voice_index
                        not in problem_measures[measure_index][-1]["elements"]
                    ):
                        problem_measures[measure_index][-1]["elements"][voice_index] = {
                            "elements": {},
                            "max_time_pos": 0,
                        }
                for el in voice:
                    if problem_measure:
                        problem_measures[measure_index][-1]["elements"][voice_index][
                            "elements"
                        ][time_pos] = el
                    if el.tag in ["Chord", "Rest"]:
                        duration_type = el.find(".//durationType")
                        dots = el.find(".//dots")
                        time_pos += resolve_duration(
                            duration_type.text if duration_type is not None else "0",
                            dots.text if dots is not None else "0",
                        )
                    if el.tag == "location":
                        fractions = el.find(".//fractions")
                        if fractions is not None:
                            time_pos += resolve_duration(
                                fractions.text if fractions is not None else "0"
                            )

                    if problem_measure:
                        problem_measures[measure_index][-1]["elements"][voice_index][
                            "max_time_pos"
                        ] = max(
                            problem_measures[measure_index][-1]["elements"][
                                voice_index
                            ]["max_time_pos"],
                            time_pos,
                        )

                if problem_measure:
                    problem_measures[measure_index][-1]["elements"][voice_index][
                        "elements"
                    ][time_pos] = None

    # For each corrupted measure, try to fix it by adjusting the final rest in each voice
    # If all voices don't have a final rest, we can't fix it
    for measure_index, staff_list in problem_measures.items():
        possible_to_fix = True
        max_time_pos = 0
        for staff_values in staff_list:
            for voice_index, voice_values in staff_values["elements"].items():
                max_time_pos = max(max_time_pos, voice_values["max_time_pos"])
        for staff_values in staff_list:
            for voice_index, voice_values in staff_values["elements"].items():
                if voice_values["max_time_pos"] < max_time_pos:
                    # Ignore this voice, it is not complete any way
                    continue
                # Check last element
                last_element = list(voice_values["elements"].values())[-2]
                if last_element.tag != "Rest":
                    possible_to_fix = False
                    break

        logging.debug(
            f"Measure {measure_index} is {'possible' if possible_to_fix else 'not possible'} to fix"
        )
        if possible_to_fix:
            time_sig = staff_list[0]["time_sig"]
            correct_measeure_len = 0
            if "/" in time_sig:
                sig_n, sig_d = map(int, time_sig.split("/"))
                correct_measeure_len = RESOLUTION * (sig_n / sig_d)
            else:
                correct_measeure_len = int(time_sig) * RESOLUTION

            to_remove = max_time_pos - correct_measeure_len
            logging.debug(
                f"Correct measure length for measure {measure_index} is {correct_measeure_len}. Must remove {to_remove} ticks."
            )
            cant_fix = False
            to_remove = []
            to_shorten = []
            for staff_values in staff_list:
                if cant_fix:
                    break
                for voice_index, voice_values in staff_values["elements"].items():
                    prev_el = None
                    prev_prev_el = None
                    remove_rest_of_elements = False

                    for time_pos, element in list(voice_values["elements"].items()):
                        # logging.debug(
                        #     f"Processing element at time position {time_pos} in staff {staff_values['staff_id']}, measure {measure_index}, voice {voice_index}"
                        # )
                        element_tag = element.tag if element is not None else None
                        if remove_rest_of_elements:
                            if element_tag == "Chord":
                                cant_fix = True
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} voice {voice_index} has a chord after prev deleted, cannot fix."
                                )
                                logging.debug(
                                    f"element xml: {etree.tostring(element, pretty_print=True).decode('utf-8')}"
                                )
                                break
                            # We have started removing elements, so we will remove all after it
                            to_remove.append(element)
                            continue

                        if element is not None and time_pos == correct_measeure_len:
                            # Nice, there is a rest at the end of the measure.
                            # Just remove this element and all after it.
                            if element_tag == "Chord":
                                cant_fix = True
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has a chord at the end, cannot fix."
                                )
                                break
                            to_remove.append(element)
                            remove_rest_of_elements = True
                            continue

                        if time_pos >= correct_measeure_len:
                            # We have passed the correct measure length
                            # We need to shorten the previous rest and remove all after it
                            if prev_el is None:
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has no previous element to shorten."
                                )
                                cant_fix = True
                                break
                            if prev_el.tag == "Chord":
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has no previous rest to shorten."
                                )
                                cant_fix = True
                                break
                            # Shorten the previous rest
                            if correct_measeure_len - time_pos <= 0:
                                # Can't shorten it enough, we need to remove it
                                to_remove.append(prev_el)
                                if prev_prev_el is not None:
                                    if prev_prev_el.tag != "Rest":
                                        logging.warning(
                                            f"Measure {measure_index} in staff {staff_values['staff_id']} has no prev previous rest to shorten."
                                        )
                                        cant_fix = True
                                        break
                                    # If there is a previous element, we can shorten it
                                    # By a delta...
                                    logging.debug(
                                        f"Shortening prev_prev rest in time_pos {time_pos} in staff {staff_values['staff_id']}, measure {measure_index}, voice {voice_index} to 0 ticks"
                                    )
                                    to_shorten.append(
                                        (
                                            prev_prev_el,
                                            get_rest_length(
                                                prev_prev_el,
                                                correct_measeure_len - time_pos,
                                            ),
                                        )
                                    )
                            else:
                                logging.debug(
                                    f"Shortening rest in time_pos {time_pos} in staff {staff_values['staff_id']}, measure {measure_index}, voice {voice_index} to {correct_measeure_len - time_pos} ticks"
                                )
                                to_shorten.append(
                                    (prev_el, correct_measeure_len - time_pos)
                                )
                            if element_tag == "Chord":
                                cant_fix = True
                                logging.warning(
                                    f"Measure {measure_index} in staff {staff_values['staff_id']} has a chord after the rest, cannot fix."
                                )
                                break
                            to_remove.append(element)
                            remove_rest_of_elements = True
                            continue

                        prev_prev_el = prev_el
                        prev_el = element

                    if cant_fix:
                        logging.warning(
                            f"Measure {measure_index} in staff {staff_values['staff_id']} cannot be fixed."
                        )
                        break

            if cant_fix:
                continue

            if to_shorten:
                for el, new_duration in to_shorten:
                    logging.debug(
                        f"Shortening rest {el.tag} in, measure {measure_index} to {new_duration} ticks"
                    )
                    shorten_rest_to(el, new_duration)
            if to_remove:
                logging.debug(
                    f"Removing elements {to_remove} from, measure {measure_index}"
                )
                for element in to_remove:
                    if element is not None:
                        parent = element.getparent()
                        if parent is not None:
                            parent.remove(element)

                # remove len attribute from the measure
                for staff_values in staff_list:
                    measure = staff_values["measure"]
                    if measure is not None:
                        measure.attrib.pop("len", None)
                        logging.debug(
                            f"Removed len attribute from measure {measure_index} in staff {staff_values['staff_id']}"
                        )


def main(input_path, output_path):
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
        input_content = f.readlines()

    # Parse the input XML
    root = etree.fromstringlist(input_content)

    # Perform the conversion
    staffs = root.findall(".//Staff")
    if not staffs:
        raise ValueError("No Staff elements found in the input XML.")

    preprocess_corrupted_measures(root)
    # Convert staff ids to make space after each staff
    # id="1" becomes id="1" and
    # id="2" becomes id="3"
    # so 2n - 1
    # ... unless the staff only has one voice, then we don't even split it.

    staffs_to_split = set()
    for staff in staffs:
        staff_id = int(staff.get("id"))
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
    new_staff_id = 1
    new_staffs_to_split = set()
    for staff in staffs:
        staff_id = int(staff.get("id"))
        if staff_id == 1:
            # Reset the new_staff_id to 1 for the first staff
            # since there are two lists of staffs in the xml
            new_staff_id = 1

        staff.set("id", str(new_staff_id))
        logging.debug(f"Updated staff id from {staff_id} to {new_staff_id}")
        if staff_id not in staffs_to_split:
            # If the staff does not need to be split, we can let the next id be next to it
            new_staff_id += 1
        else:
            new_staffs_to_split.add(new_staff_id)
            new_staff_id += 2

    for staff in staffs:
        staff_id = int(staff.get("id"))
        if staff_id in new_staffs_to_split:
            STAFF_MAPPING[staff_id] = int(str(staff_id + 1))

    logging.debug("Staff mapping: %s", STAFF_MAPPING)

    # Find the Part elements
    parts = root.findall(".//Part")
    if not parts:
        raise ValueError("No Part elements found in the input XML.")

    for part in parts:
        staff = part.find(".//Staff")
        if staff is None:
            raise ValueError("No Staff element found in the Part element.")
        staff_id = int(staff.get("id"))
        if staff_id not in STAFF_MAPPING:
            continue
        # Split the part into two separate parts
        new_part = split_part(part)
        parent = part.getparent()
        parent.insert(parent.index(part) + 1, new_part)

    for staff_id, new_staff_id in STAFF_MAPPING.items():
        # Find <Staff> element with staff_id
        # Which is a direct child of <Score>
        staff_element = root.find(f".//Score/Staff[@id='{staff_id}']")
        find_reversed_voices_by_staff_measure(staff_element)
        # Read lyrics from the staff
        read_lyrics(staff_element)
        new_staff_element = deepcopy(staff_element)
        new_staff_element.set("id", str(new_staff_id))
        # Insert the new Staff element into the Score next to the original
        score_element = root.find(".//Score")
        score_element.insert(score_element.index(staff_element) + 1, new_staff_element)

    for staff_id, new_staff_id in STAFF_MAPPING.items():
        up_staff_element = root.find(f".//Score/Staff[@id='{staff_id}']")
        handle_staff(up_staff_element, "up")
        down_staff_element = root.find(f".//Score/Staff[@id='{new_staff_id}']")
        handle_staff(down_staff_element, "down")

    # Handle rest of staffs to remove extra elements
    for staff in root.findall(".//Score/Staff"):
        staff_id = int(staff.get("id"))
        if staff_id in STAFF_MAPPING:
            # This staff is already handled
            continue
        if staff_id in set(STAFF_MAPPING.values()):
            # This staff is a new staff created by the split
            continue
        # Handle the staff
        handle_staff(staff, None)

    # Serialize the output XML
    output_content = etree.tostring(root, pretty_print=True, encoding="UTF-8").decode(
        "UTF-8"
    )

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
    main(args.input, args.output)
    logging.info("Conversion completed successfully.")
    logging.info(f"Output written to {args.output}")
