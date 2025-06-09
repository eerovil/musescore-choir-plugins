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

    # Only modify <Part> elements, preserve the rest of the MuseScore structure
    # Find the <Score> element
    score_elem = root.find(".//Score")
    if score_elem is None:
        print("No <Score> element found in input file.")
        sys.exit(1)

    # --- Explicitly rebuild <Score> children in correct order ---
    import copy
    children = list(score_elem)
    # Find indices for first and last <Part>
    part_indices = [i for i, c in enumerate(children) if c.tag == 'Part']
    if part_indices:
        first_part_idx = part_indices[0]
        last_part_idx = part_indices[-1]
    else:
        first_part_idx = len(children)
        last_part_idx = len(children) - 1
    # Prepare new staffs and parts
    staffs = [c for c in children if c.tag == 'Staff']
    parts = [c for c in children if c.tag == 'Part']
    new_staffs = []
    if staffs:
        staff1 = copy.deepcopy(staffs[0])
        staff2 = copy.deepcopy(staffs[0])
        staff1.attrib['id'] = '1'
        staff2.attrib['id'] = '2'
        new_staffs = [staff1, staff2]
    new_parts = []
    for part in parts:
        part1 = copy.deepcopy(part)
        part2 = copy.deepcopy(part)
        new_parts.extend([part1, part2])
    for idx, part in enumerate(new_parts, 1):
        part.attrib['id'] = str(idx)
    # Build new children list
    new_children = []
    new_children.extend(children[:first_part_idx])
    new_children.extend(new_staffs)
    new_children.extend(new_parts)
    new_children.extend(children[last_part_idx+1:])
    # Remove all children and set new ones
    for c in list(score_elem):
        score_elem.remove(c)
    for c in new_children:
        if c.tag not in ('Part', 'Staff') or c in new_staffs or c in new_parts:
            score_elem.append(c)

    # Remove comments and blank text nodes from the root before writing
    for elem in root.xpath('//comment()'):
        parent = elem.getparent()
        if parent is not None:
            parent.remove(elem)
    def remove_blank_text(e):
        # Remove blank text and tail recursively
        if e.text is not None and e.text.strip() == '':
            e.text = None
        if e.tail is not None and e.tail.strip() == '':
            e.tail = None
        for c in e:
            remove_blank_text(c)
    remove_blank_text(root)

    # Remove all <Part> and all top-level <Staff> from <Score>
    for elem in list(score_elem):
        if elem.tag == 'Part' or elem.tag == 'Staff':
            score_elem.remove(elem)
    # Duplicate only <Part> elements (with their internal <Staff>), set id attributes
    import copy
    if parts:
        part1 = copy.deepcopy(parts[0])
        part2 = copy.deepcopy(parts[0])
        part1.attrib['id'] = '1'
        part2.attrib['id'] = '2'
        # Find the last <metaTag> index for insertion
        children = list(score_elem)
        insert_idx = 0
        for i, child in enumerate(children):
            if child.tag == 'metaTag':
                insert_idx = i + 1
        if insert_idx == 0:
            for i, child in enumerate(children):
                if child.tag == 'showMargins':
                    insert_idx = i + 1
        score_elem.insert(insert_idx, part1)
        score_elem.insert(insert_idx + 1, part2)

    # Write the modified tree back, preserving the MuseScore structure
    xml_bytes = etree.tostring(root, pretty_print=True, encoding='UTF-8', xml_declaration=False)
    xml_str = xml_bytes.decode('utf-8')
    # Ensure trailing newline to match expected output
    if not xml_str.endswith('\n'):
        xml_str += '\n'
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(xml_str)
    

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