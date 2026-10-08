"""The `timesig` fix (#353): take off, or rewrite, a time signature the scan invented.

Kesäaamu is printed in 6/8 throughout, and homr read a 3/4 at bar 22 on every staff.
The notes fit both meters, so `spurious_timesigs` cannot see it; only the page can,
and until this kind there was no way to record what the page says. The score below
is that shape in four bars on two staves: 6/8 on bar 1, an invented 3/4 on bar 3.
"""
import json

import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, apply_fixes, bar_tokens


def _sig(n, d, extra=""):
    return f"<TimeSig>{extra}<sigN>{n}</sigN><sigD>{d}</sigD></TimeSig>"


def _bar(sig=""):
    chords = "".join(f"<Chord><durationType>eighth</durationType><Note><pitch>{p}</pitch>"
                     f"<tpc>14</tpc></Note></Chord>" for p in (60, 62, 64, 65, 67, 69))
    return f"<Measure><voice>{sig}{chords}</voice></Measure>"


def _score(third=None):
    third = third if third is not None else {1: _sig(3, 4), 2: _sig(3, 4)}
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "T1"), (2, "B1")))
    staves = "".join(
        f'<Staff id="{sid}">{_bar(_sig(6, 8))}{_bar()}{_bar(third[sid])}{_bar()}</Staff>'
        for sid in (1, 2))
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


REMOVE = {"kind": "timesig", "measure": 3, "from": "3/4", "to": None,
          "why": "the page prints 6/8 throughout"}


def _signs(root, measure):
    return [[f"{t.findtext('sigN')}/{t.findtext('sigD')}" for t in
             staff.findall("Measure")[measure - 1].iter("TimeSig")]
            for staff in root.findall("Score/Staff")]


def test_it_takes_the_signature_off_every_staff_and_leaves_the_notes():
    root = _score()
    before = [bar_tokens(root, s, 3) for s in (1, 2)]
    done = apply_fixes(root, [REMOVE])
    assert _signs(root, 3) == [[], []]
    assert _signs(root, 1) == [["6/8"], ["6/8"]]
    assert [bar_tokens(root, s, 3) for s in (1, 2)] == before
    assert "3/4" in done[0] and "2 staves" in done[0]


def test_it_can_write_another_signature_of_the_same_length():
    root = _score({1: _sig(3, 4, "<groups/>"), 2: _sig(3, 4, "<groups/>")})
    apply_fixes(root, [{**REMOVE, "to": "6/8"}])
    assert _signs(root, 3) == [["6/8"], ["6/8"]]
    assert root.find(".//TimeSig/groups") is None   # 3/4's beaming does not survive


def test_one_staff_reading_otherwise_refuses_and_changes_nothing():
    root = _score({1: _sig(3, 4), 2: _sig(6, 8)})
    with pytest.raises(FixError, match=r"m3 \(timesig\).*staff 2 reads 6/8"):
        apply_fixes(root, [REMOVE])
    assert _signs(root, 3) == [["3/4"], ["6/8"]]


def test_a_bar_with_no_signature_refuses():
    root = _score({1: "", 2: ""})
    with pytest.raises(FixError, match="0 time signatures"):
        apply_fixes(root, [REMOVE])


def test_from_is_required():
    with pytest.raises(FixError, match="from"):
        apply_fixes(_score(), [{**REMOVE, "from": None}])


def test_the_opening_signature_is_never_touched():
    root = _score()
    with pytest.raises(FixError, match="opening"):
        apply_fixes(root, [{**REMOVE, "measure": 1, "from": "6/8"}])
    assert _signs(root, 1) == [["6/8"], ["6/8"]]


def test_a_change_of_bar_length_refuses():
    root = _score({1: _sig(2, 4), 2: _sig(2, 4)})
    with pytest.raises(FixError, match="different lengths"):
        apply_fixes(root, [{**REMOVE, "from": "2/4"}])          # 6/8 is in force
    with pytest.raises(FixError, match="different lengths"):
        apply_fixes(root, [{**REMOVE, "from": "2/4", "to": "4/4"}])
    assert _signs(root, 3) == [["2/4"], ["2/4"]]


def test_a_delbar_before_it_moves_its_bar():
    """Fixes count bars in file order, as for every whole-score kind."""
    root = _score()
    for staff in root.findall("Score/Staff"):              # an empty bar 2 to delete
        staff.findall("Measure")[1].find("voice").clear()
        etree.SubElement(staff.findall("Measure")[1].find("voice"), "Rest").extend(
            [etree.fromstring("<durationType>measure</durationType>"),
             etree.fromstring("<duration>6/8</duration>")])
    apply_fixes(root, [{"kind": "delbar", "measure": 2, "from": ["measure:R"], "why": "x"},
                       {**REMOVE, "measure": 2}])
    assert _signs(root, 2) == [[], []]


def test_a_recorded_entry_replays_on_a_rebuild(tmp_path):
    from src.song_app.pipeline import apply_recorded_fixes
    cleaned = tmp_path / "s_cleaned.mscx"
    etree.ElementTree(_score()).write(str(cleaned))
    (tmp_path / "fixes.json").write_text(json.dumps([REMOVE]))
    assert apply_recorded_fixes(str(cleaned), str(tmp_path)) == 1
    assert _signs(etree.parse(str(cleaned)).getroot(), 3) == [[], []]
