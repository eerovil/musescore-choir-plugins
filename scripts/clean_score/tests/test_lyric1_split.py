# Test that splitting lyric_1_input.musicxml produces two parts with correct lyrics
import os
from lxml import etree
from scripts.clean_score.clean_score import main

def extract_lyrics(part):
    # Get all lyrics from all notes in all measures, in order
    lyrics = []
    for note in part.findall('.//note'):
        for lyric in note.findall('lyric'):
            text_el = lyric.find('text')
            if text_el is not None:
                lyrics.append(text_el.text.strip())
    return ' '.join(lyrics)

def test_lyric1_split():
    input_path = os.path.join(os.path.dirname(__file__), 'lyric_1', 'lyric_1_input.musicxml')
    tree = etree.parse(input_path)
    root = tree.getroot()
    main(root)
    parts = root.findall('part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    for part in parts:
        lyrics = extract_lyrics(part)
        assert lyrics == 'Hei o len lau lu.', f"Part {part.attrib.get('id')} lyrics: {lyrics}"
