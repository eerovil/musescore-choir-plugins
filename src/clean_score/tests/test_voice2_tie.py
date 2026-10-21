"""`tie` and `untie` on a line a `voice2` fix wrote (#434).

Butterfly's Sopraano 1 splits in bars 84-85 and the lower line holds "fly" on a C4 tied
from 84 into 85. `tie` only reached voice 1, so the voice-2 C4 could not be held and was
sung twice. What these pin: `"voice": 2` ties voice 2's notes, inside a bar and into the
next bar's voice 2, with `from` and `index` read off voice 2; the tied-into note takes no
syllable; voice 1 is left alone; `untie` takes it out again; and the refusals name voice 2.
"""
import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import (FixError, _measure, apply_fixes, bar_tokens,
                                               voice2_tokens)

HEAD = "<TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>"


def _chord(dur, pitch, tpc=14):
    return (f"<Chord><durationType>{dur}</durationType>"
            f"<Note><pitch>{pitch}</pitch><tpc>{tpc}</tpc></Note></Chord>")


def _score():
    bars = [HEAD + _chord("half.", 76), _chord("half.", 74), _chord("half.", 72)]
    measures = "".join(f"<Measure><voice>{bar}</voice></Measure>" for bar in bars)
    return etree.fromstring(
        "<museScore version='3.02'><Score>"
        '<Part><trackName>S1</trackName><Staff id="1"/></Part>'
        f'<Staff id="1">{measures}</Staff></Score></museScore>')


LOWER = {1: [{"value": "note_4", "pitches": [60], "tpcs": [14]},
             {"value": "note_2", "pitches": [60], "tpcs": [14]}],
         2: [{"value": "note_2.", "pitches": [60], "tpcs": [14]}]}


@pytest.fixture
def root():
    root = _score()
    apply_fixes(root, [{"kind": "voice2", "staff": 1, "measure": m, "from": [], "to": to,
                        "why": "lower line"} for m, to in LOWER.items()])
    return root


def _tie(root, measure, index, kind="tie", **more):
    return {"kind": kind, "staff": 1, "measure": measure, "voice": 2, "index": index,
            "pitch": 60, "from": voice2_tokens(root, 1, measure), "why": "held", **more}


def _ties(root, measure, voice):
    return _measure(root, 1, measure).findall("voice")[voice - 1].findall(
        ".//Spanner[@type='Tie']")


def _slots(root, measure, voice=2):
    staff = root.find(".//Score/Staff")
    return [takes for m, _, takes in lyric_txt._walk_voice(staff, voice) if m == measure]


def test_voice_2_ties_inside_its_bar_and_into_the_next(root):
    done = apply_fixes(root, [_tie(root, 1, 0), _tie(root, 1, 1)])
    assert "to the next chord" in done[0]
    assert "to the first chord of m2" in done[1]
    assert len(_ties(root, 1, 2)) == 3      # start, end, start
    assert len(_ties(root, 2, 2)) == 1      # the end half in bar 2's voice 2
    across = _ties(root, 1, 2)[2]
    assert across.findtext("next/location/measures") == "1"
    assert _ties(root, 2, 2)[0].findtext("prev/location/measures") == "-1"
    # Held, so sung once: the two tied-into notes take no syllable.
    assert _slots(root, 1) == [True, False]
    assert _slots(root, 2) == [False]
    # Voice 1 is left as it was.
    assert not root.findall(".//Measure/voice[1]//Spanner[@type='Tie']")
    assert bar_tokens(root, 1, 2) == ["half.:74"]


def test_untie_takes_a_voice_2_tie_out_both_halves(root):
    apply_fixes(root, [_tie(root, 1, 1)])
    done = apply_fixes(root, [_tie(root, 1, 1, kind="untie")])
    assert "to m2" in done[0]
    assert not root.findall(".//Spanner[@type='Tie']")
    assert _slots(root, 2) == [True]


def test_voice_2s_from_and_index_are_its_own(root):
    with pytest.raises(FixError, match="voice 2 reads"):
        apply_fixes(root, [_tie(root, 1, 0, **{"from": bar_tokens(root, 1, 1)})])
    with pytest.raises(FixError, match="no index 2"):
        apply_fixes(root, [_tie(root, 1, 2)])


def test_a_bar_without_voice_2_refuses(root):
    with pytest.raises(FixError, match="m3 has no voice 2"):
        apply_fixes(root, [_tie(root, 3, 0, **{"from": []})])
    # The tie out of bar 2 would land in bar 3, which has no second line.
    with pytest.raises(FixError, match="m3 has no voice 2"):
        apply_fixes(root, [_tie(root, 2, 0)])


def test_voice_1_is_still_the_default(root):
    fix = {"kind": "tie", "staff": 1, "measure": 1, "index": 0, "pitch": 76,
           "from": bar_tokens(root, 1, 1), "why": "x"}
    with pytest.raises(FixError, match="the next chord has no note at pitch 76"):
        apply_fixes(root, [fix])
