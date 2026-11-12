"""`endrepeat`: an end repeat played N times, for a strophic song that prints its verses
under one pass of the notes and no repeat signs (Kesän kukkiva maa, three verses)."""
import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, apply_fixes

WHOLE = "<Rest><durationType>whole</durationType></Rest>"


def _score(bars=3):
    staves = "".join(
        f'<Staff id="{sid}">' + "".join(f"<Measure><voice>{WHOLE}</voice></Measure>"
                                       for _ in range(bars)) + "</Staff>"
        for sid in (1, 2))
    return etree.fromstring(f"<museScore version='3.02'><Score>{staves}</Score></museScore>")


def _bars(root, measure):
    return [s.findall("Measure")[measure - 1] for s in root.findall(".//Score/Staff")]


def test_end_repeat_played_three_times_on_every_staff():
    root = _score()
    done = apply_fixes(root, [
        {"kind": "repeat", "measure": 1, "why": "three verses, no repeat printed"},
        {"kind": "endrepeat", "measure": 3, "times": 3, "why": "three verses, no repeat printed"}])
    for bar in _bars(root, 3):
        assert bar.findtext("endRepeat") == "3"
        # Ahead of the voice, as MuseScore writes it.
        assert [c.tag for c in bar] == ["endRepeat", "voice"]
    assert any("played 3 times" in line for line in done)


def test_times_defaults_to_two_and_goes_after_a_start_sign():
    root = _score(1)
    apply_fixes(root, [{"kind": "repeat", "measure": 1, "why": "x"},
                       {"kind": "endrepeat", "measure": 1, "why": "x"}])
    for bar in _bars(root, 1):
        assert [c.tag for c in bar] == ["startRepeat", "endRepeat", "voice"]
        assert bar.findtext("endRepeat") == "2"


@pytest.mark.parametrize("fix, says", [
    ({"kind": "endrepeat", "measure": 9, "why": "x"}, "has no measure 9"),
    ({"kind": "endrepeat", "measure": 2, "times": 1, "why": "x"}, "is no repeat"),
])
def test_refuses(fix, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(_score(), [fix])


def test_refuses_a_bar_that_already_ends_a_repeat():
    root = _score()
    fix = {"kind": "endrepeat", "measure": 2, "why": "x"}
    apply_fixes(root, [fix])
    with pytest.raises(FixError, match="already ends here"):
        apply_fixes(root, [fix])
