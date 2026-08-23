"""Stripping non-musical padding from a measure with a spurious `len`.

The rule is that the pass touches only things that are not music -- a `location`
gap, a trailing rest -- and leaves anything needing a note edit for the health
check. Earlier versions deleted a real note, and then padded a voice to a length the
page does not have; the tests below pin the limits, not just the happy path.
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
        for item in items:
            tag, dur = item[0], item[1]
            el = etree.SubElement(v, tag)
            etree.SubElement(el, "durationType").text = dur
            if len(item) > 2:
                etree.SubElement(el, "dots").text = str(item[2])
            if tag == "Chord":
                etree.SubElement(etree.SubElement(el, "Note"), "pitch").text = "60"
    return root


def _lens(root):
    return [_voice_len(v) for v in root.iter("voice")]


def _timesig(root, n, d):
    for measure in root.iter("Measure"):
        ts = etree.SubElement(measure, "TimeSig")
        etree.SubElement(ts, "sigN").text = str(n)
        etree.SubElement(ts, "sigD").text = str(d)
        break
    return root


def test_a_trailing_rest_that_accounts_for_the_overrun_goes():
    four = [("Chord", "quarter")] * 4
    root = _timesig(_score([four, four, four, four + [("Rest", "eighth")]]), 4, 4)
    assert fix_overfull_measures(root) == 1
    assert _lens(root) == [Fraction(1)] * 4
    assert root.find(".//Measure").get("len") is None


def test_a_location_gap_that_accounts_for_the_overrun_goes():
    four = [("Chord", "quarter")] * 4
    root = _timesig(_score([four, four, four, four]), 4, 4)
    voice = list(root.iter("voice"))[-1]
    loc = etree.SubElement(voice, "location")
    etree.SubElement(loc, "fractions").text = "1/8"
    assert _voice_len(voice) == Fraction(9, 8)          # the gap counts
    fix_overfull_measures(root)
    assert _voice_len(voice) == Fraction(1)


def test_a_voice_needing_a_note_edit_is_left_for_the_health_check():
    """The reference case: one voice overruns because of a dot the OCR invented.

    Shortening a note is a musical judgement, so the pass declines and the measure
    keeps a voice the health check will flag.
    """
    four = [("Chord", "quarter")] * 4
    dotted = [("Chord", "quarter", 1)] + [("Chord", "quarter")] * 3
    root = _timesig(_score([four, four, four, dotted]), 4, 4)
    before = [(c.findtext("durationType"), c.findtext("dots")) for c in root.iter("Chord")]
    fix_overfull_measures(root)
    after = [(c.findtext("durationType"), c.findtext("dots")) for c in root.iter("Chord")]
    assert after == before                              # every note untouched
    assert Fraction(9, 8) in _lens(root)


def test_notes_are_never_removed_or_shortened():
    four = [("Chord", "quarter")] * 4
    five = [("Chord", "quarter")] * 5
    root = _timesig(_score([four, four, four, five]), 4, 4)
    before = len(list(root.iter("Chord")))
    fix_overfull_measures(root)
    assert len(list(root.iter("Chord"))) == before


def test_nothing_happens_without_a_voice_that_fills_the_meter():
    """No witness, no evidence the override is wrong — so the bar really is 9/8.

    Every voice here ends with a rest the pass *could* strip. It must not: when
    they all agree on 9/8 and none fills the meter, the odd length is the truth
    and the rests are holding real time.
    """
    nine = [("Chord", "quarter")] * 4 + [("Rest", "eighth")]
    root = _timesig(_score([nine, nine, nine]), 4, 4)
    assert fix_overfull_measures(root) == 0
    assert _lens(root) == [Fraction(9, 8)] * 3          # rests kept
    assert root.find(".//Measure").get("len") == "9/8"


def test_measures_without_a_len_override_are_not_touched():
    four = [("Chord", "quarter")] * 4
    root = _timesig(_score([four, four, four, four + [("Rest", "eighth")]], length=None), 4, 4)
    assert fix_overfull_measures(root) == 0


FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie", "20-lyrics", "Virta-venhetta-vie_cleaned.mscx",
)


@pytest.mark.skipif(not os.path.exists(FIXTURE), reason="prototyping fixture not present")
def test_the_reference_measure():
    """m26: a 4/4 bar the OCR gave a bogus len="9/8" and three kinds of padding.

    Three voices come out at 4/4; the fourth keeps the dot that the OCR invented,
    because removing it is a musical judgement, and keeps all six of its notes --
    one per syllable the page prints for it.
    """
    root = etree.parse(FIXTURE).getroot()
    staves = {s.get("id"): s for s in root.findall(".//Score/Staff")
              if s.find("Measure") is not None}
    lengths = {}
    for sid, staff in staves.items():
        m = staff.findall("Measure")[25]
        assert m.get("len") is None                     # override gone
        v = m.find("voice") if m.find("voice") is not None else m
        lengths[sid] = _voice_len(v)
    assert lengths["1"] == lengths["2"] == lengths["4"] == Fraction(1)
    assert lengths["3"] == Fraction(9, 8)               # the dot, still there
    b1 = staves["3"].findall("Measure")[25]
    assert len(b1.findall(".//Chord")) == 6             # six notes, six syllables
