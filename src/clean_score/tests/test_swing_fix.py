"""The `swing` fix (eeron-omat-taskit#52): put back a swing marking the scan lost.

Ketun joululaulu prints ♫ = ♩♪ (in triplets) over bar 1, ♫ = ♫ at its key change and
the swing marking again later; homr reads none of them, so the song played straight.
The score below is two staves of four bars of eighths.
"""
import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, apply_fixes, bar_tokens


def _bar():
    chords = "".join(f"<Chord><durationType>eighth</durationType><Note><pitch>{p}</pitch>"
                     f"<tpc>14</tpc></Note></Chord>" for p in (60, 62, 64, 65, 67, 69, 71, 72))
    return f"<Measure><voice><Beam id='1'/>{chords}</voice></Measure>"


def _score():
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "S1"), (2, "A1")))
    staves = "".join(f'<Staff id="{sid}">{_bar() * 4}</Staff>' for sid in (1, 2))
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


def _marks(root, measure):
    """(staff id, word, swing unit, ratio) of each swing marking in that bar."""
    return [(staff.get("id"), el.findtext("text"), el.find("swing").get("unit"),
             el.find("swing").get("ratio"))
            for staff in root.findall("Score/Staff")
            for el in staff.findall("Measure")[measure - 1].iter("SystemText", "StaffText")
            if el.find("swing") is not None]


def test_swing_goes_on_the_top_staff_before_the_first_note():
    root = _score()
    before = [bar_tokens(root, s, 1) for s in (1, 2)]
    done = apply_fixes(root, [{"kind": "swing", "measure": 1, "swing": True,
                               "why": "the page prints ♫ = ♩♪ in triplets"}])
    assert done == ["m1: swing eighths from here — the page prints ♫ = ♩♪ in triplets"]
    assert _marks(root, 1) == [("1", "Swing", "eighth", "67")]
    voice = root.find("Score/Staff/Measure/voice")
    assert [c.tag for c in voice][:3] == ["Beam", "SystemText", "Chord"]
    assert [bar_tokens(root, s, 1) for s in (1, 2)] == before


def test_straight_turns_it_off():
    root = _score()
    apply_fixes(root, [{"kind": "swing", "measure": 1, "swing": True, "why": "a"},
                       {"kind": "swing", "measure": 3, "swing": False, "why": "b"}])
    assert _marks(root, 3) == [("1", "Straight", "zero", "67")]
    assert _marks(root, 1) == [("1", "Swing", "eighth", "67")]


def test_a_marking_already_in_the_bar_is_replaced():
    root = _score()
    apply_fixes(root, [{"kind": "swing", "measure": 2, "swing": True, "why": "a"},
                       {"kind": "swing", "measure": 2, "swing": False, "why": "b"}])
    assert _marks(root, 2) == [("1", "Straight", "zero", "67")]


@pytest.mark.parametrize("fix, said", [
    ({"kind": "swing", "measure": 5, "swing": True}, "bars 1-4, not 5"),
    ({"kind": "swing", "measure": 0, "swing": True}, "bars 1-4, not 0"),
    ({"kind": "swing", "measure": 2, "swing": "yes"}, "true or false"),
    ({"kind": "swing", "measure": 2}, "true or false"),
])
def test_it_refuses_what_it_cannot_do(fix, said):
    with pytest.raises(FixError, match=said):
        apply_fixes(_score(), [fix])
