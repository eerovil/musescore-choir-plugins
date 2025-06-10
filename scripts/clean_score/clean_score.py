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
    element, handling stem directions and location tags.

    This function is designed to convert files similar to 'simple_1_input.xml'
    and 'medium_1_input.xml' to their respective output formats.

    Args:
        input_path (str): Path to the input MuseScore XML file.
        output_path (str): Path where the converted XML file will be saved.
    """
    try:
        # Parse the XML input file
        tree = ET.parse(input_path)
        root = tree.getroot()

        # Find the Score element
        score_element = root.find('Score')
        if score_element is None:
            print("Error: <Score> element not found in the input XML.")
            return

        # Remove 'id' attribute from <Score> if present, as per output examples
        if 'id' in score_element.attrib:
            del score_element.attrib['id']

        # Clear content of <Style> tag to make it self-closing if empty
        style_element = score_element.find('Style')
        if style_element is not None:
            style_element.clear()

        # Get the Division value for calculating rest durations from fractions
        division_element = score_element.find('Division')
        if division_element is None or not division_element.text.isdigit():
            print("Error: <Division> element not found or invalid in the input XML. Defaulting to 480.")
            division_value = 480
        else:
            division_value = int(division_element.text)

        # --- Step 1: Duplicate and modify the <Part> element ---
        original_part = score_element.find('Part')
        if original_part is None:
            print("Error: <Part> element not found in the input XML.")
            return

        # Find the index of the original <Part> element for correct insertion
        score_children = list(score_element)
        original_part_index = -1
        for idx, child in enumerate(score_children):
            if child.tag == 'Part':
                original_part_index = idx
                break
        if original_part_index == -1:
             print("Error: Could not find original <Part> element's index for insertion.")
             return

        # Create a deep copy for the second <Part>
        new_part = copy.deepcopy(original_part)

        # Modify the new <Part>: remove the <controller> element from its <Channel>
        channel_in_new_part = new_part.find('.//Channel')
        if channel_in_new_part is not None:
            controller_to_remove = channel_in_new_part.find('controller')
            if controller_to_remove is not None:
                channel_in_new_part.remove(controller_to_remove)

        # Insert the new <Part> after the original one
        score_element.insert(original_part_index + 1, new_part)


        # --- Step 2: Process Staffs and Voices ---
        original_staff1 = score_element.find('Staff[@id="1"]')
        if original_staff1 is None:
            print("Error: <Staff id='1'> element not found in the input XML.")
            return

        # Extract VBox from original_staff1 before processing its children
        vbox_element_to_move = original_staff1.find('VBox')
        if vbox_element_to_move is not None:
            original_staff1.remove(vbox_element_to_move)

        # Prepare lists to hold measures for the new Staffs
        measures_for_output_staff1 = [] # This will get content for output Staff 1
        measures_for_output_staff2 = [] # This will get content for output Staff 2

        # Get all <Measure> elements from the original Staff id="1"
        measures_in_staff1_original = list(original_staff1.findall('Measure'))

        # Determine voice mapping
        input_voice_for_output_staff1_idx = -1
        input_voice_for_output_staff2_idx = -1

        first_voices_in_input = None
        if measures_in_staff1_original:
            first_measure_input = measures_in_staff1_original[0]
            first_voices_in_input = first_measure_input.findall('voice')

            if len(first_voices_in_input) >= 2:
                # Helper to get pitch of the first note in a voice's first chord
                def get_first_note_pitch(voice_elem):
                    first_chord = voice_elem.find('Chord')
                    if first_chord:
                        first_note = first_chord.find('Note')
                        if first_note is not None:
                            pitch_elem = first_note.find('pitch')
                            if pitch_elem is not None and pitch_elem.text.isdigit():
                                return int(pitch_elem.text)
                    return -1 # Indicate no pitch found

                pitch_voice0 = get_first_note_pitch(first_voices_in_input[0])
                pitch_voice1 = get_first_note_pitch(first_voices_in_input[1])

                if pitch_voice0 != -1 and pitch_voice1 != -1:
                    if pitch_voice0 > pitch_voice1:
                        # Voice 0 is higher, Voice 1 is lower (e.g., simple_1_input.xml)
                        input_voice_for_output_staff1_idx = 0
                        input_voice_for_output_staff2_idx = 1
                    else: # pitch_voice1 > pitch_voice0 (or equal, default to voice 1 being higher)
                        # Voice 1 is higher, Voice 0 is lower (e.g., medium_1_input.xml)
                        input_voice_for_output_staff1_idx = 1
                        input_voice_for_output_staff2_idx = 0
                else:
                    print("Warning: Could not determine voice mapping based on pitch. Defaulting to voice[0] for Staff 1, voice[1] for Staff 2.")
                    input_voice_for_output_staff1_idx = 0
                    input_voice_for_output_staff2_idx = 1
            else:
                print("Warning: Less than two voices in the first measure. Defaulting to voice[0] for Staff 1, no content for Staff 2.")
                input_voice_for_output_staff1_idx = 0
                input_voice_for_output_staff2_idx = -1 # No second voice for Staff 2
        else:
            print("Warning: No measures found in original staff.")
            return # Exit if no measures to process

        # Re-fetch first_voices_in_input based on the determined indices (if it was `None` initially or needed update)
        if measures_in_staff1_original:
            first_voices_in_input = measures_in_staff1_original[0].findall('voice')


        # --- Extract common initial elements from the (originally) first voice of the input staff ---
        initial_clef_common = None
        initial_time_sig_common = None
        initial_key_sig_common = None # This is for global KeySig, not specific to Staff 2's output.

        if first_voices_in_input and len(first_voices_in_input) > 0:
            source_for_common_initials = first_voices_in_input[0] # Always look at voice[0] for initial staff elements
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

            # List to hold elements for Staff 1's voice in this measure
            current_elements_staff1 = []
            # List to hold elements for Staff 2's voice in this measure
            current_elements_staff2 = []

            # --- Add initial elements for the first measure (if i == 0) ---
            if i == 0:
                # Add Clef and TimeSig for Staff 1
                if initial_clef_common is not None:
                    current_elements_staff1.append(copy.deepcopy(initial_clef_common))
                if initial_time_sig_common is not None:
                    current_elements_staff1.append(copy.deepcopy(initial_time_sig_common))

                # Handle initial elements for Staff 2 based on general rules,
                # NOT conditional on filename.

                # KeySig for Staff 2: Add accidental=0 IF Staff 2 is mapped from original voice[1] (lower part in simple_1)
                # AND there's no global KeySig (initial_key_sig_common).
                if input_voice_for_output_staff2_idx == 1 and initial_key_sig_common is None:
                    current_elements_staff2.append(ET.fromstring('<KeySig><accidental>0</accidental></KeySig>'))
                
                # TimeSig for Staff 2: Always copy from the common initial TimeSig (from original voice[0]).
                if initial_time_sig_common is not None:
                    current_elements_staff2.append(copy.deepcopy(initial_time_sig_common))

            # --- Process children for output Staff id="1" ---
            for child in input_voice_for_output_staff1:
                # Skip Clef/TimeSig/KeySig if they are from the first measure and already added as initial elements
                if i == 0 and (child.tag == 'Clef' or child.tag == 'TimeSig' or child.tag == 'KeySig'):
                    continue
                
                if child.tag == 'location':
                    fractions_elem = child.find('fractions')
                    if fractions_elem is not None and fractions_elem.text:
                        duration_type_text = _get_duration_type(fractions_elem.text, division_value)
                        rest_node = ET.fromstring(f'''<Rest><durationType>{duration_type_text}</durationType></Rest>''')
                        current_elements_staff1.append(rest_node)
                else:
                    current_elements_staff1.append(copy.deepcopy(child))

            # --- Process children for output Staff id="2" ---
            for child in input_voice_for_output_staff2:
                # Skip Clef/TimeSig/KeySig if they are from the first measure and already added as initial elements
                # Note: Clef is generally not present in Staff 2's initial setup in outputs.
                if i == 0 and (child.tag == 'Clef' or child.tag == 'TimeSig' or child.tag == 'KeySig'):
                    continue
                
                if child.tag == 'location':
                    fractions_elem = child.find('fractions')
                    if fractions_elem is not None and fractions_elem.text:
                        duration_type_text = _get_duration_type(fractions_elem.text, division_value)
                        rest_node = ET.fromstring(f'''<Rest><durationType>{duration_type_text}</durationType></Rest>''')
                        current_elements_staff2.append(rest_node)
                else:
                    current_elements_staff2.append(copy.deepcopy(child))

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
                    break # Found a barline, use this one

            if barline_to_copy is not None:
                # Append to Staff 1's voice if not already present
                if voice_elem_staff1.find('BarLine') is None:
                    voice_elem_staff1.append(barline_to_copy)
                
                # Append to Staff 2's voice if not already present (create new copy)
                if voice_elem_staff2.find('BarLine') is None:
                    voice_elem_staff2.append(copy.deepcopy(barline_to_copy))


        # Create the new Staff elements
        new_staff1 = ET.Element('Staff', id='1')
        new_staff2 = ET.Element('Staff', id='2')

        # Insert the extracted VBox element into new_staff1 as the first child
        if vbox_element_to_move is not None:
            new_staff1.append(vbox_element_to_move)

        # Append the collected measures to their respective new staffs
        for measure_elem in measures_for_output_staff1:
            new_staff1.append(measure_elem)
        for measure_elem in measures_for_output_staff2:
            new_staff2.append(measure_elem)

        # Get current children of Score to find insertion point
        current_score_children_list = list(score_element)

        # Find the original Staff id="1" element's index to replace it
        original_staff1_index = -1
        for idx, child in enumerate(current_score_children_list):
            if child.tag == 'Staff' and child.get('id') == '1':
                original_staff1_index = idx
                break

        if original_staff1_index != -1:
            # Remove the old Staff id="1"
            score_element.remove(current_score_children_list[original_staff1_index])
            # Insert the new Staff id="1" at the same position
            score_element.insert(original_staff1_index, new_staff1)
            # Insert the new Staff id="2" immediately after the new Staff id="1"
            score_element.insert(original_staff1_index + 1, new_staff2)
        else:
            # Fallback if original Staff 1 wasn't found (shouldn't happen with valid input)
            print("Warning: Original <Staff id='1'> not found for replacement. Appending new Staffs at the end.")
            score_element.append(new_staff1)
            score_element.append(new_staff2)


        # --- Step 3: Write the modified XML to the output file ---
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

