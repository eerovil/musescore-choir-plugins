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
