# Test that splitting lyric_1_input.mscx produces two parts with correct lyrics
import os
from lxml import etree
from scripts.clean_score.clean_score import main
import pytest
from scripts.clean_score.tests.test_simple1_split import BaseScoreTest

def extract_lyrics(part):
    # Get all lyrics from all notes in all measures, in order
    lyrics = []
    for note in part.findall('.//Note'):
        for lyric in note.findall('lyric'):
            text_el = lyric.find('text')
            if text_el is not None:
                lyrics.append(text_el.text.strip())
    return ' '.join(lyrics)

def test_lyric1_split(setup_and_teardown):
    class TestLyric1(BaseScoreTest):
        folder = 'lyric_1'
    t = TestLyric1()
    t.run_and_save()
    tree = t.get_output_tree()
    root = tree.getroot()
    score = root.find('Score')
    parts = score.findall('Part')
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    for part in parts:
        lyrics = extract_lyrics(part)
        assert lyrics == 'Hei o len lau lu.', f"Part {part.attrib.get('id')} lyrics: {lyrics}"
