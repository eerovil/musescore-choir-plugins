# Test for correct note/rest order and time signatures in medium_1_input.musicxml
import os
from lxml import etree
from scripts.clean_score.clean_score import main

def get_types(measure):
    return ['Rest' if n.find('rest') is not None else 'Note' for n in measure.findall('note')]

def extract_lyrics(part):
    lyrics = []
    for note in part.findall('.//note'):
        for lyric in note.findall('lyric'):
            text_el = lyric.find('text')
            if text_el is not None:
                lyrics.append(text_el.text.strip())
    return ' '.join(lyrics)

def test_medium1_split():
    input_path = os.path.join(os.path.dirname(__file__), 'medium_1', 'medium_1_input.musicxml')
    tree = etree.parse(input_path)
    root = tree.getroot()
    main(root)
    parts = root.findall('part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    # Find bottom part (should be 'down' or similar)
    bottom = [p for p in parts if 'down' in p.attrib.get('id', '').lower() or '2' in p.attrib.get('id', '').lower()][0]
    measures = bottom.findall('measure')
    assert len(measures) >= 4, f"Expected at least 4 measures, got {len(measures)}"
    # Check note/rest order for each measure
    assert get_types(measures[0]) == ['Note', 'Rest', 'Note'], f"M1: {get_types(measures[0])}"
    assert get_types(measures[1]) == ['Note', 'Rest'], f"M2: {get_types(measures[1])}"
    assert get_types(measures[2]) == ['Note', 'Note'], f"M3: {get_types(measures[2])}"
    assert get_types(measures[3]) == ['Note'], f"M4: {get_types(measures[3])}"

def test_medium1_lyric_split():
    input_path = os.path.join(os.path.dirname(__file__), 'medium_1', 'medium_1_input.musicxml')
    tree = etree.parse(input_path)
    root = tree.getroot()
    main(root)
    parts = root.findall('part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    parts.sort(key=lambda p: p.attrib.get('id'))
    lyrics0 = extract_lyrics(parts[0])
    lyrics1 = extract_lyrics(parts[1])
    print(f"DEBUG: Part {parts[0].attrib.get('id')} lyrics: {lyrics0}")
    print(f"DEBUG: Part {parts[1].attrib.get('id')} lyrics: {lyrics1}")
    assert lyrics0 == 'Hei o len La u lu nen jee!', f"Part {parts[0].attrib.get('id')} lyrics: {lyrics0}"
    assert lyrics1 == 'Hei o La len u Lau lu nen lu jee!', f"Part {parts[1].attrib.get('id')} lyrics: {lyrics1}"
