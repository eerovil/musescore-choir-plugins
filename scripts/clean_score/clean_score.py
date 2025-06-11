from copy import deepcopy
from lxml import etree

import logging

logging.basicConfig(level=logging.DEBUG)


STAFF_MAPPING = {}
REVERSED_VOICES_BY_STAFF_MEASURE = {}


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
                    logging.debug(
                        f"Reversed voice found in staff {staff.get('id')}, measure {index}, voice {voice_index}, direction {stem_direction}"
                    )
                    logging.debug(
                        f"Chord: {etree.tostring(chord, pretty_print=True).decode('utf-8')}"
                    )
                    REVERSED_VOICES_BY_STAFF_MEASURE[staff.get("id")][index] = True


def handle_staff(staff, direction):
    """
    Delete notes not matching the specified direction
    """
    staff_id = staff.get("id")
    logging.debug(f"Handling staff {staff_id} for direction {direction}")
    index = -1
    for measure in staff.findall(".//Measure"):
        index += 1
        reversed_voices = REVERSED_VOICES_BY_STAFF_MEASURE.get(staff_id, {}).get(
            index, False
        )
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
                timesig = voice.find(".//TimeSig")
                if timesig is None:
                    timesig = default_timesig()

                keysig = voice.find(".//KeySig")
                if keysig is None:
                    keysig = default_keysig()

            if timesig is not None:
                voice.insert(0, timesig)
                logging.debug(
                    f"Inserted TimeSig in staff {staff_id}, measure {index}, voice {voice_index}"
                )
            if keysig is not None:
                voice.insert(0, keysig)
                logging.debug(
                    f"Inserted KeySig in staff {staff_id}, measure {index}, voice {voice_index}"
                )
            if voice_index == voice_to_remove:
                # Remove the voice that does not match the direction
                measure.remove(voice)
                logging.debug(
                    f"Removed voice {voice_index} from staff {staff_id}, measure {index}"
                )
            else:
                logging.debug(
                    f"Keeping voice {voice_index} in staff {staff_id}, measure {index}"
                )

    # Finally, set StemDiretion up for all Chords in the staff
    for chord in staff.findall(".//Chord"):
        stem_direction = chord.find(".//StemDirection")
        if stem_direction is not None:
            stem_direction.text = "up"
            logging.debug(
                f"Set StemDirection to {stem_direction.text} for chord in staff {staff_id}"
            )

    # Delete all <offset> elements in the staff
    for offset in staff.findall(".//offset"):
        parent = offset.getparent()
        if parent is not None:
            parent.remove(offset)
            logging.debug(f"Removed <offset> element from staff {staff_id}")


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
