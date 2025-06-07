#!/usr/bin/env python3

import sys  # noqa
from lxml import etree
from copy import deepcopy
from collections import defaultdict

import argparse


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


def main(root):

    # Add new score-part entries for T1, T2, B1, B2
    new_parts = [
        ("T1", "P1_stem_up"),
        ("T2", "P1_stem_down"),
        ("B1", "P2_stem_up"),
        ("B2", "P2_stem_down"),
    ]

    part_list = root.find(".//part-list")
    for name, part_id in new_parts:
        score_part = etree.Element("score-part", id=part_id)
        part_name = etree.SubElement(score_part, "part-name")
        part_name.text = name
        part_list.append(score_part)
        part_elem = etree.Element("part", id=part_id)
        root.append(part_elem)

    split_map = {
        "P1": {"up": "P1_stem_up", "down": "P1_stem_down"},
        "P2": {"up": "P2_stem_up", "down": "P2_stem_down"},
    }

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

                    print(f"Part {pid}, Measure {measure.attrib.get('number')}: direction={direction}, voice={voice}")
                    reversed_voices_by_part_measure[pid][measure.attrib.get('number')] = voice != direction

    print("Reversed voices by part and measure:", reversed_voices_by_part_measure)
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
                            if pid == "P1" and placement == "below":
                                lyric_pid = "B1"
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
            if time_position > 700:
                break
            m_num = measure.attrib.get("number")
            m_by_time = defaultdict(list)
            voices_reversed = reversed_voices_by_part_measure[pid].get(m_num, False)

            m_up = etree.Element("measure", number=m_num)
            m_down = etree.Element("measure", number=m_num)
            for el in measure:
                if el.tag == "attributes":
                    attr_up = deepcopy(el)
                    attr_down = deepcopy(el)
                    convert_to_tenor_clef(attr_up)
                    convert_to_tenor_clef(attr_down)
                    m_up.append(attr_up)
                    m_down.append(attr_down)
                elif el.tag in ("backup", "forward"):
                    dur = int(el.find("duration").text)
                    time_position += dur if el.tag == "forward" else -dur
                    m_up.append(deepcopy(el))
                    m_down.append(deepcopy(el))
                elif el.tag == "note":
                    new_note = deepcopy(el)
                    m_by_time[time_position].append(copy_and_make_voice1(new_note))

                    duration_el = el.find("duration")
                    if duration_el is not None:
                        time_position += int(duration_el.text)

            for time_pos in sorted(m_by_time.keys()):
                for note in m_by_time[time_pos]:
                    direction = get_stem_direction(el, reversed_voice=voices_reversed)
                    is_rest = note.find("rest") is not None
                    for sub in list(new_note):
                        if sub.tag == "lyric":
                            new_note.remove(sub)

                    if not is_rest and not is_middle_of_slur(note):
                        lyrics_for_time = lyrics_by_time.get(time_position, {})
                        choices = [
                            split_map[pid][direction],
                            "B1" if pid == "P2" and direction == "up" else "B2" if pid == "P2" else "T1" if direction == "up" else "T2",
                            pid,
                            "P1" if pid.startswith("T") else "P2",
                            "P1",
                            "P2"
                        ]
                        for choice in choices:
                            lyric = lyrics_for_time.get(choice)
                            if lyric is not None:
                                break
                        if lyric is not None:
                            new_note.append(deepcopy(lyric))

                    note_type = note.find("type").text
                    note_info = f"{'rest' if note.find('rest') is not None else 'note'} ({note_type})"
                    print(f"{time_pos}: {note_info}")
                    if direction == "up":
                        m_up.append(new_note)
                    else:
                        m_down.append(new_note)

            new_measures[split_map[pid]["up"]].append(m_up)
            new_measures[split_map[pid]["down"]].append(m_down)

    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid in new_measures:
            part[:] = new_measures[pid]

    # ---
    ## Remove Original Parts from Part-List
    # ---
    for score_part in part_list.findall("score-part"):
        if score_part.attrib.get("id") in ["P1", "P2"]:
            part_list.remove(score_part)

    # Delete original parts P1 and P2
    for part in root.findall("part"):
        pid = part.attrib.get("id")
        if pid in ["P1", "P2"]:
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
