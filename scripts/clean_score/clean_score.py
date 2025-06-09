#!/usr/bin/env python3

import sys
import argparse
from music21 import converter, stream, note, chord, layout, duration, clef, instrument
from collections import defaultdict
from lxml import etree

# Helper for debugging (music21 objects have .show('xml') or .show('text'))
def debug_print(music21_obj):
    """
    Print the music21 object in a human-readable XML format.
    """
    music21_obj.show('xml')

def get_voice_number(music21_element):
    """
    Helper function to get the explicit voice number from a music21 note or rest.
    Returns None if no explicit voice is found.
    """
    if hasattr(music21_element, 'voice') and music21_element.voice is not None:
        return music21_element.voice
    return None # Return None if no explicit voice tag

def get_stem_direction_music21(note_obj):
    """
    Determines stem direction from a music21 note object.
    Returns 'up', 'down', or 'none' if not explicitly set and not inferred by music21.
    """
    if note_obj.stemDirection is not None:
        return note_obj.stemDirection.lower()
    return 'none' # Explicitly return 'none' if no stem direction found

def is_middle_of_slur_music21(note_obj):
    """
    Checks if a music21 note is in the middle of a slur.
    (i.e., has a slur that stops on it)
    """
    for spanner in note_obj.getSpanners():
        if isinstance(spanner, stream.Slur) and spanner.isEnded(note_obj):
            return True
    return False

def convert_to_tenor_clef_music21(measure_stream_or_attributes_stream):
    """
    Converts a G clef to a tenor clef (G clef with octave down)
    within a measure stream or an attributes stream.
    """
    clefs_to_replace = []
    for clef_obj in measure_stream_or_attributes_stream.getElementsByClass('Clef'):
        if isinstance(clef_obj, clef.TrebleClef) and clef_obj.octaveShift == 0: # Only target standard G clef
            clefs_to_replace.append(clef_obj)

    for old_clef in clefs_to_replace:
        tenor_clef = clef.TrebleClef()
        tenor_clef.octaveShift = -1
        measure_stream_or_attributes_stream.replace(old_clef, tenor_clef)


def determine_note_voice_heuristic(music21_note_or_rest, voices_reversed):
    """
    Determines the "voice" (up-stem or down-stem) for a note or rest,
    prioritizing stem direction for OCR output.

    Args:
        music21_note_or_rest: The music21.note.Note or music21.note.Rest object.
        voices_reversed: A boolean indicating if the 'up' and 'down' roles are reversed
                         for the stem direction heuristic in this measure.

    Returns:
        'up' or 'down' based on the heuristic.
    """
    # 1. Explicit voice tag (if reliable, often not for OCR)
    explicit_voice = get_voice_number(music21_note_or_rest)
    if explicit_voice == 1:
        return 'up' if not voices_reversed else 'down'
    elif explicit_voice == 2:
        return 'down' if not voices_reversed else 'up'

    # 2. Stem direction (primary heuristic for OCR)
    if isinstance(music21_note_or_rest, note.Note):
        stem_direction = get_stem_direction_music21(music21_note_or_rest)
        if stem_direction != 'none':
            return stem_direction if not voices_reversed else ('down' if stem_direction == 'up' else 'up')

        # If no stem direction, infer based on pitch relative to middle C (C4)
        # This is a common engraving rule for single-voice notes.
        if music21_note_or_rest.pitch:
            if music21_note_or_rest.pitch.midi >= 60:  # C4 is MIDI 60
                return 'down' if not voices_reversed else 'up' # Notes C4 and above usually down-stem
            else:
                return 'up' if not voices_reversed else 'down' # Notes below C4 usually up-stem

    # 3. For rests without explicit voice, or notes without stem/pitch info,
    #    make a best guess. Often rests belong to the 'down' voice in piano parts.
    #    For simplicity, if no info, assign to the 'down' voice.
    return 'down' if not voices_reversed else 'up'


def main(root):
    score = root.find('Score')
    # Write initial debug info
    with open('/Users/eerovilpponen/Documents/musescore-choir-plugins/scripts/clean_score/debug_score_children.txt', 'w') as dbg:
        dbg.write('DEBUG: <Score> children at start: ' + str([(el.tag, el.get('id')) for el in score]) + '\n')
    partlist = score.find('PartList')
    if partlist is None:
        partlist = etree.Element('PartList')
        # Insert PartList as the first child of Score
        score.insert(0, partlist)
    # Ensure all <Part> elements have an id BEFORE checking for parts
    part_counter = 1
    for el in score:
        if el.tag == 'Part' and el.get('id') is None:
            el.set('id', f'P{part_counter}')
            part_counter += 1
    parts = [el for el in score if el.tag == 'Part']
    if not parts or partlist is None:
        with open('/Users/eerovilpponen/Documents/musescore-choir-plugins/scripts/clean_score/debug_score_children.txt', 'a') as dbg:
            dbg.write(f'partlist is None: {[el.tag for el in score]}\n')
        return

    orig_part = parts[0]
    orig_id = orig_part.get('id')
    up_id = orig_id + '_up'
    down_id = orig_id + '_down'

    # --- NEW LOGIC FOR MSCX: Split Staffs, not Parts ---
    # Find all Staffs in Score (not in Part!)
    staffs = [el for el in score if el.tag == 'Staff']
    orig_staff = staffs[0]
    import copy
    up_staff = copy.deepcopy(orig_staff)
    down_staff = copy.deepcopy(orig_staff)
    up_staff.set('id', up_id)
    down_staff.set('id', down_id)

    # For each measure in each staff, keep only the correct <voice>
    for staff, keep_voice_idx in [(up_staff, 0), (down_staff, 1)]:
        for measure in staff.findall('Measure'):
            voices = measure.findall('voice')
            for idx, v in enumerate(voices):
                if idx != keep_voice_idx:
                    measure.remove(v)

    # Remove original staff and insert new staffs at the same index
    idx_staff = list(score).index(orig_staff)
    score.remove(orig_staff)
    score.insert(idx_staff, up_staff)
    score.insert(idx_staff + 1, down_staff)

    # Remove original part and insert new parts at the same index (as before)
    idx = list(score).index(orig_part)
    score.remove(orig_part)
    # Insert new parts with correct ids and names
    for pid, pname in [(up_id, 'Up'), (down_id, 'Down')]:
        new_part = copy.deepcopy(orig_part)
        new_part.set('id', pid)
        # Optionally set a name/trackName if needed
        track_name = new_part.find('trackName')
        if track_name is not None:
            track_name.text = pname
        score.insert(idx, new_part)
        idx += 1

    # Write debug info to a file
    with open('/Users/eerovilpponen/Documents/musescore-choir-plugins/scripts/clean_score/debug_score_children.txt', 'a') as dbg:
        dbg.write('DEBUG: <Score> children after split: ' + str([(el.tag, el.get('id')) for el in score]) + '\n')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split mscx score into separate voice parts using lxml only.")

    parser.add_argument(
        "input_file", type=str, default="test.mscx",
        help="Input mscx file to process."
    )

    args = parser.parse_args()

    INPUT_FILE = args.input_file

    if '.mscx' in INPUT_FILE:
        OUTPUT_FILE = INPUT_FILE.replace(".mscx", "_split.mscx")
    elif '.xml' in INPUT_FILE:
        OUTPUT_FILE = INPUT_FILE.replace(".xml", "_split.xml")
    else:
        raise ValueError("Input file must be a .mscx or .xml file.")

    # Load mscx file using lxml
    tree = etree.parse(INPUT_FILE)
    root = tree.getroot()
    main(root)
    tree.write(OUTPUT_FILE, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    print(f"Written transformed mscx to {OUTPUT_FILE}")