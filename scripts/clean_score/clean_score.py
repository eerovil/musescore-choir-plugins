#!/usr/bin/env python3

from lxml import etree
import argparse
import sys

def main(input_file, output_file):
    # Parse the input MSCX file
    try:
        tree = etree.parse(input_file)
        root = tree.getroot()
    except etree.XMLSyntaxError as e:
        print(f"Error parsing XML: {e}")
        sys.exit(1)

    # Create a new root for the output MSCX
    new_root = etree.Element("score-partwise")

    # Iterate through each part in the input file
    for part in root.findall(".//part"):
        part_id = part.get("id")
        new_part = etree.SubElement(new_root, "part", id=part_id)

        # Copy all elements from the original part to the new part
        for element in part:
            new_part.append(element)

    # Write the transformed score to a new MSCX file
    tree = etree.ElementTree(new_root)
    tree.write(output_file, pretty_print=True, xml_declaration=True, encoding='UTF-8')
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split mscx score into separate voice parts using music21 and stem direction heuristic.")

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

    main(input_file=INPUT_FILE, output_file=OUTPUT_FILE)
    print("Done.")