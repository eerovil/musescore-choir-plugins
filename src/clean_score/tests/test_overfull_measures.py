"""Padding a voice that falls short of the length its measure agrees on.

The rule this pass exists to enforce is that it *only adds rests*. An earlier
version deleted things that looked like OCR junk and destroyed a real note on the
reference score, so the tests below pin the additive behaviour rather than just the
happy path.
"""
import os
from fractions import Fraction

import pytest
from lxml import etree

from src.clean_score.utils.overfull_measures import _voice_len, fix_overfull_measures


def _score(voices, length="9/8"):
    """A one-measure score. `voices` is a list of lists of (tag, durationType)."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for i, items in enumerate(voices, start=1):
        staff = etree.SubElement(score, "Staff", id=str(i))
        measure = etree.SubElement(staff, "Measure")
        if length:
            measure.set("len", length)
        v = etree.SubElement(measure, "voice")
        for tag, dur in items:
            el = etree.SubElement(v, tag)
            etree.SubElement(el, "durationType").text = dur
            if tag == "Chord":
                etree.SubElement(etree.SubElement(el, "Note"), "pitch").text = "60"
    return root


def _lens(root):
    return [_voice_len(v) for v in root.iter("voice")]


def test_the_odd_voice_out_is_padded_to_match_the_others():
    """Three voices read as 9/8 and one as 8/8: the short one is wrong."""
    nine = [("Chord", "quarter")] * 4 + [("Chord", "eighth")]      # 9/8
    eight = [("Chord", "quarter")] * 4                             # 8/8
    root = _score([nine, nine, nine, eight])
    assert fix_overfull_measures(root) == 1
    assert _lens(root) == [Fraction(9, 8)] * 4


def test_nothing_is_ever_removed():
    """A voice longer than the agreement keeps every note it had."""
    eight = [("Chord", "quarter")] * 4
    nine = eight + [("Chord", "eighth")]
    root = _score([eight, eight, eight, nine])
    before = len(list(root.iter("Chord")))
    fix_overfull_measures(root)
    assert len(list(root.iter("Chord"))) == before
    assert Fraction(9, 8) in _lens(root)              # the long one is untouched


def test_voices_that_do_not_agree_are_left_alone():
    """No majority, no truth to pad towards."""
    a = [("Chord", "quarter")]
    b = [("Chord", "quarter"), ("Chord", "quarter")]
    c = [("Chord", "quarter"), ("Chord", "quarter"), ("Chord", "quarter")]
    root = _score([a, b, c])
    assert fix_overfull_measures(root) == 0
    assert _lens(root) == [Fraction(1, 4), Fraction(1, 2), Fraction(3, 4)]


def test_measures_without_a_len_override_are_not_touched():
    """The pass is for measures MuseScore already marked as irregular."""
    nine = [("Chord", "quarter")] * 4 + [("Chord", "eighth")]
    eight = [("Chord", "quarter")] * 4
    root = _score([nine, nine, nine, eight], length=None)
    assert fix_overfull_measures(root) == 0


def test_a_location_gap_counts_towards_the_length():
    """A voice can be complete on its notes and still occupy more of the bar.

    Missing this was what made an earlier version delete a real note.
    """
    root = _score([[("Chord", "quarter")]])
    voice = root.find(".//voice")
    loc = etree.SubElement(voice, "location")
    etree.SubElement(loc, "fractions").text = "1/8"
    assert _voice_len(voice) == Fraction(3, 8)


FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie", "20-lyrics", "Virta-venhetta-vie_cleaned.mscx",
)


@pytest.mark.skipif(not os.path.exists(FIXTURE), reason="prototyping fixture not present")
def test_the_reference_measure_comes_out_well_formed():
    """m26 of the fixture: a real 9/8 bar the OCR left one voice short of.

    All four voices must fill it, and the six notes the upper bass sings -- one
    per printed syllable -- must all still be there.
    """
    root = etree.parse(FIXTURE).getroot()
    staves = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None]
    lengths, bass_notes = [], 0
    for staff in staves:
        m = staff.findall("Measure")[25]
        for v in (m.findall("voice") or [m]):
            lengths.append(_voice_len(v))
        if staff.get("id") == "3":
            bass_notes = len(m.findall(".//Chord"))
    assert lengths == [Fraction(9, 8)] * 4
    assert bass_notes == 6
