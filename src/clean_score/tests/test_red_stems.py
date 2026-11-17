"""Red stems in the published copy (strip_red_notes).

homr and the app colour a doubtful note red, and MuseScore keeps the colour on the
chord's stem as well as on its head. The published copy turned the heads back to
black and left the stems red, so Rehearse showed red stems.
"""
from lxml import etree

from src.clean_score.utils.problem_marks import strip_red_notes

RED = '<color r="255" g="0" b="0" a="255"/>'
BLUE = '<color r="0" g="0" b="255" a="255"/>'


def _score(note_colour: str = "", stem_colour: str = "") -> etree._Element:
    """One quarter-note chord, its stem and head coloured as asked."""
    return etree.fromstring(
        "<museScore><Score><Staff id='1'><Measure><voice>"
        "<Chord><durationType>quarter</durationType>"
        f"<Stem>{stem_colour}<userLen>2</userLen></Stem>"
        f"<Note>{note_colour}<pitch>60</pitch><tpc>14</tpc></Note>"
        "</Chord></voice></Measure></Staff></Score></museScore>"
    )


def _red(root: etree._Element) -> list:
    return [c for c in root.iter("color")
            if c.get("r") == "255" and c.get("g") == "0" and c.get("b") == "0"]


def test_a_red_stem_turns_black_with_its_red_note():
    root = _score(note_colour=RED, stem_colour=RED)
    strip_red_notes(root)
    assert _red(root) == []


def test_a_red_stem_on_a_black_note_turns_black():
    root = _score(stem_colour=RED)
    strip_red_notes(root)
    assert _red(root) == []


def test_the_stem_itself_stays_when_its_colour_goes():
    root = _score(stem_colour=RED)
    strip_red_notes(root)
    stem = root.find(".//Stem")
    assert stem is not None
    assert stem.findtext("userLen") == "2"


def test_a_stem_of_another_colour_keeps_it():
    root = _score(stem_colour=BLUE)
    strip_red_notes(root)
    colour = root.find(".//Stem/color")
    assert colour is not None
    assert (colour.get("r"), colour.get("g"), colour.get("b")) == ("0", "0", "255")


def test_the_count_is_still_the_notes_turned_back():
    assert strip_red_notes(_score(note_colour=RED, stem_colour=RED)) == 1
    assert strip_red_notes(_score(stem_colour=RED)) == 0
