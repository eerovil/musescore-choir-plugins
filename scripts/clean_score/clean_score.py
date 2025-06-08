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

    # Find parts with multiple voices
    parts_with_multiple_voices = []
    for part in root.findall("part"):
        pid = part.attrib.get("id")
        for measure in part.findall("measure"):
            voices = set()
            for el in measure:
                if el.tag == "note":
                    voice_el = el.find("voice")
                    if voice_el is not None:
                        voices.add(voice_el.text)
                    else:
                        voices.add("1")
            if len(voices) > 1:
                parts_with_multiple_voices.append(pid)
                break

    # For each part in parts_with_multiple_voices, create a split map
    # create 2 new parts

    split_map = {}
    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid not in parts_with_multiple_voices:
            continue

        # Create a split map for this part
        split_map[pid] = {
            "up": f"{pid}_up",
            "down": f"{pid}_down"
        }

        # Create new parts for up and down voices
        up_part = etree.Element("part", id=split_map[pid]["up"])
        down_part = etree.Element("part", id=split_map[pid]["down"])

        # Add these new parts to the root
        root.append(up_part)
        root.append(down_part)

        # add to part list
        part_list = root.find("part-list")
        if part_list is None:
            part_list = etree.SubElement(root, "part-list")
        score_part_up = etree.SubElement(part_list, "score-part", id=split_map[pid]["up"])
        score_part_down = etree.SubElement(part_list, "score-part", id=split_map[pid]["down"])
        score_part_up.append(etree.Element("part-name", text=f"{pid} Up"))
        score_part_down.append(etree.Element("part-name", text=f"{pid} Down"))
        print("Splitting part", pid, "into", split_map[pid]["up"], "and", split_map[pid]["down"])

    # 0 PASS: Try to determine what voice is up or down using stem direction
    reversed_voices_by_part_measure = defaultdict(dict)

    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid not in split_map:
            continue
        for measure in part.findall("measure"):
            for el in measure:
                if el.tag == "note":
                    is_rest = el.find("rest") is not None
                    if is_rest:
                        continue
                    direction = get_stem_direction(el)
                    voice = "up"
                    voice_el = el.find("voice")
                    if voice_el is not None and voice_el.text == "2":
                        voice = "down"

                    if not direction:
                        print("Warning: No stem direction found for note in part", pid, "measure", measure.attrib.get("number"))
                        direction = voice

                    reversed_voices_by_part_measure[pid][measure.attrib.get('number')] = voice != direction
                    if voice != direction:
                        print(f"Detected reversed voice in part {pid}, measure {measure.attrib.get('number')}: expected {direction}, found {voice}")

    # FIRST PASS: store lyrics by (time position, lyric target part)
    lyrics_by_time = defaultdict(dict)

    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid not in split_map:
            continue
        time_position = 0
        for measure in part.findall("measure"):
            for el in measure:
                if el.tag == "note":
                    duration_el = el.find("duration")
                    duration = int(duration_el.text) if duration_el is not None else 0
                    direction = get_stem_direction(el)
                    for lyric in el.findall("lyric"):
                        verse = lyric.attrib.get("number", "1")
                        placement = lyric.attrib.get("placement", "below")
                        lyric_pid = pid
                        if verse == "2":
                            # Force verse 2 + below to be in the down part
                            if placement == "below":
                                lyric_pid = split_map[pid]["down"]
                        lyrics_by_time[time_position][lyric_pid] = deepcopy(lyric)
                        # Force verse 1
                        lyrics_by_time[time_position][lyric_pid].attrib["number"] = "1"
                    time_position += duration
                elif el.tag == "backup":
                    time_position -= int(el.find("duration").text)
                elif el.tag == "forward":
                    time_position += int(el.find("duration").text)

    # SECOND PASS: rewrite with lyric per time and direction fallback
    new_measures = defaultdict(list)

    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid not in split_map:
            continue

        time_position = 0
        for measure in part.findall("measure"):
            print(f"Processing measure {measure.attrib.get('number')} for part {pid} at time position {time_position}")

            m_num = measure.attrib.get("number")
            voices_reversed = reversed_voices_by_part_measure[pid].get(m_num, False)

            m_up = etree.Element("measure", number=m_num)
            m_down = etree.Element("measure", number=m_num)

            for el in measure:
                if el.tag == "attributes":
                    m_up.append(deepcopy(el))
                    m_down.append(deepcopy(el))
                elif el.tag in ("forward", "backup"):
                    m_up.append(deepcopy(el))
                    m_down.append(deepcopy(el))
                    dur = int(el.find("duration").text)
                    time_position += dur if el.tag == "forward" else -dur
                elif el.tag == "note":
                    is_rest = el.find("rest") is not None

                    if not is_middle_of_slur(el):
                        # Add lyrics if available
                        lyrics_for_time = lyrics_by_time.get(time_position, {})
                        lyric = lyrics_for_time.get(split_map[pid][direction])
                        if lyric is None:
                            # Try to find a fallback lyric
                            choices = [
                                split_map[pid]["up"],
                                split_map[pid]["down"],
                                pid,
                            ]
                            for choice in choices:
                                lyric = lyrics_for_time.get(choice)
                                if lyric is not None:
                                    break
                        if lyric is None:
                            # Try to find any lyric
                            lyric = next(iter(lyrics_for_time.values()), None)
                        if lyric is not None:
                            # Clear existing lyrics
                            for existing_lyric in el.findall("lyric"):
                                el.remove(existing_lyric)
                            el.append(deepcopy(lyric))

                    print(f"Processing note in part {pid}, measure {m_num}, time position {time_position}, is_rest: {is_rest}")
                    m_up.append(deepcopy(el))
                    m_down.append(deepcopy(el))
                    duration_el = el.find("duration")
                    duration = int(duration_el.text) if duration_el is not None else 0
                    time_position += duration

            remove_other_voice("up", m_up, voices_reversed)
            remove_other_voice("down", m_down, voices_reversed)

            print("measure up")
            new_measures[split_map[pid]["up"]].append(m_up)
            new_measures[split_map[pid]["down"]].append(m_down)

    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid in new_measures:
            part[:] = new_measures[pid]

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
