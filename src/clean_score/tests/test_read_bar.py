"""Reading a bar back out of the score, so a fix can be picked instead of typed.

The numbering and the token grammar belong to `score_fixes`; a caller that worked
them out for itself would be a second implementation of both, and that is exactly
how a recorded fix ends up naming the wrong chord. So what these pin is that the
read and the write agree — the index `read_bar` shows is the index a `slur` entry
means, and the token it shows is the token an `append` entry would carry.
"""
import pytest
from lxml import etree

from src.clean_score.utils import score_fixes
from src.clean_score.utils.score_fixes import FixError, apply_fixes, note_name, read_bar

# Herää Suomi!, bar 8, Tenor 1 as the scan left it: a half rest, then D, E flat, D.
# The page slurs the E flat to the D, so the bar carries two syllables and not three.
BAR_8 = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Staff id="1"><Measure><voice>
  <Rest><durationType>half</durationType></Rest>
  <Chord><durationType>quarter</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  <Chord><durationType>eighth</durationType><dots>1</dots>
         <Note><pitch>63</pitch><tpc>11</tpc></Note></Chord>
  <Chord><durationType>16th</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
</voice></Measure></Staff>
</Score></museScore>"""


@pytest.fixture
def bar8():
    return etree.fromstring(BAR_8.encode())


def test_the_bar_reads_as_its_chords_and_the_rest_is_not_one(bar8):
    """`index` counts chords, which is what an entry's `index` means — rests are skipped."""
    read = read_bar(bar8, 1, 1)
    assert [n["index"] for n in read] == [0, 1, 2]
    assert [n["name"] for n in read] == ["D4", "Eb4", "D4"]


def test_the_token_shown_is_the_token_a_fix_would_carry(bar8):
    """Same words as an `append` entry's `from` list, so the two cannot drift apart."""
    measure = score_fixes._measure(bar8, 1, 1)
    assert [n["token"] for n in read_bar(bar8, 1, 1)] == [
        t for t in score_fixes._bar_tokens(measure) if not t.endswith(":R")]


def test_the_index_shown_is_the_index_the_slur_lands_on(bar8):
    """The reason this lives beside `apply_fixes`: pick note 2, and note 2 is slurred."""
    apply_fixes(bar8, [{"kind": "slur", "staff": 1, "measure": 1, "index": 1, "span": 1,
                        "why": "the page slurs E flat to D"}])
    read = read_bar(bar8, 1, 1)
    assert [n["starts_slur"] for n in read] == [False, True, False]


def test_an_already_slurred_note_says_so(bar8):
    """The one thing a caller has to check before offering to add another slur."""
    assert not any(n["starts_slur"] for n in read_bar(bar8, 1, 1))
    apply_fixes(bar8, [{"kind": "slur", "staff": 1, "measure": 1, "index": 1, "span": 1,
                        "why": "..."}])
    assert read_bar(bar8, 1, 1)[1]["starts_slur"]


def test_a_bar_that_is_not_there_is_refused_the_same_way_a_fix_is(bar8):
    with pytest.raises(FixError):
        read_bar(bar8, 1, 99)
    with pytest.raises(FixError):
        read_bar(bar8, 9, 1)


def test_a_chord_reads_as_its_notes(bar8):
    chord = bar8.findall(".//Chord")[0]
    etree.SubElement(etree.SubElement(chord, "Note"), "pitch").text = "58"
    assert read_bar(bar8, 1, 1)[0]["name"].startswith("D4+")


@pytest.mark.parametrize("pitch, tpc, expected", [
    (62, 16, "D4"),      # the naturals are tpc 13..19, F C G D A E B
    (63, 11, "Eb4"),     # seven down is one flat
    (63, 23, "D#4"),     # seven up is one sharp — same sound, different line
    (60, 14, "C4"),
    (58, 12, "Bb3"),
    (60, 26, "B#3"),     # the octave follows the letter, not the sound
    (59, 7, "Cb4"),
])
def test_a_note_is_named_the_way_the_page_spells_it(pitch, tpc, expected):
    """Shown to someone comparing against the print: an E flat offered as a D sharp
    is a note they have to translate before they can agree with it."""
    assert note_name(pitch, tpc) == expected


def test_without_a_spelling_it_falls_back_to_sharps():
    assert note_name(63) == "D#4"
    assert note_name(60) == "C4"


# #357: what an agent writing a fix through the API kept getting wrong.

TIED = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Staff id="1">
<Measure><voice>
  <Chord><durationType>quarter</durationType><Note><pitch>60</pitch><tpc>14</tpc></Note></Chord>
  <Rest><durationType>quarter</durationType></Rest>
  <Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>
  <Chord><durationType>eighth</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  <Chord><durationType>eighth</durationType><Note><pitch>64</pitch><tpc>18</tpc></Note></Chord>
  <Chord><durationType>eighth</durationType><Note><pitch>64</pitch><tpc>18</tpc></Note></Chord>
  <endTuplet/>
  <Chord><durationType>quarter</durationType>
    <Note><Spanner type="Tie"><Tie/><next><location><measures>1</measures><fractions>-1/4</fractions></location></next></Spanner><pitch>65</pitch><tpc>13</tpc></Note>
    <Note><pitch>60</pitch><tpc>14</tpc></Note></Chord>
</voice></Measure>
<Measure><voice>
  <Chord><durationType>whole</durationType>
    <Note><Spanner type="Tie"><prev><location><measures>-1</measures><fractions>1/4</fractions></location></prev></Spanner><pitch>65</pitch><tpc>13</tpc></Note></Chord>
</voice></Measure>
</Staff>
</Score></museScore>"""


@pytest.fixture
def tied():
    return etree.fromstring(TIED.encode())


def test_a_tie_across_the_barline_shows_on_both_notes(tied):
    """Agents recorded ties that were already there, because `/bar` did not show them."""
    last = read_bar(tied, 1, 1)[-1]
    assert last["pitches"] == [
        {"pitch": 65, "name": "F4", "tied_to_next": True, "tied_from_prev": False},
        {"pitch": 60, "name": "C4", "tied_to_next": False, "tied_from_prev": False}]
    assert read_bar(tied, 1, 2)[0]["pitches"][0]["tied_from_prev"] is True


def test_a_tie_inside_the_bar_shows_on_both_notes(tied):
    apply_fixes(tied, [{"kind": "tie", "staff": 1, "measure": 1, "index": 2, "pitch": 64,
                        "from": score_fixes.bar_tokens(tied, 1, 1), "why": "..."}])
    read = read_bar(tied, 1, 1)
    assert read[2]["pitches"][0]["tied_to_next"] and read[3]["pitches"][0]["tied_from_prev"]
    assert not read[1]["pitches"][0]["tied_to_next"]


def test_each_chord_says_where_it_stands_in_from(tied):
    """`index` counts chords, `from` lists rests and brackets too; `at` joins the two."""
    tokens = score_fixes.bar_tokens(tied, 1, 1)
    items = score_fixes.bar_items(tied, 1, 1)
    assert [i["token"] for i in items] == tokens
    assert [i["index"] for i in items] == [0, None, None, 1, 2, 3, None, 4]
    for note in read_bar(tied, 1, 1):
        assert tokens[note["at"]] == note["token"]
        assert items[note["at"]]["index"] == note["index"]


def test_a_slur_end_is_shown(bar8):
    apply_fixes(bar8, [{"kind": "slur", "staff": 1, "measure": 1, "index": 1, "span": 1,
                        "why": "..."}])
    assert [n["ends_slur"] for n in read_bar(bar8, 1, 1)] == [False, False, True]


def test_a_wrong_index_is_answered_with_the_chords(tied):
    """A first wrong guess shows the right one: rests do not count."""
    with pytest.raises(FixError) as err:
        apply_fixes(tied, [{"kind": "untie", "staff": 1, "measure": 2, "index": 1,
                            "pitch": 65, "from": ["whole:65"], "why": "..."}])
    assert "has 1 chords, no index 1" in str(err.value)
    assert "index counts chords only, not rests: 0 = whole:65" in str(err.value)
    with pytest.raises(FixError, match="not rests: 0 = quarter:60, 1 = eighth:62"):
        apply_fixes(tied, [{"kind": "undot", "staff": 1, "measure": 1, "index": 9, "why": "."}])
