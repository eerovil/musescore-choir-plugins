"""Recorded score edits: the ones the automatic passes refuse to guess at.

Applying is strict on purpose. A fix that silently does not match leaves the score
looking repaired when it is not, and the whole point of recording these is that
nobody can tell by looking at the file whether the judgement was made.
"""
import json
import os
from fractions import Fraction

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.overfull_measures import _voice_len
from src.clean_score.utils.score_fixes import FixError, apply_fixes


def _score(chords):
    """One staff, one measure. `chords` is a list of (durationType, dots, pitch)."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "trackName").text = "B1"
    etree.SubElement(part, "Staff", id="3")
    staff = etree.SubElement(score, "Staff", id="3")
    v = etree.SubElement(etree.SubElement(staff, "Measure"), "voice")
    for dur, dots, pitch in chords:
        c = etree.SubElement(v, "Chord")
        etree.SubElement(c, "durationType").text = dur
        if dots:
            etree.SubElement(c, "dots").text = str(dots)
        etree.SubElement(etree.SubElement(c, "Note"), "pitch").text = str(pitch)
    return root


def test_undot_shortens_the_note_it_names():
    root = _score([("quarter", 1, 60), ("eighth", 0, 62)])
    v = root.find(".//voice")
    assert _voice_len(v) == Fraction(1, 2)
    apply_fixes(root, [{"kind": "undot", "staff": 3, "measure": 1, "index": 0, "why": "x"}])
    assert _voice_len(v) == Fraction(3, 8)


def test_a_slur_makes_the_following_notes_carry_no_syllable():
    """That is the point of recording one: the melisma the OCR lost."""
    root = _score([("quarter", 0, 60)] * 4)
    before = lyric_txt.slot_counts(root)[3][1]
    apply_fixes(root, [{"kind": "slur", "staff": 3, "measure": 1,
                        "index": 0, "span": 2, "why": "x"}])
    after = lyric_txt.slot_counts(root)[3][1]
    assert (before, after) == (4, 2)          # two notes became continuations
    assert _voice_len(root.find(".//voice")) == Fraction(1)   # durations untouched


def test_a_fix_that_does_not_match_is_an_error_not_a_shrug():
    root = _score([("quarter", 0, 60)])
    for bad in (
        {"kind": "undot", "staff": 3, "measure": 1, "index": 0},      # no dot there
        {"kind": "undot", "staff": 9, "measure": 1, "index": 0},      # no such staff
        {"kind": "undot", "staff": 3, "measure": 7, "index": 0},      # no such measure
        {"kind": "undot", "staff": 3, "measure": 1, "index": 5},      # no such chord
        {"kind": "slur", "staff": 3, "measure": 1, "index": 0},       # nothing to slur to
        {"kind": "reharmonise", "staff": 3, "measure": 1, "index": 0},
    ):
        with pytest.raises(FixError):
            apply_fixes(_score([("quarter", 0, 60)]), [dict(bad, why="x")])


FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie", "10-cleaned")


@pytest.mark.skipif(not os.path.exists(os.path.join(FIXTURE, "fixes.json")),
                    reason="prototyping fixture not present")
def test_the_fixtures_recorded_fix_still_applies():
    """It is recorded against a score the pipeline regenerates, so it can go stale.

    If a pipeline change moves that note, this fails rather than the fixture quietly
    keeping a defect.
    """
    with open(os.path.join(FIXTURE, "fixes.json"), encoding="utf-8") as f:
        fixes = json.load(f)
    assert fixes and all(f.get("why") for f in fixes), "every fix explains itself"

    cleaned = os.path.join(FIXTURE, "Virta-venhetta-vie_cleaned.mscx")
    root = etree.parse(cleaned).getroot()
    # The snapshot already has the fix applied, so re-applying must now fail.
    with pytest.raises(FixError):
        apply_fixes(root, fixes)
    staff = [s for s in root.findall(".//Score/Staff") if s.get("id") == "3"][0]
    m26 = staff.findall("Measure")[25]
    v = m26.find("voice") if m26.find("voice") is not None else m26
    assert _voice_len(v) == Fraction(1)       # 4/4, like the other three voices
