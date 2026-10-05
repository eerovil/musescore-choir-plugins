"""The `rhythm` fix: a bar's notes given new lengths, the notes themselves untouched (#269).

It is how a person's pick among homr's readings of an unsure bar is recorded, so what
these pin is that the bar comes out as the reading says — triplet brackets included —
and that nothing else moves: the pitches, the lyrics, a tie into the next bar.
"""
import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import (FixError, _bar_tokens, _measure, apply_fixes,
                                               triplet_groups)

# Legenda bar 25's bass as homr wrote it: G C D triplet, E flat plain quarter, then
# C F sharp as a triplet, and a triplet F G whose F is tied from the F sharp before.
BAR = """
<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>
<Chord><BeamMode>begin</BeamMode><durationType>eighth</durationType>
  <Lyrics><text>Maa</text></Lyrics><Note><pitch>43</pitch><tpc>15</tpc></Note></Chord>
<Chord><durationType>eighth</durationType><Note><pitch>48</pitch><tpc>14</tpc></Note></Chord>
<Chord><durationType>eighth</durationType><Note><pitch>50</pitch><tpc>16</tpc></Note></Chord>
<endTuplet/>
<Chord><durationType>quarter</durationType><Note><pitch>51</pitch><tpc>11</tpc></Note></Chord>
<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>
<Chord><durationType>eighth</durationType><Note><pitch>48</pitch><tpc>14</tpc></Note></Chord>
<Chord><durationType>quarter</durationType><Note>
  <Spanner type="Tie"><Tie/><next><location><fractions>1/6</fractions></location></next></Spanner>
  <pitch>54</pitch><tpc>20</tpc></Note></Chord>
<endTuplet/>
<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes><baseNote>eighth</baseNote></Tuplet>
<Chord><durationType>quarter</durationType><Note>
  <Spanner type="Tie"><prev><location><fractions>-1/6</fractions></location></prev></Spanner>
  <Spanner type="Tie"><Tie/><next><location><measures>1</measures><fractions>-3/4</fractions></location></next></Spanner>
  <pitch>54</pitch><tpc>20</tpc></Note></Chord>
<Chord><durationType>eighth</durationType><Note><pitch>43</pitch><tpc>15</tpc></Note></Chord>
<endTuplet/>
"""

SCORE = f"""<museScore version="3.02"><Score>
<Part><trackName>B1</trackName><Staff id="1"/></Part>
<Staff id="1">
  <Measure><voice>
    <StaffText><color r="255" g="0" b="0" a="255"/><text>⚠ check against the page: rhythm</text></StaffText>
    {BAR}
  </voice></Measure>
  <Measure><voice>
    <Chord><durationType>quarter</durationType><Note>
      <Spanner type="Tie"><prev><location><measures>-1</measures><fractions>3/4</fractions></location></prev></Spanner>
      <pitch>54</pitch><tpc>20</tpc></Note></Chord>
    <Rest><durationType>quarter</durationType></Rest>
    <Rest><durationType>half</durationType></Rest>
  </voice></Measure>
</Staff>
</Score></museScore>"""

WRITTEN = ["note_12", "note_12", "note_12", "note_4", "note_12", "note_6", "note_6", "note_12"]
PAGE = ["note_12", "note_12", "note_12", "note_6", "note_12", "note_4", "note_6", "note_12"]


@pytest.fixture
def root():
    return etree.fromstring(SCORE)


def _fix(root, values):
    return {"kind": "rhythm", "staff": 1, "measure": 1,
            "from": _bar_tokens(_measure(root, 1, 1)), "to": values, "why": "picked c"}


def _bar(root, n=1):
    return _measure(root, 1, n).find("voice")


def test_the_bar_takes_the_picked_lengths_and_brackets(root):
    apply_fixes(root, [_fix(root, PAGE)])
    assert _bar_tokens(_measure(root, 1, 1)) == [
        "[tuplet", "eighth:43", "eighth:48", "eighth:50", "tuplet]",
        "[tuplet", "quarter:51", "eighth:48", "tuplet]",
        "quarter:54",
        "[tuplet", "quarter:54", "eighth:43", "tuplet]"]


def test_the_notes_and_their_words_are_left_alone(root):
    pitches = [n.findtext("pitch") for n in _bar(root).iter("Note")]
    apply_fixes(root, [_fix(root, PAGE)])
    assert [n.findtext("pitch") for n in _bar(root).iter("Note")] == pitches
    assert _bar(root).find("Chord/Lyrics/text").text == "Maa"


def test_ties_still_join_the_notes_they_joined(root):
    apply_fixes(root, [_fix(root, PAGE)])
    chords = _bar(root).findall("Chord")
    # The F sharp now starts at 1/2 and the F it is tied to at 3/4: a quarter apart.
    assert chords[5].findtext("Note/Spanner/next/location/fractions") == "1/4"
    assert chords[6].findtext("Note/Spanner/prev/location/fractions") == "-1/4"
    # The tie on into the next bar starts at the same place it did, so it is unchanged.
    assert chords[6].findtext(".//Spanner/next/location/fractions") == "-3/4"


def test_a_tie_into_the_next_bar_follows_its_note():
    # The last F now starts at 2/3 rather than 3/4, so both ends of its tie into the
    # next bar move by the same twelfth.
    root = etree.fromstring(SCORE)
    values = ["note_12", "note_12", "note_12", "note_6", "note_12", "note_6", "note_12",
              "note_4"]
    apply_fixes(root, [_fix(root, values)])
    last_f = _bar(root).findall("Chord")[6]
    assert last_f.findtext(".//Spanner/next/location/fractions") == "-2/3"
    assert _measure(root, 1, 2).findtext(
        "voice/Chord/Note/Spanner/prev/location/fractions") == "2/3"
    # The tie from the F sharp before it is still a sixth long.
    assert _bar(root).findall("Chord")[5].findtext(
        "Note/Spanner/next/location/fractions") == "1/6"


def test_the_red_mark_goes_once_a_person_has_read_the_bar(root):
    apply_fixes(root, [_fix(root, PAGE)])
    assert _bar(root).find("StaffText") is None


def test_picking_the_reading_already_there_changes_nothing_but_the_mark(root):
    before = _bar_tokens(_measure(root, 1, 1))
    apply_fixes(root, [_fix(root, WRITTEN)])
    assert _bar_tokens(_measure(root, 1, 1)) == before


def test_old_beams_are_left_to_musescore(root):
    apply_fixes(root, [_fix(root, PAGE)])
    assert _bar(root).find(".//BeamMode") is None


def test_dots_go_before_the_length_as_musescore_writes_them():
    root = etree.fromstring(SCORE)
    apply_fixes(root, [_fix(root, ["note_12", "note_12", "note_12", "note_8.", "note_16",
                                   "note_6", "note_6", "note_6"])])
    chord = _bar(root).findall("Chord")[3]
    assert [el.tag for el in chord][:2] == ["dots", "durationType"]
    assert chord.findtext("durationType") == "eighth"


def test_a_bar_that_has_changed_since_is_refused(root):
    fix = _fix(root, PAGE)
    fix["from"] = fix["from"][1:]
    with pytest.raises(FixError, match="reads"):
        apply_fixes(root, [fix])


def test_lengths_that_do_not_fill_the_bar_are_refused(root):
    with pytest.raises(FixError, match="add up"):
        apply_fixes(root, [_fix(root, ["note_12"] * 8)])


def test_the_wrong_number_of_lengths_is_refused(root):
    with pytest.raises(FixError, match="lengths"):
        apply_fixes(root, [_fix(root, PAGE[:-1])])


def test_a_rest_cannot_become_a_note():
    root = etree.fromstring(SCORE)
    fix = {"kind": "rhythm", "staff": 1, "measure": 2,
           "from": _bar_tokens(_measure(root, 1, 2)),
           "to": ["note_4", "note_4", "rest_2"], "why": "x"}
    with pytest.raises(FixError, match="rest"):
        apply_fixes(root, [fix])


def test_triplets_are_bracketed_beat_by_beat():
    assert triplet_groups(["note_12"] * 6 + ["note_2"]) == [(0, 2), (3, 5)]
    assert triplet_groups(["note_6"] * 3 + ["note_2"]) == [(0, 2)]
    assert triplet_groups(["note_6", "note_12", "note_4", "note_2"]) == [(0, 1)]
    with pytest.raises(FixError):
        triplet_groups(["note_12", "note_12", "note_4", "note_12", "note_2."])
    with pytest.raises(FixError):
        triplet_groups(["note_2.", "note_12", "note_6"][:2] + ["note_12"])
