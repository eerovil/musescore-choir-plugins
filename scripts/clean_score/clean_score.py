#!/usr/bin/env python3

from lxml import etree
import argparse
import sys
import xml.etree.ElementTree as ET
import copy

def main(input_path, output_path):
    """
    Converts a MuseScore XML file from a single-staff, two-voice structure
    to a two-staff, single-voice-per-staff structure, and duplicates the Part
    element.

    This function is designed to convert files similar to 'simple_1_input.xml'
    to the format of 'simple_1_output.xml'.

    Args:
        input_path (str): Path to the input MuseScore XML file.
        output_path (str): Path where the converted XML file will be saved.
    """
    try:
        # Parse the XML input file
        tree = ET.parse(input_path)
        root = tree.getroot()

        # Find the Score element, which is the main container for musical data
        score_element = root.find('Score')
        if score_element is None:
            print("Error: <Score> element not found in the input XML.")
            return

        # Based on simple_1_output, the <Score> element does not have an 'id' attribute.
        # If it exists in the input, remove it.
        if 'id' in score_element.attrib:
            del score_element.attrib['id']

        # Clean up the <Style> tag: In the output, it's self-closing if empty.
        # The input has whitespace which prevents self-closing, so clear it.
        style_element = score_element.find('Style')
        if style_element is not None:
            style_element.clear() # Clears any text content and children, making it self-closing on write


        # --- Step 1: Duplicate and modify the <Part> element ---
        # The output file has two <Part> elements, both for 'Piano'.
        # We find the existing one and create a copy.
        original_part = score_element.find('Part')
        if original_part is None:
            print("Error: <Part> element not found in the input XML.")
            return

        # Get the index of the original <Part> element to insert the new one correctly.
        # We convert to a list to easily find the index of mutable elements.
        score_children = list(score_element)
        original_part_index = -1
        for idx, child in enumerate(score_children):
            # Find the first <Part> element; assuming only one <Part> initially as per input.
            if child.tag == 'Part':
                original_part_index = idx
                break

        if original_part_index == -1:
             print("Error: Could not find original <Part> element's index for insertion.")
             return

        # Create a deep copy of the original <Part> to serve as the second part.
        new_part = copy.deepcopy(original_part)

        # Modify the new <Part>: remove the <controller> element from its <Channel>.
        # This matches the structure of the second <Part> in simple_1_output.xml.
        channel_in_new_part = new_part.find('.//Channel')
        if channel_in_new_part is not None:
            controller_to_remove = channel_in_new_part.find('controller')
            if controller_to_remove is not None:
                channel_in_new_part.remove(controller_to_remove)

        # Insert the new <Part> directly after the original <Part> in the <Score> element.
        score_element.insert(original_part_index + 1, new_part)


        # --- Step 2: Process Staffs and Voices ---
        # The input has a single <Staff id="1"> with two <voice> elements per <Measure>.
        # The output has two <Staff> elements (<Staff id="1"> and <Staff id="2">),
        # each containing one <voice> element per <Measure>.
        original_staff1 = score_element.find('Staff[@id="1"]')
        if original_staff1 is None:
            print("Error: <Staff id='1'> element not found in the input XML.")
            return

        # Create the new <Staff id="2"> element, which will hold the content of the second voice.
        new_staff2 = ET.Element('Staff', id='2')

        # Define templates for <KeySig> and <TimeSig> to be added to the first measure of Staff id="2",
        # as seen in simple_1_output.xml.
        key_sig_template = ET.fromstring('''<KeySig><accidental>0</accidental></KeySig>''')
        time_sig_template = ET.fromstring('''<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>''')

        # This list will temporarily hold the measures that will populate new_staff2.
        measures_to_add_to_staff2 = []

        # Get all <Measure> elements from the original Staff id="1".
        # We iterate over a copy to safely modify the original 'original_staff1' during the loop.
        measures_in_staff1_copy = list(original_staff1.findall('Measure'))

        for i, measure in enumerate(measures_in_staff1_copy):
            voices = measure.findall('voice')
            
            # Ensure the measure has at least two voices as expected for this transformation.
            if len(voices) < 2:
                print(f"Warning: Measure {i+1} in Staff 1 does not have two voices. "
                      "This measure's second voice content will not be processed for Staff 2. "
                      "Original Staff 1 will retain its existing voice(s).")
                # If there are extra voices beyond the first, remove them for staff1.
                for j in range(1, len(voices)):
                    measure.remove(voices[j])
                continue # Skip processing for Staff 2 for this measure

            first_voice = voices[0]
            second_voice = voices[1]

            # --- Construct Measure for new Staff id="2" ---
            new_measure_for_staff2 = ET.Element('Measure')
            voice_for_staff2 = ET.Element('voice')

            # Add KeySig and TimeSig to the first measure of Staff id="2" as per output example.
            if i == 0:
                voice_for_staff2.append(copy.deepcopy(key_sig_template))
                voice_for_staff2.append(copy.deepcopy(time_sig_template))

            # Handle the <location> tag: In input, <location><fractions>1/2</fractions></location>
            # indicates a mid-measure start. In output, this is represented by a half-note rest.
            location_tag = second_voice.find('location')
            if location_tag is not None:
                fractions = location_tag.find('fractions')
                if fractions is not None and fractions.text == '1/2':
                    half_rest = ET.fromstring('''<Rest><durationType>half</durationType></Rest>''')
                    voice_for_staff2.append(half_rest)
            
            # Append all children of the second voice (from input) to the new voice for Staff id="2".
            # We explicitly exclude the <location> tag itself from being copied.
            for child in second_voice:
                if child.tag != 'location':
                    voice_for_staff2.append(copy.deepcopy(child))

            # Check if the first voice of the current measure has a <BarLine> and copy it
            # to ensure both staffs have end barlines in the final measure.
            barline_in_first_voice = first_voice.find('BarLine')
            if barline_in_first_voice is not None:
                voice_for_staff2.append(copy.deepcopy(barline_in_first_voice))

            # Add the constructed voice to the new measure, and then add the measure to our list.
            new_measure_for_staff2.append(voice_for_staff2)
            measures_to_add_to_staff2.append(new_measure_for_staff2)

            # --- Modify original Staff id="1" ---
            # Remove all voices except the first one from the original measure.
            # Iterate over a copy of the list of voices to safely modify the 'measure' element.
            for j, v in enumerate(list(measure.findall('voice'))):
                if j > 0: # If it's the second voice or any subsequent voice
                    measure.remove(v)

        # After processing all measures, append the prepared measures to new_staff2.
        for measure_elem in measures_to_add_to_staff2:
            new_staff2.append(measure_elem)

        # Insert new_staff2 into the Score element at the correct position.
        # In simple_1_output.xml, <Staff id="2"> comes after <VBox>, which comes after <Staff id="1">.
        # Re-list children of score_element as it might have changed after <Part> insertion.
        score_children_current = list(score_element)
        insert_after_element_index = -1

        # First, try to find the <VBox> element to insert after it.
        vbox_element = None
        for idx, child in enumerate(score_children_current):
            if child.tag == 'VBox':
                vbox_element = child
                insert_after_element_index = idx
                break
        
        # If <VBox> is not found (though it should be present in simple_1_input.xml),
        # fallback to inserting after the original <Staff id="1">.
        if vbox_element is None:
            print("Warning: <VBox> element not found. Attempting to insert new Staff after original Staff id='1'.")
            for idx, child in enumerate(score_children_current):
                # Ensure we find the specific original_staff1 object, not just any Staff id="1" if duplicated.
                if child.tag == 'Staff' and child.get('id') == '1' and child is original_staff1:
                    insert_after_element_index = idx
                    break
            
        if insert_after_element_index == -1:
            print("Error: Could not determine an appropriate insertion point for new <Staff id='2'>.")
            return

        score_element.insert(insert_after_element_index + 1, new_staff2)

        # --- Step 3: Write the modified XML to the output file ---
        # Create an ElementTree object from the modified root element.
        final_tree = ET.ElementTree(root)

        # Add the XML declaration (<?xml version="1.0" encoding="UTF-8"?>)
        # and pretty-print the output for readability with an indent of 2 spaces.
        ET.indent(final_tree, space="  ", level=0)
        final_tree.write(output_path, encoding='UTF-8', xml_declaration=True)

        print(f"Conversion successful! Output saved to {output_path}")

    except FileNotFoundError:
        print(f"Error: Input file not found at {input_path}. Please check the path and try again.")
    except ET.ParseError as e:
        print(f"Error parsing XML file: {e}. Please ensure the input file is valid XML.")
    except Exception as e:
        print(f"An unexpected error occurred during conversion: {e}")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process and modify an MSCX file.")
    parser.add_argument("input", help="Input MSCX file")
    parser.add_argument("output", help="Output MSCX file")
    args = parser.parse_args()

    main(args.input, args.output)