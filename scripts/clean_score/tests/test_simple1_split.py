# Test that splitting simple_1_input.musicxml produces two parts with correct note/rest counts
import os
from lxml import etree
from scripts.clean_score.clean_score import main

def test_simple1_split():
    input_path = os.path.join(os.path.dirname(__file__), 'simple_1', 'simple_1_input.musicxml')
    tree = etree.parse(input_path)
    root = tree.getroot()
    main(root)
    parts = root.findall('part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    for part in parts:
        notes = [n for n in part.findall('.//note') if n.find('rest') is None]
        rests = [r for r in part.findall('.//note') if r.find('rest') is not None]
        assert len(notes) == 6, f"Part {part.attrib.get('id')} should have 6 notes, got {len(notes)}"
        assert len(rests) == 2, f"Part {part.attrib.get('id')} should have 2 rests, got {len(rests)}"
        measures = part.findall('measure')
        assert len(measures) == 2, f"Part {part.attrib.get('id')} should have 2 measures, got {len(measures)}"
        m1 = measures[0]
        m2 = measures[1]
        m1_types = ['Rest' if el.find('rest') is not None else 'Note' for el in m1.findall('note')]
        m2_types = ['Rest' if el.find('rest') is not None else 'Note' for el in m2.findall('note')]
        print(f"DEBUG {part.attrib.get('id')} M1: {m1_types}")
        print(f"DEBUG {part.attrib.get('id')} M2: {m2_types}")
        assert m1_types == ['Note', 'Rest', 'Note', 'Note'], f"Part {part.attrib.get('id')} M1 order: {m1_types}"
        assert m2_types == ['Rest', 'Note', 'Note', 'Note'], f"Part {part.attrib.get('id')} M2 order: {m2_types}"
