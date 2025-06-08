# Test that splitting simple_1_input.musicxml produces two parts with correct note/rest counts
import os
from music21 import converter
from scripts.clean_score.clean_score import main

def test_simple1_split():
    input_path = os.path.join(os.path.dirname(__file__), 'simple_1', 'simple_1_input.musicxml')
    score = converter.parse(input_path)
    split_score = main(score)
    parts = list(split_score.parts)
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    for part in parts:
        notes = [n for n in part.recurse().notes]
        rests = [r for r in part.recurse().getElementsByClass('Rest')]
        assert len(notes) == 6, f"Part {part.id} should have 6 notes, got {len(notes)}"
        assert len(rests) == 2, f"Part {part.id} should have 2 rests, got {len(rests)}"
