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
from src.clean_score.utils.score_fixes import FixError, apply_fixes, free_text


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


# --------------------------------------------------------------------------- #
# Writing a bar: the edits undot and slur cannot express
# --------------------------------------------------------------------------- #

def _tokens(root):
    from src.clean_score.utils.score_fixes import _bar_tokens
    return _bar_tokens(root.find(".//Measure"))


def test_append_leaves_the_start_of_the_bar_alone():
    """What the tokens cannot say — a triplet bracket, a tie — has to survive."""
    root = _score([("quarter", 0, 60)])
    voice = root.find(".//voice")
    etree.SubElement(voice, "Tuplet")
    apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                        "from": ["quarter:60", "[tuplet"], "add": ["quarter:57"],
                        "why": "x"}])
    assert _tokens(root) == ["quarter:60", "[tuplet", "quarter:57"]
    assert voice.find("Tuplet") is not None


def test_a_triplet_bracket_is_part_of_what_the_bar_reads():
    """Two bars differing only by a bracket are different bars; the fix must not apply."""
    root = _score([("quarter", 0, 60)])
    etree.SubElement(root.find(".//voice"), "Tuplet")
    with pytest.raises(FixError):
        apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                            "from": ["quarter:60"], "add": ["quarter:57"], "why": "x"}])


def test_append_can_drop_the_padding_the_scan_left():
    """A scan that loses notes pads with a rest; appending alone overfills the bar."""
    root = _score([("quarter", 0, 60)])
    etree.SubElement(etree.SubElement(root.find(".//voice"), "Rest"),
                     "durationType").text = "half"
    apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                        "from": ["quarter:60", "half:R"], "drop": 1,
                        "add": ["quarter:59", "quarter:57"], "why": "x"}])
    assert _tokens(root) == ["quarter:60", "quarter:59", "quarter:57"]


def test_drop_refuses_to_take_a_note_off():
    """Removing a note is a musical decision, and can strand a tie or a bracket."""
    root = _score([("quarter", 0, 60), ("quarter", 0, 62)])
    with pytest.raises(FixError):
        apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                            "from": ["quarter:60", "quarter:62"], "drop": 1,
                            "add": ["quarter:59"], "why": "x"}])
    assert _tokens(root) == ["quarter:60", "quarter:62"]


def test_a_bar_that_no_longer_reads_as_recorded_raises():
    """A pipeline change that moves this bar must fail the build, not overwrite it."""
    root = _score([("quarter", 0, 60)])
    with pytest.raises(FixError) as caught:
        apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                            "from": ["half:60"], "add": ["quarter:60"], "why": "x"}])
    assert "staff 3 m1" in str(caught.value)      # says which entry, not just what
    assert _tokens(root) == ["quarter:60"]        # and nothing was written


def test_the_spelling_is_derived_from_the_pitch():
    """Hand-written spellings were wrong three times out of four, so there is no field."""
    root = _score([("quarter", 0, 60)])
    apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                        "from": ["quarter:60"],
                        "add": ["quarter:57", "quarter:59"], "why": "x"}])
    tpcs = [n.findtext("tpc") for n in root.findall(".//Note")]
    assert tpcs[1:] == ["17", "19"]               # A and B, not G and A


def test_a_whole_bar_rest_can_be_read_but_not_written():
    """It needs the bar's own length, which a recorded fix has no way to know."""
    root = _score([])
    etree.SubElement(etree.SubElement(root.find(".//voice"), "Rest"),
                     "durationType").text = "measure"
    with pytest.raises(FixError):
        apply_fixes(root, [{"kind": "append", "staff": 3, "measure": 1,
                            "from": ["measure:R"], "add": ["measure:R"], "why": "x"}])


# --------------------------------------------------------------------------- #
# A fix that is just a sentence
# --------------------------------------------------------------------------- #

def test_a_sentence_is_recorded_but_not_carried_out():
    """Most edits are none of the three kinds; the judgement still has to survive."""
    root = _score([("quarter", 0, 60)])
    said = "B1 bar 40, last eighth: drop the D, keep the C — the basses cross here."
    lines = apply_fixes(root, [{"kind": "text", "what": said}])
    assert lines == []                                  # nothing claims to have run
    assert _tokens(root) == ["quarter:60"]              # and the score is untouched
    assert free_text([{"kind": "text", "what": said}]) == [said]


def test_a_sentence_does_not_stop_the_fixes_around_it():
    root = _score([("quarter", 1, 60)])
    lines = apply_fixes(root, [
        {"kind": "text", "what": "bars 8 and 26 should be whole-bar rests"},
        {"kind": "undot", "staff": 3, "measure": 1, "index": 0, "why": "x"},
    ])
    assert len(lines) == 1
    assert _voice_len(root.find(".//voice")) == Fraction(1, 4)


def test_a_sentence_that_says_nothing_is_an_error():
    """An empty reminder and a repaired score look identical from here."""
    for empty in ({"kind": "text"}, {"kind": "text", "what": "   "}):
        with pytest.raises(FixError):
            apply_fixes(_score([("quarter", 0, 60)]), [empty])


def test_free_text_ignores_the_kinds_that_do_apply():
    assert free_text([{"kind": "undot", "staff": 3, "measure": 1, "index": 0}]) == []
