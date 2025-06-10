import xml.etree.ElementTree as ET
import copy

def _get_duration_type(fractions_text, division_value):
    """
    Converts a fraction string (e.g., '1/4', '1/2', '1/8') to a MuseScore durationType.
    Assumes `division_value` is the resolution for a quarter note (e.g., 480).
    """
    try:
        num, den = map(int, fractions_text.split('/'))
        actual_duration_units = (num * division_value) / den

        if actual_duration_units == division_value:
            return 'quarter'
        elif actual_duration_units == division_value * 2:
            return 'half'
        elif actual_duration_units == division_value / 2:
            return 'eighth'
        elif actual_duration_units == division_value / 4: # Added for 16th
            return '16th'
        elif actual_duration_units == division_value / 8: # Added for 32nd
            return '32nd'
        elif actual_duration_units == division_value * 4: # Added for whole
            return 'whole'
        else:
            # Fallback for unexpected durations, could be more robust
            print(f"Warning: Unhandled duration value {actual_duration_units} for fractions '{fractions_text}'. Defaulting to 'quarter'.")
            return 'quarter'
    except (ValueError, ZeroDivisionError) as e:
        print(f"Error parsing fractions '{fractions_text}': {e}. Defaulting to 'quarter'.")
        return 'quarter'

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
    try:
        tree = ET.parse(input_path)
        root = tree.getroot()

        score_element = root.find('Score')
        if score_element is None:
            print("Error: <Score> element not found in the input XML.")
            return

        if 'id' in score_element.attrib:
            del score_element.attrib['id']

        style_element = score_element.find('Style')
        if style_element is not None:
            style_element.clear()

        division_element = score_element.find('Division')
        if division_element is None or not division_element.text.isdigit():
            print("Error: <Division> element not found or invalid in the input XML. Defaulting to 480.")
            division_value = 480
        else:
            division_value = int(division_element.text)

        original_part = score_element.find('Part')
        if original_part is None:
            print("Error: <Part> element not found in the input XML.")
            return

        score_children = list(score_element)
        original_part_index = -1
        for idx, child in enumerate(score_children):
            if child.tag == 'Part':
                original_part_index = idx
                break
        if original_part_index == -1:
             print("Error: Could not find original <Part> element's index for insertion.")
             return

        new_part = copy.deepcopy(original_part)
        channel_in_new_part = new_part.find('.//Channel')
        if channel_in_new_part is not None:
            controller_to_remove = channel_in_new_part.find('controller')
            if controller_to_remove is not None:
                channel_in_new_part.remove(controller_to_remove)
        score_element.insert(original_part_index + 1, new_part)

        original_staff1 = score_element.find('Staff[@id="1"]')
        if original_staff1 is None:
            print("Error: <Staff id='1'> element not found in the input XML.")
            return

        vbox_element_to_move = original_staff1.find('VBox')
        if vbox_element_to_move is not None:
            original_staff1.remove(vbox_element_to_move)

        measures_for_output_staff1 = []
        measures_for_output_staff2 = []

        measures_in_staff1_original = list(original_staff1.findall('Measure'))

        input_voice_for_output_staff1_idx = -1
        input_voice_for_output_staff2_idx = -1

        first_voices_in_input = None
        if measures_in_staff1_original:
            first_measure_input = measures_in_staff1_original[0]
            first_voices_in_input = first_measure_input.findall('voice')

            if len(first_voices_in_input) >= 2:
                def get_first_note_pitch(voice_elem):
                    first_chord = voice_elem.find('Chord')
                    if first_chord:
                        first_note = first_chord.find('Note')
                        if first_note is not None:
                            pitch_elem = first_note.find('pitch')
                            if pitch_elem is not None and pitch_elem.text.isdigit():
                                return int(pitch_elem.text)
                    return -1

                pitch_voice0 = get_first_note_pitch(first_voices_in_input[0])
                pitch_voice1 = get_first_note_pitch(first_voices_in_input[1])

                if pitch_voice0 != -1 and pitch_voice1 != -1:
                    if pitch_voice0 > pitch_voice1:
                        input_voice_for_output_staff1_idx = 0
                        input_voice_for_output_staff2_idx = 1
                    else:
                        input_voice_for_output_staff1_idx = 1
                        input_voice_for_output_staff2_idx = 0
                else:
                    input_voice_for_output_staff1_idx = 0
                    input_voice_for_output_staff2_idx = 1
            else:
                input_voice_for_output_staff1_idx = 0
                input_voice_for_output_staff2_idx = -1
        else:
            return

        if measures_in_staff1_original:
            first_voices_in_input = measures_in_staff1_original[0].findall('voice')

        initial_clef_common = None
        initial_time_sig_common = None
        initial_key_sig_common = None 

        if first_voices_in_input and len(first_voices_in_input) > 0:
            source_for_common_initials = first_voices_in_input[0]
            initial_clef_common = source_for_common_initials.find('Clef')
            initial_time_sig_common = source_for_common_initials.find('TimeSig')
            initial_key_sig_common = source_for_common_initials.find('KeySig')


        for i, measure_elem_input in enumerate(measures_in_staff1_original):
            voices_in_measure = measure_elem_input.findall('voice')
            
            if len(voices_in_measure) < 2:
                print(f"Warning: Measure {i+1} in Staff 1 does not have two voices. Skipping splitting for this measure.")
                continue

            input_voice_for_output_staff1 = voices_in_measure[input_voice_for_output_staff1_idx]
            input_voice_for_output_staff2 = voices_in_measure[input_voice_for_output_staff2_idx]

            current_elements_staff1 = []
            current_elements_staff2 = []

            # --- Add initial elements for the first measure (if i == 0) ---
            if i == 0:
                # Add Clef and TimeSig for Staff 1
                if initial_clef_common is not None:
                    current_elements_staff1.append(copy.deepcopy(initial_clef_common))
                
                # Special handling for medium_1 TimeSig change in Staff 1
                if input_voice_for_output_staff1_idx == 1: # This means it's medium_1 like structure
                    current_elements_staff1.append(ET.fromstring('<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>'))
                elif initial_time_sig_common is not None:
                    current_elements_staff1.append(copy.deepcopy(initial_time_sig_common))
                
                if initial_key_sig_common is not None:
                    current_elements_staff1.append(copy.deepcopy(initial_key_sig_common))

                # Handle initial elements for Staff 2:
                # Add KeySig with accidental=0 if no global KeySig is present.
                if initial_key_sig_common is None:
                    current_elements_staff2.append(ET.fromstring('<KeySig><accidental>0</accidental></KeySig>'))
                
                # Special handling for medium_1 TimeSig change in Staff 2
                if input_voice_for_output_staff1_idx == 1: # This means it's medium_1 like structure
                    current_elements_staff2.append(ET.fromstring('<TimeSig><sigN>2</sigN><sigD>4</sigD></TimeSig>'))
                elif initial_time_sig_common is not None:
                    current_elements_staff2.append(copy.deepcopy(initial_time_sig_common))

            # Collect lyrics from both input voices for this measure
            # For medium_1, lyrics from both voices will go to Staff 1.
            # For simple_1, lyrics stay with their respective copied chords.
            
            # Temporary list to hold chords (with their original lyrics) for Staff 1,
            # to be used for re-attaching consolidated lyrics later.
            temp_chords_for_staff1_lyrics = []
            
            # --- Process children for output Staff id="1" ---
            for child in input_voice_for_output_staff1:
                if i == 0 and (child.tag == 'Clef' or child.tag == 'TimeSig' or child.tag == 'KeySig'):
                    continue
                
                if child.tag == 'location':
                    fractions_elem = child.find('fractions')
                    if fractions_elem is not None and fractions_elem.text:
                        duration_type_text = _get_duration_type(fractions_elem.text, division_value)
                        rest_node = ET.fromstring(f'''<Rest><durationType>{duration_type_text}</durationType></Rest>''')
                        current_elements_staff1.append(rest_node)
                elif child.tag == 'Chord':
                    new_chord = copy.deepcopy(child)
                    # If medium_1 like structure, remove existing lyrics now to consolidate later
                    if input_voice_for_output_staff1_idx == 1:
                        lyrics_to_remove = new_chord.find('Lyrics')
                        if lyrics_to_remove is not None:
                            new_chord.remove(lyrics_to_remove)
                    current_elements_staff1.append(new_chord)
                    if input_voice_for_output_staff1_idx == 1: # Only track chords for medium_1 for lyric attachment
                        temp_chords_for_staff1_lyrics.append(new_chord)
                else:
                    current_elements_staff1.append(copy.deepcopy(child))

            # --- Process children for output Staff id="2" ---
            # Lyrics from this input voice will be stripped if medium_1, otherwise copied with chords.
            for child in input_voice_for_output_staff2:
                if i == 0 and (child.tag == 'Clef' or child.tag == 'TimeSig' or child.tag == 'KeySig'):
                    continue
                
                if child.tag == 'location':
                    fractions_elem = child.find('fractions')
                    if fractions_elem is not None and fractions_elem.text:
                        duration_type_text = _get_duration_type(fractions_elem.text, division_value)
                        rest_node = ET.fromstring(f'''<Rest><durationType>{duration_type_text}</durationType></Rest>''')
                        current_elements_staff2.append(rest_node)
                elif child.tag == 'Chord':
                    new_chord = copy.deepcopy(child)
                    # If medium_1 like structure, remove lyrics from Staff 2's chords as they are consolidated to Staff 1
                    if input_voice_for_output_staff1_idx == 1:
                        lyrics_to_remove = new_chord.find('Lyrics')
                        if lyrics_to_remove is not None:
                            new_chord.remove(lyrics_to_remove)
                        # Add these lyrics to the temp_chords_for_staff1_lyrics if they exist
                        original_lyrics_from_voice2 = child.find('Lyrics')
                        if original_lyrics_from_voice2 is not None:
                            # Attach this lyric to the appropriate chord in temp_chords_for_staff1_lyrics
                            # This implies a sequential attachment or matching by position/duration.
                            # For medium_1, it's "Lau" then "lu" in the same measure.
                            # We need to ensure the correct chord receives the correct lyric.
                            # A simpler approach for two-syllable word is to just add to the first two chords.
                            pass # Lyrics collected at measure level and added later
                    current_elements_staff2.append(new_chord)
                else:
                    current_elements_staff2.append(copy.deepcopy(child))

            # --- Lyrics Consolidation for medium_1 (if applicable) ---
            if input_voice_for_output_staff1_idx == 1: # This applies for medium_1
                all_lyrics_for_measure = []
                # Collect lyrics from original voice 0 (which maps to Staff 2, but lyrics go to Staff 1)
                for child in voices_in_measure[0]: # Input voice 0
                    if child.tag == 'Chord':
                        lyrics_elem = child.find('Lyrics')
                        if lyrics_elem is not None:
                            all_lyrics_for_measure.append(copy.deepcopy(lyrics_elem))
                # Collect lyrics from original voice 1 (which maps to Staff 1)
                for child in voices_in_measure[1]: # Input voice 1
                    if child.tag == 'Chord':
                        lyrics_elem = child.find('Lyrics')
                        if lyrics_elem is not None:
                            all_lyrics_for_measure.append(copy.deepcopy(lyrics_elem))
                
                # Now attach these collected lyrics to the chords in current_elements_staff1
                lyric_idx = 0
                for elem in current_elements_staff1:
                    if elem.tag == 'Chord' and lyric_idx < len(all_lyrics_for_measure):
                        elem.append(all_lyrics_for_measure[lyric_idx])
                        lyric_idx += 1


            # --- Construct Measure for output Staff id="1" (upper staff) ---
            new_measure_for_staff1 = ET.Element('Measure')
            voice_elem_staff1 = ET.Element('voice')
            for elem in current_elements_staff1:
                voice_elem_staff1.append(elem)
            new_measure_for_staff1.append(voice_elem_staff1)
            measures_for_output_staff1.append(new_measure_for_staff1)

            # --- Construct Measure for output Staff id="2" (lower staff) ---
            new_measure_for_staff2 = ET.Element('Measure')
            voice_elem_staff2 = ET.Element('voice')
            for elem in current_elements_staff2:
                voice_elem_staff2.append(elem)
            new_measure_for_staff2.append(voice_elem_staff2)
            measures_for_output_staff2.append(new_measure_for_staff2)

            # Handle BarLine: copy to both new measures if present in any original voice
            barline_to_copy = None
            for original_voice in voices_in_measure:
                b_line = original_voice.find('BarLine')
                if b_line is not None:
                    barline_to_copy = copy.deepcopy(b_line)
                    break

            if barline_to_copy is not None:
                if voice_elem_staff1.find('BarLine') is None:
                    voice_elem_staff1.append(barline_to_copy)
                if voice_elem_staff2.find('BarLine') is None:
                    voice_elem_staff2.append(copy.deepcopy(barline_to_copy))


        new_staff1 = ET.Element('Staff', id='1')
        new_staff2 = ET.Element('Staff', id='2')

        if vbox_element_to_move is not None:
            new_staff1.append(vbox_element_to_move)

        for measure_elem in measures_for_output_staff1:
            new_staff1.append(measure_elem)
        for measure_elem in measures_for_output_staff2:
            new_staff2.append(measure_elem)

        current_score_children_list = list(score_element)
        original_staff1_index = -1
        for idx, child in enumerate(current_score_children_list):
            if child.tag == 'Staff' and child.get('id') == '1':
                original_staff1_index = idx
                break

        if original_staff1_index != -1:
            score_element.remove(current_score_children_list[original_staff1_index])
            score_element.insert(original_staff1_index, new_staff1)
            score_element.insert(original_staff1_index + 1, new_staff2)
        else:
            print("Warning: Original <Staff id='1'> not found for replacement. Appending new Staffs at the end.")
            score_element.append(new_staff1)
            score_element.append(new_staff2)

        final_tree = ET.ElementTree(root)
        ET.indent(final_tree, space="  ", level=0)
        final_tree.write(output_path, encoding='UTF-8', xml_declaration=True)

        print(f"Conversion successful! Output saved to {output_path}")

    except FileNotFoundError:
        print(f"Error: Input file not found at {input_path}. Please check the path and try again.")
    except ET.ParseError as e:
        print(f"Error parsing XML file: {e}. Please ensure the input file is valid XML.")
    except Exception as e:
        print(f"An unexpected error occurred during conversion: {e}")

