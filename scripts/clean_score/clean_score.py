from collections import defaultdict
from copy import deepcopy
from lxml import etree

import logging

logging.basicConfig(level=logging.DEBUG)


STAFF_MAPPING = {}
REVERSED_VOICES_BY_STAFF_MEASURE = {}
LYRICS_BY_TIMEPOS = {}
RESOLUTION = 128  # Default resolution for durations in MuseScore XML


def resolve_duration(fraction_or_duration):
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
        return duration_map.get(fraction_or_duration.lower(), 0)


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
        # If staff_id is parent staff and this is child staff, and lyric "no" is 1, use that
        if staff_id is not None:
            original_staff_id = get_original_staff_id(staff_id)
            if staff_id != original_staff_id:
                for lyric in lyric_choices:
                    if (
                        lyric["staff_id"] == original_staff_id
                        and lyric["lyric"]["no"] == "1"
                    ):
                        # Force "no" to be empty
                        lyric["lyric"]["no"] = ""
                        return lyric["lyric"]

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
    staff_id = staff.get("id")
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
                    time_pos += resolve_duration(
                        duration_type.text if duration_type is not None else "0"
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
    staff_id = staff.get("id")
    global LYRICS_BY_TIMEPOS, REVERSED_VOICES_BY_STAFF_MEASURE
    for el in loop_staff(staff):
        staff_id = el["staff_id"]
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
                logging.debug(
                    f"Found lyric in staff {staff_id}, measure {measure_index}, voice {voice_index}, time position {time_pos}: {lyric_to_dict(lyric)}"
                )
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

    import json

    print(
        f"Read lyrics for staff {staff_id}: {json.dumps(LYRICS_BY_TIMEPOS, indent=2)}"
    )


def find_reversed_voices_by_staff_measure(staff):
    """
    Find reversed voices for a given staff ID.
    This function should return a list of reversed voices for the specified staff.
    """
    REVERSED_VOICES_BY_STAFF_MEASURE[staff.get("id")] = {}
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
                    REVERSED_VOICES_BY_STAFF_MEASURE[staff.get("id")][index] = True


def get_original_staff_id(staff_id):
    original_staff_id = staff_id
    for parent_staff_id, child_staff_id in STAFF_MAPPING.items():
        if child_staff_id == staff_id:
            original_staff_id = parent_staff_id
            logging.debug(
                f"Found original staff ID {original_staff_id} for staff {staff_id}"
            )
            break
    return original_staff_id


def handle_staff(staff, direction):
    """
    Delete notes not matching the specified direction
    """
    staff_id = staff.get("id")
    original_staff_id = get_original_staff_id(staff_id)

    logging.debug(f"Handling staff {staff_id} for direction {direction}")
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
                voice.insert(0, timesig)
            if keysig is not None:
                voice.insert(0, keysig)
            if voice_index == voice_to_remove:
                # Remove the voice that does not match the direction
                measure.remove(voice)

    # Finally, set StemDiretion up for all Chords in the staff
    for chord in staff.findall(".//Chord"):
        stem_direction = chord.find(".//StemDirection")
        if stem_direction is not None:
            stem_direction.text = "up"

    # Delete all <offset> elements in the staff
    for offset in staff.findall(".//offset"):
        parent = offset.getparent()
        if parent is not None:
            parent.remove(offset)

    # Try to find a lyric for each Chord in the staff
    for el in loop_staff(staff):
        staff_id = el["staff_id"]
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
            logging.debug(
                f"Found lyric for staff {staff_id}, measure {measure_index}, voice {voice_index}, time position {time_pos}: {lyric}"
            )
            if lyric:
                # Delete old lyrics
                for old_lyric in element.findall(".//Lyrics"):
                    element.remove(old_lyric)
                    logging.debug(
                        f"Removed old lyric from staff {staff_id}, measure {measure_index}, voice {voice_index}"
                    )
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
            if staff.get("id") == from_staff:
                staff.set("id", to_staff)
    return new_part


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

    for staff in staffs:
        staff_id = staff.get("id")
        STAFF_MAPPING[staff_id] = {}

    mapping_index_start = len(list(STAFF_MAPPING.keys())) + 1
    for i, staff_id in enumerate(STAFF_MAPPING.keys(), start=mapping_index_start):
        STAFF_MAPPING[staff_id] = str(i)

    logging.debug("Staff mapping: %s", STAFF_MAPPING)

    # Find the Part elements
    parts = root.findall(".//Part")
    if not parts:
        raise ValueError("No Part elements found in the input XML.")

    for part in parts:
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
        new_staff_element.set("id", new_staff_id)
        # Insert the new Staff element into the Score next to the original
        score_element = root.find(".//Score")
        score_element.insert(score_element.index(staff_element) + 1, new_staff_element)

    for staff_id, new_staff_id in STAFF_MAPPING.items():
        up_staff_element = root.find(f".//Score/Staff[@id='{staff_id}']")
        handle_staff(up_staff_element, "up")
        down_staff_element = root.find(f".//Score/Staff[@id='{new_staff_id}']")
        handle_staff(down_staff_element, "down")

    # Serialize the output XML
    output_content = etree.tostring(root, pretty_print=True, encoding="UTF-8").decode(
        "UTF-8"
    )

    # Write the output XML to the specified file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output_content)
