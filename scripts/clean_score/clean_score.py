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

    correct_dir = True
    for el in measure:
        if el.tag == "note":
            direction = get_stem_direction(el, voices_reversed)
            correct_dir = direction == this_direction

        if el.tag == "forward":
            # This will toggle correct dir also.
            correct_dir = not correct_dir

        if not correct_dir:
            # Remove the note
            print(f"Removing note in measure {measure.attrib.get('number')} with direction {direction} (expected {this_direction})")
            measure.remove(el)
        elif el.tag == "forward":
            # This must be a rest now
            duration_el = el.find("duration")
            if duration_el is None:
                raise ValueError("Forward element without duration in measure")
            duration = int(duration_el.text)
            # Create a rest note with the same duration
            rest_note = etree.Element("note")
            etree.SubElement(rest_note, "rest")
            duration_el = etree.SubElement(rest_note, "duration")
            duration_el.text = str(duration)
            voice_el = etree.SubElement(rest_note, "voice")
            voice_el.text = "1"
            # replace el with rest_note
            print(f"Replacing forward in measure {measure.attrib.get('number')} with rest note of duration {duration}")
            measure.replace(el, rest_note)

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

            print(debug_print(measure))

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
            print(debug_print(m_up))

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
