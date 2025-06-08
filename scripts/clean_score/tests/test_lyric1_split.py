# Test that splitting lyric_1_input.musicxml produces two parts with correct lyrics
import os
from music21 import converter
from scripts.clean_score.clean_score import main

def extract_lyrics(part):
    # Get all lyrics from all notes in all measures, in order
    lyrics = []
    for n in part.recurse().notes:
        if n.lyrics:
            for l in n.lyrics:
                if l.text:
                    lyrics.append(l.text.strip())
    return ' '.join(lyrics)

def test_lyric1_split():
    input_path = os.path.join(os.path.dirname(__file__), 'lyric_1', 'lyric_1_input.musicxml')
    score = converter.parse(input_path)
    split_score = main(score)
    parts = list(split_score.parts)
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    for part in parts:
        lyrics = extract_lyrics(part)
        assert lyrics == 'Hei o len lau lu.', f"Part {part.id} lyrics: {lyrics}"
