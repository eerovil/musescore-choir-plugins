"""A `bar` fix puts right a voice the scan made longer than its bar (#350).

Lasinkuultava laulu, T1 bar 9: the page prints A dotted quarter, G eighth, F quarter,
D C eighths in 4/4. The scan read G (with a sixteenth rest) and D C as triplets and
added an eighth rest, so the voice lasts 7/6 of a whole note in a bar of 1, and
MuseScore resets it to a rest after the recorded fixes have run. A `bar` fix had to
keep the 7/6, which no writable lengths add up to, so nothing in `fixes.json` could
bring the bar back. It may now fill the bar's own length instead.

The score below is that bar as cleaning writes it, red notes taken off, with the
slur reaching in from bar 8 and the lower tenor beside it.
"""
import os

import pytest
from lxml import etree

from src.clean_score.utils.rejected_bars import _walk
from src.clean_score.utils.score_fixes import FixError, _measure, apply_fixes, bar_tokens


def _chord(dur, pitch, dots=0, inside=""):
    dot = f"<dots>{dots}</dots>" if dots else ""
    return (f"<Chord>{dot}<durationType>{dur}</durationType>{inside}"
            f"<Note><pitch>{pitch}</pitch><tpc>14</tpc></Note></Chord>")


def _rest(dur):
    return f"<Rest><durationType>{dur}</durationType></Rest>"


def _triplet(base, inside):
    return (f"<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes>"
            f"<baseNote>{base}</baseNote></Tuplet>{inside}<endTuplet/>")


SLUR_OUT = ('<Spanner type="Slur"><Slur/><next><location><measures>1</measures>'
            '<fractions>-3/4</fractions></location></next></Spanner>')
SLUR_IN = ('<Spanner type="Slur"><prev><location><measures>-1</measures>'
           '<fractions>3/4</fractions></location></prev></Spanner>')

T1_BAR_9 = (_chord("quarter", 57, dots=1, inside=SLUR_IN)
            + _triplet("16th", _chord("eighth", 55) + _rest("16th"))
            + _chord("quarter", 53, dots=1)
            + _triplet("eighth", _chord("eighth", 62) + _chord("eighth", 60))
            + _rest("eighth"))
T2_BAR_9 = (_chord("eighth", 48) + _chord("quarter", 50) + _chord("eighth", 52)
            + _chord("quarter", 53, dots=1) + _chord("eighth", 55))

FROM = ["quarter.:57", "[tuplet", "eighth:55", "16th:R", "tuplet]", "quarter.:53",
        "[tuplet", "eighth:62", "eighth:60", "tuplet]", "eighth:R"]
PAGE = [("note_4.", 57, 17), ("note_8", 55, 15), ("note_4", 53, 13),
        ("note_8", 62, 16), ("note_8", 60, 14)]


def lasinkuultava():
    head = "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
    bar_8 = {1: _rest("half") + _chord("quarter", 60) + _chord("quarter", 59, inside=SLUR_OUT),
             2: _rest("quarter") + _chord("quarter", 50) + _chord("quarter", 52)
             + _chord("quarter", 48)}
    bar_9 = {1: T1_BAR_9, 2: T2_BAR_9}
    staves = "".join(
        f'<Staff id="{sid}"><Measure><voice>{head}{bar_8[sid]}</voice></Measure>'
        f"<Measure><voice>{bar_9[sid]}</voice></Measure></Staff>" for sid in (1, 2))
    parts = "".join(f'<Part><trackName>{name}</trackName><Staff id="{sid}"/></Part>'
                    for sid, name in ((1, "T1"), (2, "T2")))
    return etree.fromstring(
        f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


def _bar(values, expect=FROM):
    return {"kind": "bar", "staff": 1, "measure": 2, "from": expect,
            "why": "page prints A. G F D C",
            "to": [{"value": v, "pitches": [p], "tpcs": [t]} for v, p, t in values]}


def _onsets(root):
    return [at for at, el in _walk(_measure(root, 1, 2).find("voice"), 1)
            if el.tag in ("Chord", "Rest")]


def test_the_scanned_bar_is_what_the_fix_was_recorded_against():
    assert bar_tokens(lasinkuultava(), 1, 2) == FROM


def test_lasinkuultava_t1_bar_9_comes_back_from_the_recorded_fix_alone():
    root = lasinkuultava()
    done = apply_fixes(root, [_bar(PAGE)])
    assert "wrote the bar afresh" in done[0]
    assert bar_tokens(root, 1, 2) == ["quarter.:57", "eighth:55", "quarter:53",
                                      "eighth:62", "eighth:60"]
    assert _onsets(root) == [0, 3 / 8, 1 / 2, 3 / 4, 7 / 8]
    assert _measure(root, 1, 2).find(".//Tuplet") is None
    # The bar keeps the time signature's length and the other tenor is untouched.
    assert "len" not in _measure(root, 1, 2).attrib
    assert bar_tokens(root, 2, 2) == ["eighth:48", "quarter:50", "eighth:52",
                                      "quarter.:53", "eighth:55"]


def test_a_fix_that_fills_neither_the_voice_nor_the_bar_refuses():
    root = lasinkuultava()
    with pytest.raises(FixError, match="adds up to 7/8 .* the voice to 7/6 and the bar to 1"):
        apply_fixes(root, [_bar(PAGE[:-1])])
    assert bar_tokens(root, 1, 2) == FROM


def test_a_voice_that_fills_its_bar_still_has_to_be_filled():
    # Only the bar's own length is added; a voice already that long gains nothing.
    root = lasinkuultava()
    expect = bar_tokens(root, 2, 2)
    fix = {"kind": "bar", "staff": 2, "measure": 2, "from": expect, "why": "x",
           "to": [{"value": "note_2", "pitches": [48], "tpcs": [14]}]}
    with pytest.raises(FixError, match="adds up to 1/2 .* the bar to 1$"):
        apply_fixes(root, [fix])


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_opens_the_bar_after_the_fix(tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    root = lasinkuultava()
    path = tmp_path / "scanned.mscx"
    etree.ElementTree(root).write(str(path))
    assert pipeline.musescore_check(str(path)) != []
    apply_fixes(root, [_bar(PAGE)])
    etree.ElementTree(root).write(str(path))
    assert pipeline.musescore_check(str(path)) == []
