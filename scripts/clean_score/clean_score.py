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
        elif actual_duration_units == division_value * 4:
            return 'whole'
        elif actual_duration_units == division_value / 4:
            return '16th'
        elif actual_duration_units == division_value / 8:
            return '32nd'
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
        measures_for_output_staff1 = [] # This will get content from input voice 1 (up-stemmed)
        measures_for_output_staff2 = [] # This will get content from input voice 0 (down-stemmed)

        # Get all <Measure> elements from the original Staff id="1"
        measures_in_staff1_copy = list(original_staff1.findall('Measure'))

        for i, measure_elem_input in enumerate(measures_in_staff1_copy):
            voices_in_measure = measure_elem_input.findall('voice')
            
            # Expect exactly two voices in the input measure for this conversion logic
            if len(voices_in_measure) < 2:
                print(f"Warning: Measure {i+1} in Staff 1 does not have two voices. "
                      "Skipping processing for Staff 2 from this measure.")
                # If there are extra voices beyond the first, remove them for staff1.
                for j in range(1, len(voices_in_measure)):
                    measure_elem_input.remove(voices_in_measure[j])
                continue

            # Assign input voices based on their typical stem direction/role in the examples
            # voice 0 (input): often down-stemmed, lower part -> goes to output Staff 2
            # voice 1 (input): often up-stemmed, higher part -> goes to output Staff 1
            input_voice_for_output_staff2 = voices_in_measure[0]
            input_voice_for_output_staff1 = voices_in_measure[1]

            # --- Construct Measure for output Staff id="1" (upper staff) ---
            new_measure_for_staff1 = ET.Element('Measure')
            voice_elem_staff1 = ET.Element('voice')

            # Process elements from input_voice_for_output_staff1
            for child in input_voice_for_output_staff1:
                if child.tag == 'location':
                    # Convert <location> to <Rest>
                    fractions_elem = child.find('fractions')
                    if fractions_elem is not None and fractions_elem.text:
                        duration_type_text = _get_duration_type(fractions_elem.text, division_value)
                        rest_node = ET.fromstring(f'''<Rest><durationType>{duration_type_text}</durationType></Rest>''')
                        voice_elem_staff1.append(rest_node)
                else:
                    voice_elem_staff1.append(copy.deepcopy(child)) # Deep copy other elements

            new_measure_for_staff1.append(voice_elem_staff1)
            measures_for_output_staff1.append(new_measure_for_staff1)

            # --- Construct Measure for output Staff id="2" (lower staff) ---
            new_measure_for_staff2 = ET.Element('Measure')
            voice_elem_staff2 = ET.Element('voice')

            # Process elements from input_voice_for_output_staff2
            for child in input_voice_for_output_staff2:
                if child.tag == 'location':
                    # Convert <location> to <Rest>
                    fractions_elem = child.find('fractions')
                    if fractions_elem is not None and fractions_elem.text:
                        duration_type_text = _get_duration_type(fractions_elem.text, division_value)
                        rest_node = ET.fromstring(f'''<Rest><durationType>{duration_type_text}</durationType></Rest>''')
                        voice_elem_staff2.append(rest_node)
                elif child.tag == 'Clef':
                    # Clef from input voice 0 might be present, but it's not in output Staff 2.
                    # Staff 2 uses a default F clef from <Instrument> definition.
                    pass
                else:
                    voice_elem_staff2.append(copy.deepcopy(child))

            new_measure_for_staff2.append(voice_elem_staff2)
            measures_for_output_staff2.append(new_measure_for_staff2)

            # After processing, remove all voices from the original input measure
            # This makes sure original_staff1 is empty if it's reused, or elements are properly moved.
            for v in list(measure_elem_input.findall('voice')):
                measure_elem_input.remove(v)

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

        # Handle initial Clef and TimeSig for Staff id="1" and Staff id="2" in the output
        # These should be copied from the original input's first voice (which goes to Staff id="2" conceptually)
        # Re-parse the input to get a fresh start for initial Clef/TimeSig extraction
        re_tree = ET.parse(input_path)
        re_root = re_tree.getroot()
        re_original_staff1_for_initial = re_root.find('Score/Staff[@id="1"]')
        re_first_measure_for_initial = re_original_staff1_for_initial.find('Measure')
        
        # Get the first voice of the first measure from the original input,
        # which typically contains the initial Clef and TimeSig for the staff.
        re_input_voice_0 = re_first_measure_for_initial.find('voice[1]') # This is the first voice in MuseScore XML's 1-based indexing for voice elements

        if re_input_voice_0 is not None:
            initial_clef = re_input_voice_0.find('Clef')
            initial_time_sig = re_input_voice_0.find('TimeSig')

            # Add Clef and TimeSig to the first measure of Staff id="1" in the output
            output_staff1_first_measure = new_staff1.find('Measure')
            if output_staff1_first_measure is not None:
                output_staff1_first_voice = output_staff1_first_measure.find('voice')
                if output_staff1_first_voice is not None:
                    # Insert Clef and TimeSig at the beginning of the voice
                    if initial_time_sig is not None:
                        # Check if TimeSig already exists for Staff 1's first voice to avoid duplicates
                        if output_staff1_first_voice.find('TimeSig') is None:
                            output_staff1_first_voice.insert(0, copy.deepcopy(initial_time_sig))
                    if initial_clef is not None:
                        # Check if Clef already exists for Staff 1's first voice to avoid duplicates
                        if output_staff1_first_voice.find('Clef') is None:
                            output_staff1_first_voice.insert(0, copy.deepcopy(initial_clef))
            
            # Add TimeSig to the first measure of Staff id="2" in the output
            output_staff2_first_measure = new_staff2.find('Measure')
            if output_staff2_first_measure is not None:
                output_staff2_first_voice = output_staff2_first_measure.find('voice')
                if output_staff2_first_voice is not None:
                    # Insert TimeSig at the beginning of the voice for Staff 2
                    if initial_time_sig is not None:
                        # Check if TimeSig already exists for Staff 2's first voice to avoid duplicates
                        if output_staff2_first_voice.find('TimeSig') is None:
                            output_staff2_first_voice.insert(0, copy.deepcopy(initial_time_sig))
        else:
            print("Warning: Could not find initial voice to extract Clef/TimeSig for new staffs.")


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

