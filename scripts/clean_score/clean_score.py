#!/usr/bin/env python3

import sys  # noqa
from lxml import etree
from copy import deepcopy
from collections import defaultdict

import argparse


def debug_print(xml_el):
    """
    Print the XML element in a human-readable format.
    """
    print(etree.tostring(xml_el, pretty_print=True, encoding='unicode'))


def note_to_string(note):
    """
    Convert a note element to a string representation.
    include duration
    """
    duration_el = note.find("duration")
    duration = duration_el.text if duration_el is not None else "0"
    pitch_el = note.find("pitch")
    pitch = f"{pitch_el.find('step').text}{pitch_el.find('octave').text}" if pitch_el is not None else note.tag
    if note.find("rest") is not None:
        pitch = "rest"
    stem = note.find("stem")
    repr = f"{pitch} ({duration})"
    if stem is not None:
        stem_direction = stem.text.strip()
        if stem_direction == "up":
            repr += " [up stem]"
        elif stem_direction == "down":
            repr += " [down stem]"
    voice = note.find("voice")
    if voice is not None:
        repr += f" [voice {voice.text}]"
    lyrics = note.findall("lyric")
    if lyrics:
        lyric_texts = [lyric.find("text").text for lyric in lyrics if lyric.find("text") is not None]
        repr += f" [lyrics: {', '.join(lyric_texts)}]"
    return repr


def get_stem_direction(note, reversed_voice=None):
    stem = note.find("stem")
    if stem is None and reversed_voice is not None:
        ret = "down" if reversed_voice else "up"
        if note.find("voice") is None:
            return ret
        if note.find("voice").text == "2":
            ret = "up" if reversed_voice else "down"
        return ret
    return stem.text.strip() if stem is not None else "up"

def is_middle_of_slur(note):
    notations = note.find("notations")
    if notations is not None:
        for slur in notations.findall("slur"):
            if slur.attrib.get("type") == "stop":
                return True
    return False

def convert_to_tenor_clef(attributes):
    clef = attributes.find("clef")
    if clef is not None:
        sign = clef.find("sign")
        if sign is not None and sign.text == "G":
            clef_octave = clef.find("clef-octave-change")
            if clef_octave is None:
                clef_octave = etree.SubElement(clef, "clef-octave-change")
            clef_octave.text = "-1"


def copy_and_make_voice1(note):
    new_note = deepcopy(note)
    voice = new_note.find("voice")
    if voice is not None:
        voice.text = "1"
    else:
        voice = etree.SubElement(new_note, "voice")
        voice.text = "1"
    return new_note


def remove_other_voice(this_direction, measure, voices_reversed):
    print("")
    print("Starting to remove other voice in measure:", measure.attrib.get("number"), "for direction:", this_direction)

    for el in measure:
        if el.tag not in ("note", "forward", "backup"):
            continue
        print("Looking at element:", note_to_string(el), "this direction:", this_direction)
        if el.tag == "note":
            direction = get_stem_direction(el, voices_reversed)
            if direction != this_direction:
                if el.find("rest") is not None:
                    continue
                # Replace note with a forward
                print("Removing note with wrong direction:", direction, "expected:", this_direction)

                forward_el = etree.Element("forward")
                duration_el = el.find("duration")
                if duration_el is not None:
                    forward_el.append(deepcopy(duration_el))
                measure.replace(el, forward_el)

    print("")
    # Another loop, to check which rests to remove if any
    time_pos = 0
    notes_by_time_pos = defaultdict(list)
    for el in measure:
        if el.tag not in ("note", "forward", "backup"):
            continue
        if el.tag == "note":
            notes_by_time_pos[time_pos].append(el)
        duration_el = el.find("duration")
        duration = int(duration_el.text) if duration_el is not None else 0
        if el.tag == "note":
            time_pos += duration
        elif el.tag == "backup":
            time_pos -= duration
        elif el.tag == "forward":
            time_pos += duration

    max_time_pos = time_pos

    sorted_times = sorted(notes_by_time_pos.keys())
    for index in range(len(sorted_times)):
        time = sorted_times[index]
        if index + 1 >= len(sorted_times):
            next_time = max_time_pos
        else:
            next_time = sorted_times[index + 1]
        time_between = next_time - time
        print(f"Checking time position {time} with next time {next_time}, time between: {time_between}")
        print("Notes at this time position:", len(notes_by_time_pos[time]), [note_to_string(note) for note in notes_by_time_pos[time]])
        for note in notes_by_time_pos[time]:
            duration_el = note.find("duration")
            if duration_el is not None:
                duration = int(duration_el.text)
                if duration != time_between:
                    # This note is longer than the time between, so it should be converted
                    # to a forward
                    print("Note longer than time between, converting to forward:", note_to_string(note))
                    forward_el = etree.Element("forward")
                    forward_el.append(deepcopy(duration_el))
                    measure.replace(note, forward_el)



def main(root):
    # Only handle mscx: <Score>/<Part>/<Staff>/<Measure>/<voice>
    score = root.find("Score")
    if score is None:
        raise ValueError("No <Score> element found in mscx root!")

    # Find all <Part> elements (case-insensitive for mscx)
    parts = score.findall("Part")
    if not parts:
        parts = score.findall("part")
    # Always split the first part if only one exists
    if len(parts) == 1:
        parts_with_multiple_voices = [parts[0]]
    else:
        parts_with_multiple_voices = []
        for part in parts:
            for staff in part.findall("Staff") or part.findall("staff"):
                for measure in staff.findall("Measure") or staff.findall("measure"):
                    voice_count = len(measure.findall("voice"))
                    if voice_count > 1:
                        parts_with_multiple_voices.append(part)
                        break

    split_map = {}
    for part in parts_with_multiple_voices:
        pid = part.attrib.get("id", "P1")
        split_map[pid] = {"up": f"{pid}_up", "down": f"{pid}_down"}
        # Create new <Part> elements
        up_part = deepcopy(part)
        down_part = deepcopy(part)
        up_part.attrib["id"] = split_map[pid]["up"]
        down_part.attrib["id"] = split_map[pid]["down"]
        # Remove all measures from both, will fill below
        for staff in up_part.findall("Staff"):
            staff[:] = []
        for staff in down_part.findall("Staff"):
            staff[:] = []
        score.append(up_part)
        score.append(down_part)
        # Remove the original part immediately after appending split parts
        score.remove(part)

    # Remove all original parts that were split (by id, to avoid object identity issues)
    # (This is now redundant, since we remove above, but keep for safety)
    for orig_id in list(split_map.keys()):
        for part in list(score.findall('Part')):
            if part.attrib.get('id') == orig_id:
                score.remove(part)

    # Debug: print resulting XML for inspection
    print(etree.tostring(root, pretty_print=True, encoding='unicode'))

    # ---
    ## Remove Original Parts from Part-List
    # ---
    part_list = root.find("part-list")
    if part_list is not None:
        for part in part_list.findall("score-part"):
            pid = part.attrib.get("id")
            if pid in split_map:
                part_list.remove(part)

    # Delete original parts P1 and P2
    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid in split_map:
            root.remove(part)

    # Build a map of all Staff elements in the Score by id
    staff_map = {}
    for staff in score.findall('Staff'):
        staff_id = staff.attrib.get('id')
        if staff_id:
            staff_map[staff_id] = staff
    for part in parts_with_multiple_voices:
        pid = part.attrib.get("id", "P1")
        up_part = score.find(f"Part[@id='{split_map[pid]['up']}']") or score.find(f"part[@id='{split_map[pid]['up']}']")
        down_part = score.find(f"Part[@id='{split_map[pid]['down']}']") or score.find(f"part[@id='{split_map[pid]['down']}']")
        # Remove all <Staff> from split parts
        for s in list(up_part):
            if s.tag == 'Staff':
                up_part.remove(s)
        for s in list(down_part):
            if s.tag == 'Staff':
                down_part.remove(s)
        # For each staff id in the original part, find the corresponding Staff in the Score
        # Instead of iterating part.findall('Staff'), always use the staff_map (from <Score>)
        for staff_id, orig_staff in staff_map.items():
            # Create new Staff for up and down, copy non-Measure children
            up_staff = etree.Element('Staff', id=staff_id)
            down_staff = etree.Element('Staff', id=staff_id)
            for child in orig_staff:
                if child.tag != 'Measure':
                    up_staff.append(deepcopy(child))
                    down_staff.append(deepcopy(child))
            for idx_m, measure in enumerate(orig_staff.findall('Measure')):
                m_num = measure.attrib.get('number')
                if m_num is None:
                    m_num = str(idx_m + 1)
                up_measure = etree.Element('Measure', number=m_num)
                down_measure = etree.Element('Measure', number=m_num)
                voices = measure.findall('voice')
                if len(voices) == 2:
                    for idx_v, voice in enumerate(voices):
                        target_measure = up_measure if idx_v == 0 else down_measure
                        for el in voice:
                            if el.tag == 'Chord' or el.tag == 'Rest':
                                target_measure.append(deepcopy(el))
                elif len(voices) == 1:
                    for el in voices[0]:
                        if el.tag == 'Chord' or el.tag == 'Rest':
                            up_measure.append(deepcopy(el))
                            down_measure.append(deepcopy(el))
                up_staff.append(up_measure)
                down_staff.append(down_measure)
            up_part.append(up_staff)
            down_part.append(down_staff)
    # ...existing code...
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split MusicXML score into tenor parts.")

    parser.add_argument(
        "input_file", type=str, default="test.musicxml",
        help="Input MusicXML file to process."
    )

    args = parser.parse_args()

    INPUT_FILE = args.input_file

    if '.musicxml' in INPUT_FILE:
        OUTPUT_FILE = INPUT_FILE.replace(".musicxml", "_split.musicxml")
    elif '.xml' in INPUT_FILE:
        OUTPUT_FILE = INPUT_FILE.replace(".xml", "_split.xml")
    else:
        raise ValueError("Input file must be a .musicxml or .xml file.")

    # Load MusicXML file
    tree = etree.parse(INPUT_FILE)
    root = tree.getroot()

    # Side effect
    main(root)

    with open(OUTPUT_FILE, "wb") as f:
        tree.write(f, pretty_print=True, xml_declaration=True, encoding="UTF-8")

    print(f"Written transformed MusicXML to {OUTPUT_FILE}")
