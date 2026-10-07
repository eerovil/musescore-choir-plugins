"""The `bar` fix: a whole bar of one voice put as a person picked it (#295).

A pick among homr's whole-bar readings changes lengths and pitches together. What
these pin: the bar comes out as picked; when the notes stay in their places the ties,
slurs and words stay with them; when they do not — homr's second reading can have
more or fewer notes — ties reaching in from outside are cut, the words go back on the
notes in order, and the red mark goes either way. And it is strict like every kind.
"""
import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, _bar_tokens, _measure, apply_fixes

SCORE = """<museScore version="3.02"><Score>
<Part><trackName>A1</trackName><Staff id="1"/></Part>
<Staff id="1">
  <Measure><voice>
    <Rest><durationType>half</durationType></Rest>
    <Chord><durationType>half</durationType><Note>
      <Spanner type="Tie"><Tie/><next><location><measures>1</measures><fractions>-1/2</fractions></location></next></Spanner>
      <pitch>60</pitch><tpc>14</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice>
    <StaffText><text>⚠ notes?</text></StaffText>
    <Chord><durationType>quarter</durationType><Lyrics><text>la</text></Lyrics><Note>
      <Spanner type="Tie"><prev><location><measures>-1</measures><fractions>1/2</fractions></location></prev></Spanner>
      <pitch>60</pitch><tpc>14</tpc></Note></Chord>
    <Chord><durationType>quarter</durationType><Lyrics><text>le</text></Lyrics><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
    <Chord><durationType>half</durationType><Lyrics><text>li</text></Lyrics><Note><color r="255" g="0" b="0" a="255"/><pitch>64</pitch><tpc>18</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice>
    <Rest><durationType>measure</durationType><duration>4/4</duration></Rest>
  </voice></Measure>
</Staff>
</Score></museScore>"""

FROM = ["quarter:60", "quarter:62", "half:64"]


@pytest.fixture
def root():
    return etree.fromstring(SCORE)


def _fix(to, expect=FROM):
    return {"kind": "bar", "staff": 1, "measure": 2, "from": expect, "to": to,
            "why": "picked against the page"}


def _note(value, pitch, tpc):
    return {"value": value, "pitches": [pitch], "tpcs": [tpc]}


def _words(root, measure=2):
    return [lyr.findtext("text") for lyr in _measure(root, 1, measure).iter("Lyrics")]


def _ties(root, measure):
    return _measure(root, 1, measure).findall(".//Spanner[@type='Tie']")


def test_same_places_keep_the_tie_and_the_words(root):
    apply_fixes(root, [_fix([_note("note_4.", 60, 14), _note("note_8", 62, 16),
                             _note("note_2", 65, 13)])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["quarter.:60", "eighth:62", "half:65"]
    assert _words(root) == ["la", "le", "li"]
    assert len(_ties(root, 1)) == 1 and len(_ties(root, 2)) == 1
    bar = _measure(root, 1, 2)
    assert bar.find(".//StaffText") is None  # the red mark
    assert bar.find(".//Note/color") is None  # and the red note
    assert [n.findtext("tpc") for n in bar.iter("Note")] == ["14", "16", "13"]


def test_a_tied_note_changing_pitch_loses_its_tie(root):
    apply_fixes(root, [_fix([_note("note_4", 59, 19), _note("note_4", 62, 16),
                             _note("note_2", 64, 18)])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["quarter:59", "quarter:62", "half:64"]
    assert _ties(root, 1) == [] and _ties(root, 2) == []
    assert _words(root) == ["la", "le", "li"]


def test_a_bar_with_other_notes_is_written_afresh(root):
    apply_fixes(root, [_fix([_note("note_2", 60, 14), _note("note_2", 65, 13)])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["half:60", "half:65"]
    # The tie from bar 1 pointed at a note that is not there any more.
    assert _ties(root, 1) == [] and _ties(root, 2) == []
    # The words stay in order; the one left over goes.
    assert _words(root) == ["la", "le"]
    assert _measure(root, 1, 2).find(".//StaffText") is None
    # The bars either side are untouched.
    assert _bar_tokens(_measure(root, 1, 3)) == ["measure:R"]


def test_a_fresh_bar_brackets_its_triplets_and_keeps_rests(root):
    apply_fixes(root, [_fix([_note("note_12", 60, 14), _note("note_12", 62, 16),
                             _note("note_12", 64, 18), {"value": "rest_4", "pitches": []},
                             _note("note_2", 65, 13)])])
    assert _bar_tokens(_measure(root, 1, 2)) == [
        "[tuplet", "eighth:60", "eighth:62", "eighth:64", "tuplet]", "quarter:R", "half:65"]


def test_a_chord_is_written_with_both_notes(root):
    apply_fixes(root, [_fix([{"value": "note_1", "pitches": [64, 60], "tpcs": [18, 14]}])])
    assert _bar_tokens(_measure(root, 1, 2)) == ["whole:60+64"]


def test_it_is_strict(root):
    with pytest.raises(FixError, match="recorded against"):
        apply_fixes(root, [_fix([_note("note_1", 60, 14)], expect=["whole:60"])])
    with pytest.raises(FixError, match="adds up"):
        apply_fixes(root, [_fix([_note("note_2", 60, 14)])])
    with pytest.raises(FixError, match="spellings"):
        apply_fixes(root, [_fix([{"value": "note_1", "pitches": [60]}])])
    assert _bar_tokens(_measure(root, 1, 2)) == FROM
