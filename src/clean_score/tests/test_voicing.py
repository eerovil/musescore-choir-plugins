"""Naming the parts from the voicing instead of guessing from pitch.

The reference case: a Sibelius male-choir score whose treble staff is an octave
clef the engraver did not mark. Its tenor line reads as 66-82 -- squarely soprano
-- so clef and range cannot possibly get it right. Told the score is for men, the
answer is forced, and the notes are moved down to where they actually sound.
"""
from lxml import etree

import pytest

from src.clean_score.utils.part_types import detect_part_types


def _score(staves):
    """`staves` is a list of (clef, [pitches])."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for i, (clef_type, pitches) in enumerate(staves, start=1):
        staff = etree.SubElement(score, "Staff", id=str(i))
        measure = etree.SubElement(staff, "Measure")
        if clef_type:
            etree.SubElement(etree.SubElement(measure, "Clef"),
                             "concertClefType").text = clef_type
        for p in pitches:
            chord = etree.SubElement(measure, "Chord")
            etree.SubElement(etree.SubElement(chord, "Note"), "pitch").text = str(p)
    return root


def _named(info):
    return [f"{v['part_name'][0]}{v['part_index']}" for v in info.values()]


# A treble staff in the range an unmarked octave clef produces, and a bass staff.
TREBLE = ("G", [66, 74, 82])
BASS = ("F", [40, 50, 60])


def test_men_makes_every_treble_staff_a_tenor_in_an_octave_clef():
    info = detect_part_types(_score([TREBLE, TREBLE, BASS, BASS]), "men")
    assert _named(info) == ["T1", "T2", "B1", "B2"]
    assert [v["clef_type"] for v in info.values()] == ["G8vb", "G8vb", "F", "F"]
    assert [v["octave_down"] for v in info.values()] == [True, True, False, False]


def test_a_staff_already_marked_as_an_octave_clef_is_not_moved_again():
    """Its pitches are already where they sound."""
    info = detect_part_types(_score([("G8vb", [54, 62, 70]), BASS]), "men")
    assert _named(info) == ["T1", "B1"]
    assert [v["octave_down"] for v in info.values()] == [False, False]


def test_mixed_splits_each_clef_into_the_two_voices_it_carries():
    info = detect_part_types(_score([TREBLE, TREBLE, BASS, BASS]), "mixed")
    assert _named(info) == ["S1", "A1", "T1", "B1"]
    assert not any(v["octave_down"] for v in info.values())


def test_women_splits_the_treble_staves():
    info = detect_part_types(_score([TREBLE, TREBLE, TREBLE, TREBLE]), "women")
    assert _named(info) == ["S1", "S2", "A1", "A2"]


def test_a_staff_with_no_notes_is_not_given_a_part():
    """The recording spacer is one; counting it would shift the split."""
    info = detect_part_types(_score([TREBLE, TREBLE, BASS, BASS, ("", [])]), "mixed")
    assert _named(info) == ["S1", "A1", "T1", "B1"]


def test_without_a_voicing_the_old_guess_still_runs():
    """Existing songs have no voicing recorded, and must clean as they did."""
    info = detect_part_types(_score([TREBLE, TREBLE, BASS, BASS]))
    assert all(v.get("part_name") for v in info.values())
    assert "octave_down" not in info[1]        # the guess never moves anything
