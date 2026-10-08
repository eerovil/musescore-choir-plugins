"""A bar a recorded fix has answered stops showing red (#347).

homr marks a printed staff, and on a staff two parts share the mark lands on the first
part the staff becomes while its red notes stay on both. So an answer recorded on one
of them has to find the mark on the other, and once no mark is left on the bar its red
notes go too. The findings count is read off the marks, so it follows.
"""
from lxml import etree

from src.clean_score.utils.problem_marks import mark_bar, marks
from src.clean_score.utils.score_fixes import apply_fixes, bar_tokens

from .scorebuilder import build_score


def _score():
    """T1+T2 from printed staff 1, B1+B2 from printed staff 2, two bars of eighths."""
    return build_score(staff_ids=(1, 2, 3, 4), measures=2, chords=8,
                       names={1: "T1", 2: "T2", 3: "B1", 4: "B2"}, staff_map="1:1,2;2:3,4")


def _bar(root, staff, measure):
    return root.findall(".//Score/Staff")[staff - 1].findall("Measure")[measure - 1]


def _redden(root, staff, measure, chord=0):
    note = _bar(root, staff, measure).findall(".//Chord")[chord].find("Note")
    etree.SubElement(note, "color", r="255", g="0", b="0", a="255")


def _red(root):
    return sorted((si, mi) for si, staff in enumerate(root.findall(".//Score/Staff"), 1)
                  for mi, bar in enumerate(staff.findall("Measure"), 1)
                  for c in bar.iter("color") if c.getparent().tag == "Note")


def test_unmark_on_one_part_of_a_shared_staff_takes_the_red_notes_off_both():
    root = _score()
    mark_bar(_bar(root, 1, 1), "voice?")
    _redden(root, 1, 1)
    _redden(root, 2, 1)
    _redden(root, 2, 2)
    done = apply_fixes(root, [{"kind": "unmark", "staff": 2, "measure": 1,
                               "text": "voice?", "why": "checked"}])
    assert marks(root) == []
    assert _red(root) == [(2, 2)]  # another bar is not answered
    assert "took the red mark off" in done[0] and "red notes black" in done[0]


def test_an_unmark_with_its_mark_already_gone_still_settles_the_bar():
    root = _score()
    _redden(root, 4, 2)
    apply_fixes(root, [{"kind": "unmark", "staff": 3, "measure": 2, "text": "slur?"}])
    assert _red(root) == []


def test_red_notes_stay_while_any_mark_on_the_bar_is_unanswered():
    root = _score()
    mark_bar(_bar(root, 1, 1), "voice?")
    mark_bar(_bar(root, 1, 1), "slur from T2 bar 1 removed; check the page")
    _redden(root, 2, 1)
    apply_fixes(root, [{"kind": "unmark", "staff": 1, "measure": 1, "text": "voice?"}])
    assert [m["text"] for m in marks(root)] == ["slur from T2 bar 1 removed; check the page"]
    assert _red(root) == [(2, 1)]


def test_a_staff_from_another_printed_staff_is_not_touched():
    root = _score()
    mark_bar(_bar(root, 3, 1), "voice?")
    _redden(root, 3, 1)
    apply_fixes(root, [{"kind": "unmark", "staff": 1, "measure": 1, "text": "voice?"}])
    assert len(marks(root)) == 1 and _red(root) == [(3, 1)]


def test_a_pick_on_the_second_part_answers_the_mark_on_the_first():
    root = _score()
    mark_bar(_bar(root, 3, 1), "pitch? voice?")
    _redden(root, 4, 1)
    apply_fixes(root, [{"kind": "pitch", "staff": 4, "measure": 1, "index": 0,
                        "from": bar_tokens(root, 4, 1), "was": 60, "to": 62, "tpc": 16}])
    assert [m["text"] for m in marks(root)] == ["voice?"]  # #290: voice? is not a pitch
    apply_fixes(root, [{"kind": "unmark", "staff": 3, "measure": 1, "text": "voice?"}])
    assert marks(root) == [] and _red(root) == []


def test_a_tie_or_slur_written_from_the_page_answers_the_mark_asking_about_it():
    root = _score()
    mark_bar(_bar(root, 1, 1), "slur? tie?")
    _redden(root, 2, 1, chord=3)
    apply_fixes(root, [{"kind": "slur", "staff": 2, "measure": 1, "index": 2, "span": 1}])
    assert [m["text"] for m in marks(root)] == ["tie?"]
    assert _red(root) == [(2, 1)]
    apply_fixes(root, [{"kind": "tie", "staff": 2, "measure": 1, "index": 5, "pitch": 60,
                        "from": bar_tokens(root, 2, 1)}])
    assert marks(root) == [] and _red(root) == []


def test_a_per_system_score_finds_the_siblings_of_that_system():
    """Per-system scores regroup staves by system, so the map for the bar decides."""
    root = build_score(
        staff_ids=(1, 2, 3), measures=2, chords=8, names={1: "T1", 2: "T2", 3: "B"},
        system_map=[{"start": 1, "end": 1, "map": {"1": [1, 2], "2": [3]}},
                    {"start": 2, "end": 2, "map": {"1": [1], "2": [2, 3]}}])
    mark_bar(_bar(root, 1, 2), "voice?")
    mark_bar(_bar(root, 2, 2), "voice?")
    _redden(root, 2, 1)
    _redden(root, 3, 2)
    apply_fixes(root, [{"kind": "unmark", "staff": 1, "measure": 1, "text": "voice?"},
                       {"kind": "unmark", "staff": 3, "measure": 2, "text": "voice?"}])
    assert [(m["staff"], m["measure"]) for m in marks(root)] == [(1, 2)]
    assert _red(root) == []
