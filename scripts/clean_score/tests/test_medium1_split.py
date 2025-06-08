# Test for correct note/rest order and time signatures in medium_1_input.musicxml
import os
from music21 import converter, meter
from scripts.clean_score.clean_score import main

def get_types(measure):
    return [el.__class__.__name__ for el in measure.notesAndRests]

def get_time_signature(measure):
    ts = measure.getTimeSignatures()
    if ts:
        return ts[0].ratioString
    return None

def test_medium1_split():
    input_path = os.path.join(os.path.dirname(__file__), 'medium_1', 'medium_1_input.musicxml')
    score = converter.parse(input_path)
    split_score = main(score)
    parts = list(split_score.parts)
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    # Find bottom part (should be 'down' or similar)
    bottom = [p for p in parts if 'down' in p.id or 'Down' in p.id or '2' in p.id][0]
    measures = list(bottom.getElementsByClass('Measure'))
    assert len(measures) >= 4, f"Expected at least 4 measures, got {len(measures)}"
    # Check note/rest order for each measure
    assert get_types(measures[0]) == ['Note', 'Rest', 'Note'], f"M1: {get_types(measures[0])}"
    assert get_types(measures[1]) == ['Note', 'Rest'], f"M2: {get_types(measures[1])}"
    assert get_types(measures[2]) == ['Note', 'Note'], f"M3: {get_types(measures[2])}"
    assert get_types(measures[3]) == ['Note'], f"M4: {get_types(measures[3])}"
    # Check time signatures
    assert get_time_signature(measures[0]) == '3/4', f"M1 time sig: {get_time_signature(measures[0])}"
    assert get_time_signature(measures[2]) == '2/4', f"M3 time sig: {get_time_signature(measures[2])}"


def extract_lyrics(part):
    # Get all lyrics from all notes in all measures, in order
    lyrics = []
    for n in part.recurse().notes:
        if n.lyrics:
            for l in n.lyrics:
                if l.text:
                    lyrics.append(l.text.strip())
    return ' '.join(lyrics)


def test_medium1_lyric_split():
    input_path = os.path.join(os.path.dirname(__file__), 'medium_1', 'medium_1_input.musicxml')
    score = converter.parse(input_path)
    split_score = main(score)
    parts = list(split_score.parts)
    assert len(parts) == 2, f"Expected 2 parts, got {len(parts)}"
    # Sort by id for deterministic order
    parts.sort(key=lambda p: p.id)
    lyrics0 = extract_lyrics(parts[0])
    lyrics1 = extract_lyrics(parts[1])
    print(f"DEBUG: Part {parts[0].id} lyrics: {lyrics0}")
    print(f"DEBUG: Part {parts[1].id} lyrics: {lyrics1}")
    assert lyrics0 == 'Hei o len La u lu nen jee!', f"Part {parts[0].id} lyrics: {lyrics0}"
    assert lyrics1 == 'Hei o len Lau lu jee!', f"Part {parts[1].id} lyrics: {lyrics1}"
