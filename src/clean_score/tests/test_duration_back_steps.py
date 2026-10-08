"""A `duration` fix also takes out the back-step cleaning squeezed a voice in with (#344).

The scan drops a dot in one voice, the bar is written at that short length, and
cleaning fits the voices the page prints correctly into it by stepping them
backwards (a negative `location`). The `duration` fix gives the bar its length back,
and the back-step then makes MuseScore read those voices as too long and reset them
to rests -- after every recorded fix has run, so nothing in `fixes.json` could put
them back.

The scores below are the two real cases as cleaning writes them, decorations taken
off: Gute Nacht bar 6 (4/4 read as 7/8, T1 and B1 stepped back an eighth) and Annin
laulu bar 10 (3/4 read as 11/16, T1 stepped back a sixteenth, a tie out of its last
note into the next bar).
"""
import os

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.rejected_bars import _walk
from src.clean_score.utils.score_fixes import _measure, apply_fixes, bar_tokens


def _chord(dur, pitch, dots=0, inside=""):
    dot = f"<dots>{dots}</dots>" if dots else ""
    return (f"<Chord>{dot}<durationType>{dur}</durationType>"
            f"<Note>{inside}<pitch>{pitch}</pitch><tpc>14</tpc></Note></Chord>")


def _rest(dur):
    return f"<Rest><durationType>{dur}</durationType></Rest>"


def _step(fraction):
    return f"<location><fractions>{fraction}</fractions></location>"


def _score(sig, short, bars):
    """`bars`: staff id -> (name, [bar contents]); the second bar is the short one."""
    head = f"<TimeSig><sigN>{sig[0]}</sigN><sigD>{sig[1]}</sigD></TimeSig>"
    parts = "".join(f'<Part><trackName>{name}</trackName><Staff id="{sid}"/></Part>'
                    for sid, (name, _) in bars.items())
    staves = ""
    for sid, (_, contents) in bars.items():
        measures = "".join(
            f'<Measure{f" len={chr(34)}{short}{chr(34)}" if i == 1 else ""}>'
            f"<voice>{head if i == 0 else ''}{bar}</voice></Measure>"
            for i, bar in enumerate(contents))
        staves += f'<Staff id="{sid}">{measures}</Staff>'
    return etree.fromstring(
        f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


def gute_nacht():
    # Bar 2 of the song stands in for its bar 2, the bar 6 repeats.
    whole = _chord("whole", 60)
    return _score((4, 4), "7/8", {
        1: ("T1", [whole, _chord("quarter", 60, dots=1) + _step("-1/8") + _chord("eighth", 60)
                   + _chord("quarter", 60) + _chord("quarter", 60), whole]),
        2: ("T2", [whole, _chord("quarter", 60) + _chord("eighth", 58)
                   + _chord("quarter", 56) + _chord("quarter", 56), whole]),
        3: ("B1", [whole, _chord("quarter", 56, dots=1) + _step("-1/8") + _chord("eighth", 55)
                   + _chord("quarter", 53) + _chord("quarter", 53), whole]),
        4: ("B2", [whole, _chord("quarter", 56) + _chord("eighth", 55)
                   + _chord("quarter", 53) + _chord("quarter", 51), whole]),
    })


TIE_OUT = ('<Spanner type="Tie"><Tie></Tie><next><location><measures>1</measures>'
           '<fractions>-7/16</fractions></location></next></Spanner>')
TIE_IN = ('<Spanner type="Tie"><prev><location><measures>-1</measures>'
          '<fractions>7/16</fractions></location></prev></Spanner>')


def annin_laulu():
    # Bar 10 and the bar 11 its last note is tied into.
    first = _chord("half", 60, dots=1)
    return _score((3, 4), "11/16", {
        1: ("T1", [first, _chord("eighth", 65, dots=1) + _chord("16th", 61)
                   + _chord("eighth", 65, dots=1) + _step("-1/16") + _chord("16th", 61)
                   + _chord("quarter", 68, inside=TIE_OUT),
                   _chord("half", 68, inside=TIE_IN) + _rest("quarter")]),
        2: ("T2", [first, _chord("quarter", 61, dots=1) + _chord("16th", 61)
                   + _chord("quarter", 60), _chord("half", 60) + _rest("quarter")]),
    })


def _fix(root, staff, measure, to):
    return {"kind": "duration", "staff": staff, "measure": measure, "index": 0, "to": to,
            "from": bar_tokens(root, staff, measure), "why": "read against the page"}


def _onsets(root, staff, measure):
    voice = _measure(root, staff, measure).find("voice")
    return [at for at, el in _walk(voice, 1) if el.tag == "Chord"]


def test_gute_nacht_bar_6_comes_back_from_the_recorded_fixes_alone():
    root = gute_nacht()
    done = apply_fixes(root, [_fix(root, 2, 2, "quarter."), _fix(root, 4, 2, "quarter.")])
    assert "the back-step squeezing staff 1, 3 into it is gone" in done[0]
    # The second fix finds the bar 4/4 already and nothing left to close.
    assert "back-step" not in done[1]
    for staff in (1, 3):
        assert _measure(root, staff, 2).find("voice/location") is None
        assert _onsets(root, staff, 2) == [0, 3 * 1 / 8, 1 / 2, 3 * 1 / 4]
    assert bar_tokens(root, 1, 2) == ["quarter.:60", "eighth:60", "quarter:60", "quarter:60"]
    assert bar_tokens(root, 3, 2) == ["quarter.:56", "eighth:55", "quarter:53", "quarter:53"]
    assert lyric_txt.slot_counts(root)[1][2] == 4


def test_a_tie_after_the_back_step_still_reaches_its_note():
    root = annin_laulu()
    apply_fixes(root, [_fix(root, 2, 2, "quarter..")])
    assert _measure(root, 1, 2).find("voice/location") is None
    assert _onsets(root, 1, 2) == [0, 3 / 16, 4 / 16, 7 / 16, 8 / 16]
    out = _measure(root, 1, 2).find(".//Spanner[@type='Tie']")
    into = _measure(root, 1, 3).find(".//Spanner[@type='Tie']")
    assert out.findtext("next/location/fractions") == "-1/2"
    assert into.findtext("prev/location/fractions") == "1/2"


def test_a_voice_the_back_step_did_not_squeeze_to_the_bar_is_left_for_musescore():
    # Without the step the voice would last 13/16 in a bar of 12: it is not the
    # printed bar squeezed, so taking the step out would be a guess.
    root = annin_laulu()
    voice = _measure(root, 1, 2).find("voice")
    voice.append(etree.fromstring(_chord("16th", 61)))
    done = apply_fixes(root, [_fix(root, 2, 2, "quarter..")])
    assert "back-step" not in done[0]
    assert _measure(root, 1, 2).find("voice/location") is not None


def test_a_forward_gap_is_not_a_back_step():
    root = gute_nacht()
    _measure(root, 1, 2).find("voice/location/fractions").text = "1/8"
    done = apply_fixes(root, [_fix(root, 2, 2, "quarter.")])
    assert "staff 1," not in done[0] and "staff 3 into" in done[0]
    assert _measure(root, 1, 2).find("voice/location") is not None


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
@pytest.mark.parametrize("build,fixes", [
    (gute_nacht, [(2, "quarter."), (4, "quarter.")]),
    (annin_laulu, [(2, "quarter..")]),
])
def test_musescore_opens_every_voice_of_the_bar_after_the_fixes(build, fixes, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    root = build()
    apply_fixes(root, [_fix(root, staff, 2, to) for staff, to in fixes])
    path = tmp_path / "fixed.mscx"
    etree.ElementTree(root).write(str(path))
    assert pipeline.musescore_check(str(path)) == []
