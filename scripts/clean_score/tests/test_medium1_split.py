# Test for correct note/rest order and time signatures in medium_1_input.mscx
import os
from lxml import etree
from scripts.clean_score.clean_score import main
import pytest
from scripts.clean_score.tests.test_simple1_split import BaseScoreTest

def get_types(measure):
    return ['Rest' if n.tag == 'Rest' else 'Note' for n in measure.iterchildren() if n.tag in ('Note', 'Rest')]

def extract_lyrics(part):
    lyrics = []
    for note in part.findall('.//Note'):
        for lyric in note.findall('lyric'):
            text_el = lyric.find('text')
            if text_el is not None:
                lyrics.append(text_el.text.strip())
    return ' '.join(lyrics)

def test_medium1_split(setup_and_teardown):
    class TestMedium1(BaseScoreTest):
        folder = 'medium_1'
    t = TestMedium1()
    t.run_and_save()
    tree = t.get_output_tree()
    root = tree.getroot()
    score = root.find('Score')
    parts = score.findall('Part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    # Find bottom part (should be 'down' or similar)
    bottom = [p for p in parts if 'down' in p.attrib.get('id', '').lower() or '2' in p.attrib.get('id', '').lower()][0]
    measures = bottom.findall('.//Measure')
    assert len(measures) >= 4, f"Expected at least 4 measures, got {len(measures)}"
    # Check note/rest order for each measure
    assert get_types(measures[0]) == ['Note', 'Rest', 'Note'], f"M1: {get_types(measures[0])}"
    assert get_types(measures[1]) == ['Note', 'Rest'], f"M2: {get_types(measures[1])}"
    assert get_types(measures[2]) == ['Note', 'Note'], f"M3: {get_types(measures[2])}"
    assert get_types(measures[3]) == ['Note'], f"M4: {get_types(measures[3])}"

def test_medium1_lyric_split(setup_and_teardown):
    class TestMedium1(BaseScoreTest):
        folder = 'medium_1'
    t = TestMedium1()
    t.run_and_save()
    tree = t.get_output_tree()
    root = tree.getroot()
    score = root.find('Score')
    parts = score.findall('Part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    parts.sort(key=lambda p: p.attrib.get('id'))
    lyrics0 = extract_lyrics(parts[0])
    lyrics1 = extract_lyrics(parts[1])
    print(f"DEBUG: Part {parts[0].attrib.get('id')} lyrics: {lyrics0}")
    print(f"DEBUG: Part {parts[1].attrib.get('id')} lyrics: {lyrics1}")
    assert lyrics0 == 'Hei o len La u lu nen jee!', f"Part {parts[0].attrib.get('id')} lyrics: {lyrics0}"
    assert lyrics1 == 'Hei o La len u Lau lu nen lu jee!', f"Part {parts[1].attrib.get('id')} lyrics: {lyrics1}"
