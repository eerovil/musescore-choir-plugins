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
    # Prepare new <Part> elements (just as in input, but duplicated)
    parts = [c for c in children if c.tag == 'Part']
    new_parts = []
    for idx, part in enumerate(parts):
        part1 = copy.deepcopy(part)
        part2 = copy.deepcopy(part)
        # For each, keep only the <StaffType> in <Staff> (remove measures)
        for p, staff_id in zip([part1, part2], ['1', '2']):
            staff = p.find('./Staff')
            if staff is not None:
                # Remove everything except StaffType
                for elem in list(staff):
                    if elem.tag != 'StaffType':
                        staff.remove(elem)
                staff.attrib['id'] = staff_id
            p.attrib['id'] = staff_id
        new_parts.extend([part1, part2])
    # Prepare new <Staff> elements (top-level, with measures split by voice)
    orig_staff = None
    for c in children:
        if c.tag == 'Staff':
            orig_staff = c
            break
    new_staffs = []
    if orig_staff is not None:
        # For staff 1 (voice 1)
        staff1 = copy.deepcopy(orig_staff)
        staff1.attrib['id'] = '1'
        # Remove all measures
        for elem in list(staff1):
            if elem.tag == 'Measure':
                staff1.remove(elem)
        # For each measure in original, keep only first <voice>
        for measure in [m for m in orig_staff if m.tag == 'Measure']:
            m1 = copy.deepcopy(measure)
            voices = [v for v in list(m1) if v.tag == 'voice']
            for v in voices[1:]:
                m1.remove(v)
            staff1.append(m1)
        # For staff 2 (voice 2)
        staff2 = copy.deepcopy(orig_staff)
        staff2.attrib['id'] = '2'
        for elem in list(staff2):
            if elem.tag == 'Measure':
                staff2.remove(elem)
        for measure in [m for m in orig_staff if m.tag == 'Measure']:
            m2 = copy.deepcopy(measure)
            voices = [v for v in list(m2) if v.tag == 'voice']
            for v in voices[:1] + voices[2:]:
                m2.remove(v)
            staff2.append(m2)
        new_staffs = [staff1, staff2]
    # Build new children list for <Score>
    new_children = []
    new_children.extend(children[:first_part_idx])
    new_children.extend(new_parts)
    new_children.extend(new_staffs)
    new_children.extend(children[last_part_idx+1:])
    # Remove all children and set new ones
    for c in list(score_elem):
        score_elem.remove(c)
    for c in new_children:
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

    # Find and store the original top-level <Staff> before any removals
    orig_top_staff = None
    for c in children:
        if c.tag == 'Staff':
            orig_top_staff = c
            break
    # Remove all <Part> and all top-level <Staff> from <Score> before inserting new ones
    for elem in list(score_elem):
        if elem.tag == 'Part' or elem.tag == 'Staff':
            score_elem.remove(elem)
    # Insert <Part> elements after <metaTag> or <showMargins>
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
    # Build new top-level <Staff> elements for each part using the saved original top-level <Staff>
    if orig_top_staff is not None:
        orig_staff_type = orig_top_staff.find('StaffType')
        vbox = orig_top_staff.find('VBox')
        orig_measures = [m for m in list(orig_top_staff) if m.tag == 'Measure']
        for idx, voice_idx in [(1, 0), (2, 1)]:
            new_staff = etree.Element('Staff', id=str(idx))
            if orig_staff_type is not None:
                new_staff.append(copy.deepcopy(orig_staff_type))
            if vbox is not None:
                new_staff.append(copy.deepcopy(vbox))
            for measure in orig_measures:
                m_copy = copy.deepcopy(measure)
                voices = [v for v in list(m_copy) if v.tag == 'voice']
                # Remove all voices except the one for this staff
                for i, v in enumerate(voices):
                    if i != voice_idx:
                        m_copy.remove(v)
                # If the measure has at least one <voice>, append it
                if m_copy.find('voice') is not None:
                    new_staff.append(m_copy)
            score_elem.insert(insert_idx + 2 + (idx - 1), new_staff)

    # Write the modified MuseScore XML to the output file
    try:
        tree.write(output_file, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    except Exception as e:
        print(f"Error writing output file: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process and modify an MSCX file.")
    parser.add_argument("input", help="Input MSCX file")
    parser.add_argument("output", help="Output MSCX file")
    args = parser.parse_args()

    main(args.input, args.output)