#!/usr/bin/env python3

import sys
import argparse
from music21 import converter, stream, note, chord, layout, duration, clef, instrument, metadata
from collections import defaultdict
import logging
import copy # Import the copy module

logger = logging.getLogger("clean_score")
logging.basicConfig(level=logging.DEBUG)

# Helper for debugging (music21 objects have .show('xml') or .show('text'))
def debug_print(music21_obj):
    logger.debug(f"debug_print: {music21_obj}")
    music21_obj.show('xml')

def get_voice_number(music21_element):
    logger.debug(f"get_voice_number: {music21_element}")
    # Try to get context Voice stream
    try:
        voice_ctx = music21_element.getContextByClass('Voice')
        if voice_ctx is not None:
            logger.debug(f"get_voice_number: found Voice context: {voice_ctx}, id={getattr(voice_ctx, 'id', None)}, number={getattr(voice_ctx, 'number', None)}")
            # Try id or number
            if hasattr(voice_ctx, 'number') and voice_ctx.number is not None:
                try:
                    return int(voice_ctx.number)
                except Exception:
                    return voice_ctx.number
            if hasattr(voice_ctx, 'id') and voice_ctx.id is not None:
                try:
                    return int(voice_ctx.id)
                except Exception:
                    return voice_ctx.id
    except Exception as e:
        logger.debug(f"get_voice_number: Exception in getContextByClass('Voice'): {e}")
    # Try attribute 'voice'
    if hasattr(music21_element, 'voice') and music21_element.voice is not None:
        try:
            return int(music21_element.voice)
        except Exception:
            return music21_element.voice
    # Try .voiceNumber
    if hasattr(music21_element, 'voiceNumber') and music21_element.voiceNumber is not None:
        try:
            return int(music21_element.voiceNumber)
        except Exception:
            return music21_element.voiceNumber
    # Try .groups (sometimes used for context in music21)
    if hasattr(music21_element, 'groups') and music21_element.groups:
        for g in music21_element.groups:
            if hasattr(g, 'id') and g.id is not None:
                try:
                    return int(g.id)
                except Exception:
                    return g.id
    return None # Return None if no explicit voice tag

def get_stem_direction_music21(note_obj):
    logger.debug(f"get_stem_direction_music21: {note_obj}")
    """
    Determines stem direction from a music21 note object.
    Returns 'up', 'down', or 'none' if not explicitly set and not inferred by music21.
    """
    if note_obj.stemDirection is not None:
        return note_obj.stemDirection.lower()
    return 'none' # Explicitly return 'none' if no stem direction found

def is_middle_of_slur_music21(note_obj):
    logger.debug(f"is_middle_of_slur_music21: {note_obj}")
    """
    Checks if a music21 note is in the middle of a slur.
    (i.e., has a slur that stops on it)
    """
    for spanner in note_obj.getSpanners():
        if isinstance(spanner, stream.Slur) and spanner.isEnded(note_obj):
            return True
    return False

def convert_to_tenor_clef_music21(measure_stream_or_attributes_stream):
    logger.debug(f"convert_to_tenor_clef_music21: {measure_stream_or_attributes_stream}")
    """
    Converts a G clef to a tenor clef (G clef with octave down)
    within a measure stream or an attributes stream.
    """
    clefs_to_replace = []
    for clef_obj in measure_stream_or_attributes_stream.getElementsByClass('Clef'):
        if isinstance(clef_obj, clef.TrebleClef) and clef_obj.octaveShift == 0: # Only target standard G clef
            clefs_to_replace.append(clef_obj)

    for old_clef in clefs_to_replace:
        # FIX: Corrected typo from TrebleCleblef to TrebleClef
        tenor_clef = clef.TrebleClef()
        tenor_clef.octaveShift = -1
        measure_stream_or_attributes_stream.replace(old_clef, tenor_clef)


def determine_note_voice_heuristic(music21_note_or_rest):
    logger.debug(f"determine_note_voice_heuristic: {music21_note_or_rest}")
    explicit_voice = get_voice_number(music21_note_or_rest)
    if explicit_voice == 1:
        return 'up'
    elif explicit_voice == 2:
        return 'down'
    # Only use fallback if not voice 1 or 2
    # 2. Stem direction (primary heuristic for OCR if no explicit voice)
    if isinstance(music21_note_or_rest, note.Note):
        stem_direction = get_stem_direction_music21(music21_note_or_rest)
        if stem_direction == 'up':
            return 'up'
        elif stem_direction == 'down':
            return 'down'
        if music21_note_or_rest.pitch:
            if music21_note_or_rest.pitch.midi >= 60:
                return 'up'
            else:
                return 'down'
    return 'down'


def main(score):
    logger.debug(f"main: score={score}")
    # Find parts with multiple voices (based on presence of any voice tags or multiple notes at same time)
    parts_to_split_ids = []
    for part in score.parts:
        logger.debug(f"Checking part: {part}, id={part.id}")
        has_multiple_voices = False
        for measure in part.getElementsByClass('Measure'):
            logger.debug(f"Checking measure: {measure}, number={measure.number}")
            # Check for multiple Voice streams in the measure
            voice_streams = list(measure.getElementsByClass('Voice'))
            if len(voice_streams) > 1:
                logger.debug(f"Detected multiple Voice streams in measure {measure.number}: {[v.id for v in voice_streams]}")
                has_multiple_voices = True
                break
            # Fallback: Check for multiple voice numbers as before
            offset_map = defaultdict(list)
            voices_in_measure = set()
            for element in measure.notesAndRests:
                logger.debug(f"Element in measure: {element}, offset={element.offset}, element.voice={getattr(element, 'voice', None)}")
                # Try to get .voiceNumber if present
                vnum = getattr(element, 'voiceNumber', None)
                if vnum is None:
                    vnum = get_voice_number(element)
                if vnum is not None:
                    voices_in_measure.add(vnum)
                offset_map[element.offset].append(element)
            logger.debug(f"voices_in_measure for measure {measure.number}: {voices_in_measure}")
            if len(voices_in_measure) > 1:
                has_multiple_voices = True
                break
            for offset, elements_at_offset in offset_map.items():
                if len(elements_at_offset) > 1:
                    has_multiple_voices = True
                    break
        if has_multiple_voices:
            parts_to_split_ids.append(part.id)
            print(f"Detected multiple voices in part: {part.id} (based on multiple Voice streams or voice numbers)")
            break

    logger.debug(f"parts_to_split_ids: {parts_to_split_ids}")
    # Prepare for splitting
    split_part_mappings = {} # Original_ID -> {'up': New_Up_ID, 'down': New_Down_ID}
    new_parts_to_add = []

    for part_id in parts_to_split_ids:
        logger.debug(f"Splitting part: {part_id}")
        original_part = score.getElementById(part_id)
        if original_part is None:
            continue

        up_part_id = f"{part_id}_up"
        down_part_id = f"{part_id}_down"
        split_part_mappings[part_id] = {"up": up_part_id, "down": down_part_id}

        # Create new parts for up and down voices
        up_part = stream.Part()
        up_part.id = up_part_id
        up_part.partId = up_part_id  # Ensure music21 recognizes this as a part
        up_part.partName = f"{original_part.partName} Up"
        # Copy instrument/clef information from original part to new parts
        instruments = original_part.getElementsByClass(instrument.Instrument)
        if instruments:
            # FIX: Use deepcopy directly as 'Instrument' objects might not have '.editor.copy()'
            up_part.insert(0, copy.deepcopy(instruments[0]))
        new_parts_to_add.append(up_part)

        down_part = stream.Part()
        down_part.id = down_part_id
        down_part.partId = down_part_id  # Ensure music21 recognizes this as a part
        down_part.partName = f"{original_part.partName} Down"
        if instruments:
            # FIX: Use deepcopy directly as 'Instrument' objects might not have '.editor.copy()'
            down_part.insert(0, copy.deepcopy(instruments[0]))
        new_parts_to_add.append(down_part)

        print(f"Splitting part {part_id} into {up_part_id} and {down_part_id}")

    logger.debug(f"split_part_mappings: {split_part_mappings}")
    # Add new parts to a list (do not append to the original score)
    split_parts = []
    for new_part in new_parts_to_add:
        split_parts.append(new_part)

    # Removed the 0 PASS: reversed_voices_by_part_measure logic
    # As the heuristic is now simplified within determine_note_voice_heuristic and the flag is not used there.

    # FIRST PASS: store lyrics by (part_id, measure_number, offset, lyric_number)
    # Lyrics are usually associated with a specific note, not a general time position for a 'voice'.
    # We collect them here so they can be re-attached to the correct note in the split part.
    lyrics_data = defaultdict(lambda: defaultdict(lambda: defaultdict(dict))) # {part_id: {measure_num: {offset: {lyric_num: lyric_obj}}}}

    for part in score.parts:
        pid = part.id
        if pid not in parts_to_split_ids:
            continue
        
        for measure in part.getElementsByClass('Measure'):
            measure_num = measure.number
            for element in measure.notesAndRests:
                if element.lyrics:
                    for lyric_obj in element.lyrics:
                        lyrics_data[pid][measure_num][element.offset][lyric_obj.number] = lyric_obj.editor.copy()

    logger.debug(f"lyrics_data: {lyrics_data}")
    # SECOND PASS: Separate notes/rests into new parts based on explicit Voice streams if present
    temp_new_measures = defaultdict(list) # {new_part_id: [music21.Measure objects]}

    for original_part in score.parts:
        logger.debug(f"Second pass original_part: {original_part.id}")
        pid = original_part.id
        if pid not in parts_to_split_ids:
            continue

        up_part_id = split_part_mappings[pid]["up"]
        down_part_id = split_part_mappings[pid]["down"]

        for original_measure in original_part.getElementsByClass('Measure'):
            logger.debug(f"Second pass measure: {original_measure.number}")
            measure_num = original_measure.number

            # --- NEW LOGIC: Split by Voice streams if present ---
            voice_streams = list(original_measure.getElementsByClass('Voice'))
            if len(voice_streams) > 1:
                measure_up = stream.Measure()
                measure_up.number = measure_num
                measure_down = stream.Measure()
                measure_down.number = measure_num
                # Copy measure-level elements (attributes, directions, etc.), skipping Voice streams
                for el in original_measure.elements:
                    if (
                        not isinstance(el, note.Note)
                        and not isinstance(el, note.Rest)
                        and not isinstance(el, chord.Chord)
                        and not (isinstance(el, stream.Voice))
                    ):
                        if isinstance(el, stream.Stream) and hasattr(el, 'clefs') and el.clefs:
                            copied_el_up = el.editor.copy()
                            copied_el_down = el.editor.copy()
                            measure_up.append(copied_el_up)
                            if isinstance(copied_el_down.clefs[0], clef.TrebleClef) and copied_el_down.clefs[0].octaveShift == 0:
                                convert_to_tenor_clef_music21(copied_el_down)
                            measure_down.append(copied_el_down)
                        else:
                            if hasattr(el, 'editor') and hasattr(el.editor, 'copy'):
                                measure_up.append(el.editor.copy())
                                measure_down.append(el.editor.copy())
                            else:
                                measure_up.append(copy.deepcopy(el))
                                measure_down.append(copy.deepcopy(el))
                for v in voice_streams:
                    vnum = None
                    if hasattr(v, 'number') and v.number is not None:
                        try:
                            vnum = int(v.number)
                        except Exception:
                            vnum = v.number
                    elif hasattr(v, 'id') and v.id is not None:
                        try:
                            vnum = int(v.id)
                        except Exception:
                            vnum = v.id
                    logger.debug(f"Processing Voice stream: {v}, vnum={vnum}")
                    for element in v.notesAndRests:
                        element_copy = copy.deepcopy(element)
                        if hasattr(element_copy, 'lyrics') and element_copy.lyrics:
                            element_copy.lyrics = []
                        lyrics_at_current_time = lyrics_data[pid][measure_num].get(element.offset, {})
                        chosen_lyric = None
                        if vnum == 1 and 1 in lyrics_at_current_time:
                            chosen_lyric = lyrics_at_current_time[1]
                        elif vnum == 2 and 2 in lyrics_at_current_time:
                            chosen_lyric = lyrics_at_current_time[2]
                        if chosen_lyric is None:
                            for lyric_num in sorted(lyrics_at_current_time.keys()):
                                chosen_lyric = lyrics_at_current_time[lyric_num]
                                break
                        if chosen_lyric and not is_middle_of_slur_music21(element_copy):
                            element_copy.addLyric(chosen_lyric.text, lyricNumber=1, lyricPlacement=chosen_lyric.placement)
                        if vnum == 1:
                            measure_up.append(element_copy)
                        elif vnum == 2:
                            measure_down.append(element_copy)
                measure_up.makeRests(fillGaps=True, inPlace=True)
                measure_down.makeRests(fillGaps=True, inPlace=True)
                temp_new_measures[up_part_id].append(measure_up)
                temp_new_measures[down_part_id].append(measure_down)
                continue  # Only do one logic per measure!

            # --- STRICT VOICE SPLIT LOGIC ---
            measure_up = stream.Measure()
            measure_up.number = measure_num
            measure_down = stream.Measure()
            measure_down.number = measure_num
            # Copy measure-level elements (attributes, directions, etc.), skipping Voice streams
            for el in original_measure.elements:
                if (
                    not isinstance(el, note.Note)
                    and not isinstance(el, note.Rest)
                    and not isinstance(el, chord.Chord)
                    and not (isinstance(el, stream.Voice))
                ):
                    if isinstance(el, stream.Stream) and hasattr(el, 'clefs') and el.clefs:
                        copied_el_up = el.editor.copy()
                        copied_el_down = el.editor.copy()
                        measure_up.append(copied_el_up)
                        if isinstance(copied_el_down.clefs[0], clef.TrebleClef) and copied_el_down.clefs[0].octaveShift == 0:
                            convert_to_tenor_clef_music21(copied_el_down)
                        measure_down.append(copied_el_down)
                    else:
                        if hasattr(el, 'editor') and hasattr(el.editor, 'copy'):
                            measure_up.append(el.editor.copy())
                            measure_down.append(el.editor.copy())
                        else:
                            measure_up.append(copy.deepcopy(el))
                            measure_down.append(copy.deepcopy(el))
            for element in original_measure.notesAndRests:
                logger.debug(f"Second pass element: {element}, offset={element.offset}")
                element_copy = copy.deepcopy(element)
                vnum = get_voice_number(element_copy)
                if hasattr(element_copy, 'lyrics') and element_copy.lyrics:
                    element_copy.lyrics = []
                lyrics_at_current_time = lyrics_data[pid][measure_num].get(element.offset, {})
                chosen_lyric = None
                if vnum == 1 and 1 in lyrics_at_current_time:
                    chosen_lyric = lyrics_at_current_time[1]
                elif vnum == 2 and 2 in lyrics_at_current_time:
                    chosen_lyric = lyrics_at_current_time[2]
                if chosen_lyric is None:
                    for lyric_num in sorted(lyrics_at_current_time.keys()):
                        chosen_lyric = lyrics_at_current_time[lyric_num]
                        break
                if chosen_lyric and not is_middle_of_slur_music21(element_copy):
                    element_copy.addLyric(chosen_lyric.text, lyricNumber=1, lyricPlacement=chosen_lyric.placement)
                if vnum == 1:
                    measure_up.append(element_copy)
                elif vnum == 2:
                    measure_down.append(element_copy)
            measure_up.makeRests(fillGaps=True, inPlace=True)
            measure_down.makeRests(fillGaps=True, inPlace=True)
            temp_new_measures[up_part_id].append(measure_up)
            temp_new_measures[down_part_id].append(measure_down)
    logger.debug(f"temp_new_measures: {temp_new_measures}")
    # Populate the newly created music21 Part objects with their measures
    for new_part_id, measures_list in temp_new_measures.items():
        logger.debug(f"Populating new_part_id: {new_part_id}, measures: {measures_list}")
        for new_p in split_parts: # Find the actual music21 Part object
            if new_p.id == new_part_id:
                for m in measures_list:
                    new_p.append(m)
                break

    # Final score reconstruction: remove original parts, keep only new ones.
    # Create a new score and add parts to it.
    new_score = stream.Score()
    
    # FIX: Add a check for score.metadata being None and use deepcopy
    if score.metadata is not None:
        new_score.metadata = copy.deepcopy(score.metadata)
    else:
        new_score.metadata = metadata.Metadata() # Initialize with an empty Metadata object


    # Copy other score-level elements (title, composer, etc.), but skip layout/staff group elements
    for element in score.elements:
        if not isinstance(element, stream.Part) and not isinstance(element, layout.StaffGroup):
            # Use .editor.copy() if available, else fallback to copy.deepcopy
            if hasattr(element, 'editor') and hasattr(element.editor, 'copy'):
                new_score.append(element.editor.copy())
            else:
                new_score.append(copy.deepcopy(element))

    logger.debug(f"split_parts: {split_parts}")
    # Add only the parts we want to keep (the split parts)
    for part in split_parts:
        logger.debug(f"Appending split part: {part}, id={part.id}, measures={len(part.getElementsByClass('Measure'))}")
        new_score.append(part)

    logger.debug(f"new_score elements: {[(el, getattr(el, 'id', None), type(el)) for el in new_score]}")
    logger.debug(f"new_score.parts: {[(p, p.id) for p in new_score.parts]}")
    assert len(new_score.parts) == len(split_parts), f"Split parts not added correctly: {len(new_score.parts)} vs {len(split_parts)}"

    return new_score


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split MusicXML score into separate voice parts using music21 and stem direction heuristic.")

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

    # Load MusicXML file using music21
    try:
        score = converter.parse(INPUT_FILE)
    except Exception as e:
        print(f"Error parsing MusicXML file: {e}")
        sys.exit(1)

    # Process the score
    transformed_score = main(score)

    # Write the transformed score to a new MusicXML file
    try:
        transformed_score.write('musicxml', fp=OUTPUT_FILE)
        print(f"Written transformed MusicXML to {OUTPUT_FILE}")
    except Exception as e:
        print(f"Error writing MusicXML file: {e}")
        sys.exit(1)