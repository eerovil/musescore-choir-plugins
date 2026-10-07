"""A slur the scan ran from one voice into another is taken out and marked (#238).

Sangerhilsen bars 10->11: homr started a slur in the upper tenor and stopped it in the
lower one. After the split each half points at a voice that is not there, and the end
half still costs the lower tenor a syllable.
"""
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.cross_voice_slurs import drop_cross_voice_slurs
from src.clean_score.utils.problem_marks import marks

# Upper tenor, beat 4 of bar 1, slurring into the other voice at the head of bar 2.
OUT = ('<Spanner type="Slur"><Slur/><next><location><voices>1</voices><measures>1</measures>'
       '<fractions>-3/4</fractions></location></next></Spanner>')
IN = ('<Spanner type="Slur"><prev><location><voices>-1</voices><measures>-1</measures>'
      '<fractions>3/4</fractions></location></prev></Spanner>')
# An ordinary slur inside one voice, beat 1 to beat 2 of bar 2.
OWN_OUT = ('<Spanner type="Slur"><Slur/><next><location><fractions>1/4</fractions>'
           '</location></next></Spanner>')
OWN_IN = ('<Spanner type="Slur"><prev><location><fractions>-1/4</fractions>'
          '</location></prev></Spanner>')


def _chord(pitch, spanner=""):
    return f"<Chord><durationType>quarter</durationType>{spanner}<Note><pitch>{pitch}</pitch></Note></Chord>"


def _score(t1_bar2=None, t2_bar2=None):
    t1 = (f'<Staff id="1"><Measure><voice>{_chord(64)}{_chord(64)}{_chord(64)}{_chord(64, OUT)}</voice></Measure>'
          f'<Measure><voice>{t1_bar2 or _chord(64, OWN_OUT) + _chord(66, OWN_IN) + _chord(64) + _chord(64)}'
          '</voice></Measure></Staff>')
    t2 = (f'<Staff id="2"><Measure><voice>{_chord(60) * 4}</voice></Measure>'
          f'<Measure><voice>{t2_bar2 or _chord(60, IN) + _chord(60) * 3}</voice></Measure></Staff>')
    return etree.fromstring(
        ('<museScore><Score><Part><trackName>T1</trackName><Staff id="1"/></Part>'
         '<Part><trackName>T2</trackName><Staff id="2"/></Part>'
         f'{t1}{t2}</Score></museScore>').encode())


def _slurs(root):
    return [(sp.find("next") is not None) for sp in root.iter("Spanner") if sp.get("type") == "Slur"]


def test_both_halves_go_and_are_reported_as_one_slur():
    root = _score()
    records = drop_cross_voice_slurs(root)
    assert records == [{"measure": 1, "staff": 1, "part": "T1", "note": "E4", "pos": "3/4",
                        "end_measure": 2, "end_staff": 2, "end_part": "T2", "end_note": "C4",
                        "end_pos": "0"}]
    # Only the slur inside one voice is left.
    assert _slurs(root) == [True, False]


def test_both_bars_are_marked_naming_the_other_singer():
    root = _score()
    drop_cross_voice_slurs(root)
    found = [(m["staff"], m["measure"], m["text"].split(" removed")[0]) for m in marks(root)]
    assert found == [(1, 1, "slur to T2 bar 2"), (2, 2, "slur from T1 bar 1")]


def test_the_lower_voice_gets_its_syllable_back():
    root = _score()
    assert lyric_txt.syllable_slots(root, 2, 2)[0] is False
    drop_cross_voice_slurs(root)
    assert lyric_txt.syllable_slots(root, 2, 2)[0] is True


def test_a_slur_inside_one_voice_is_left_alone():
    root = _score(t2_bar2=_chord(60) * 4)
    root.find(".//Staff[@id='1']/Measure/voice/Chord[4]").remove(
        root.find(".//Staff[@id='1']/Measure/voice/Chord[4]/Spanner"))
    before = etree.tostring(root)
    assert drop_cross_voice_slurs(root) == []
    assert etree.tostring(root) == before


def test_a_half_with_no_partner_is_reported_alone():
    root = _score(t2_bar2=_chord(60) * 4)
    records = drop_cross_voice_slurs(root)
    assert [(r["measure"], r["end_measure"]) for r in records] == [(1, None)]
    assert [m["text"].split(" removed")[0] for m in marks(root)] == ["slur to another voice"]


def test_a_second_run_finds_nothing():
    root = _score()
    drop_cross_voice_slurs(root)
    after = etree.tostring(root)
    assert drop_cross_voice_slurs(root) == []
    assert etree.tostring(root) == after
