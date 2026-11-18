"""A dashed tie or slur in a sung part is marked and kept (#482).

Strophic songs print a later verse's tie or slur dashed. The fork passes the dashes
on and MuseScore keeps them as `<lineType>2</lineType>`; cleaning marks the bar,
and an `untie` or `unslur` fix answers the mark.
"""
from lxml import etree

from src.clean_score.main import mark_scan_damage
from src.clean_score.utils.dashed_arcs import mark_dashed_arcs
from src.clean_score.utils.problem_marks import marks
from src.clean_score.utils.score_fixes import apply_fixes, bar_tokens


def _tie(line_type):
    style = f"<lineType>{line_type}</lineType>" if line_type else ""
    return (f'<Spanner type="Tie"><Tie>{style}</Tie><next><location><fractions>1/4'
            '</fractions></location></next></Spanner>')


TIE_IN = ('<Spanner type="Tie"><prev><location><fractions>-1/4</fractions></location>'
          '</prev></Spanner>')


def _slur(line_type):
    style = f"<lineType>{line_type}</lineType>" if line_type else ""
    return (f'<Spanner type="Slur"><Slur>{style}</Slur><next><location><fractions>1/4'
            '</fractions></location></next></Spanner>')


SLUR_IN = ('<Spanner type="Slur"><prev><location><fractions>-1/4</fractions></location>'
           '</prev></Spanner>')


def _note(pitch, inside=""):
    return f"<Note>{inside}<pitch>{pitch}</pitch><tpc>14</tpc></Note>"


def _chord(pitch, inside=""):
    return f"<Chord><durationType>quarter</durationType>{_note(pitch, inside)}</Chord>"


def _tied_bar(line_type):
    return (_chord(60, _tie(line_type)) + _chord(60, TIE_IN) + _chord(62) + _chord(62))


def _slurred_bar(line_type):
    return (f"{_slur(line_type)}{_chord(60)}{SLUR_IN}{_chord(62)}" + _chord(64) + _chord(65))


HEAD = "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"


def _score(*staves):
    """`staves`: (name, [bar content, ...]) per staff."""
    parts = "".join(f'<Part><trackName>{name}</trackName><Staff id="{i}"/></Part>'
                    for i, (name, _) in enumerate(staves, start=1))
    body = "".join(
        f'<Staff id="{i}">' + "".join(f"<Measure><voice>{HEAD if m == 0 else ''}{bar}</voice></Measure>"
                                      for m, bar in enumerate(bars)) + "</Staff>"
        for i, (_, bars) in enumerate(staves, start=1))
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{body}</Score></museScore>")


def _ties(root):
    return [sp for sp in root.iter("Spanner") if sp.get("type") == "Tie"]


def test_a_dashed_tie_is_marked_and_kept():
    root = _score(("S1", [_chord(67) * 4, _tied_bar("2")]))
    assert mark_dashed_arcs(root) == [{"part": "S1", "measure": 2, "kind": "tie"}]
    assert marks(root) == [{"staff": 1, "measure": 2, "text": "dashed tie?"}]
    assert len(_ties(root)) == 2


def test_a_solid_or_dotted_tie_is_not_marked():
    root = _score(("S1", [_tied_bar(None)]), ("A1", [_tied_bar("1")]))
    assert mark_dashed_arcs(root) == []
    assert marks(root) == []


def test_a_dashed_slur_is_marked_and_kept():
    root = _score(("T1", [_slurred_bar("2")]))
    assert mark_dashed_arcs(root) == [{"part": "T1", "measure": 1, "kind": "slur"}]
    assert marks(root)[0]["text"] == "dashed slur?"
    assert len([sp for sp in root.iter("Spanner") if sp.get("type") == "Slur"]) == 2


def test_an_instrument_part_is_not_marked():
    root = _score(("Piano", [_tied_bar("2")]))
    assert mark_dashed_arcs(root) == []


def test_cleaning_marks_it_once_however_often_it_runs():
    root = _score(("S1", [_tied_bar("2")]))
    mark_scan_damage(root)
    mark_scan_damage(root)
    assert [m["text"] for m in marks(root)] == ["dashed tie?"]


def _fix(root, kind, staff, measure, **more):
    return {"kind": kind, "staff": staff, "measure": measure,
            "from": bar_tokens(root, staff, measure), "why": "page prints it dashed", **more}


def test_untie_answers_the_mark():
    root = _score(("S1", [_tied_bar("2")]))
    mark_dashed_arcs(root)
    done = apply_fixes(root, [_fix(root, "untie", 1, 1, index=0, pitch=60)])
    assert "dashed tie?" in done[0]
    assert marks(root) == [] and _ties(root) == []


def test_unslur_answers_the_mark():
    root = _score(("T1", [_slurred_bar("2")]))
    mark_dashed_arcs(root)
    done = apply_fixes(root, [_fix(root, "unslur", 1, 1, index=0)])
    assert "dashed slur?" in done[0]
    assert marks(root) == []


def test_a_new_tie_does_not_answer_it():
    root = _score(("S1", [_tied_bar("2")]))
    mark_dashed_arcs(root)
    apply_fixes(root, [_fix(root, "tie", 1, 1, index=2, pitch=62)])
    assert [m["text"] for m in marks(root)] == ["dashed tie?"]


def _two_ties(second):
    """A dashed tie on 60, then `second` ("2" dashed, None solid) on 62."""
    return (_chord(60, _tie("2")) + _chord(60, TIE_IN)
            + _chord(62, _tie(second)) + _chord(62, TIE_IN))


def test_taking_out_a_solid_tie_keeps_the_mark():
    root = _score(("S1", [_two_ties(None)]))
    mark_dashed_arcs(root)
    apply_fixes(root, [_fix(root, "untie", 1, 1, index=2, pitch=62)])
    assert [m["text"] for m in marks(root)] == ["dashed tie?"]


def test_taking_out_one_dashed_tie_of_two_keeps_the_mark():
    root = _score(("S1", [_two_ties("2")]))
    mark_dashed_arcs(root)
    apply_fixes(root, [_fix(root, "untie", 1, 1, index=0, pitch=60)])
    assert [m["text"] for m in marks(root)] == ["dashed tie?"]
    apply_fixes(root, [_fix(root, "untie", 1, 1, index=2, pitch=62)])
    assert marks(root) == []


def test_a_part_sharing_the_printed_staff_keeps_its_mark():
    root = _score(("S1", [_tied_bar("2")]), ("A1", [_tied_bar("2")]))
    root.find("Score").insert(0, etree.fromstring(
        '<metaTag name="lyricsStaffMap">1:1,2</metaTag>'))
    mark_dashed_arcs(root)
    apply_fixes(root, [_fix(root, "untie", 1, 1, index=0, pitch=60)])
    assert marks(root) == [{"staff": 2, "measure": 1, "text": "dashed tie?"}]
