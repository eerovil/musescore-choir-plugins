"""What a staff of its own has to draw that a shared staff did not (#354).

The shapes are the ones found on Laulajain lippu, Mieslaulu and Jouluyö: a rest
the scan hid because the other voice printed it, a double barline written
before the bar's last rest, a double barline only the upper voice's staff
kept, and a plain barline that blocked the final barline on one staff.
"""
from lxml import etree

from src.clean_score.utils.staff_display import (
    barlines_to_bar_end, drop_plain_final_barlines, fix_staff_display,
    share_end_barlines, show_lone_rests)


def score(*staves):
    """``staves`` is a list per staff of bars, each bar a list of voice bodies."""
    body = "".join(
        f'<Staff id="{n}">' + "".join(
            "<Measure>" + "".join(f"<voice>{v}</voice>" for v in bar) + "</Measure>"
            for bar in bars) + "</Staff>"
        for n, bars in enumerate(staves, 1))
    return etree.fromstring(f'<museScore><Score>{body}</Score></museScore>')


CHORD = "<Chord><durationType>quarter</durationType><Note><pitch>60</pitch></Note></Chord>"
REST = "<Rest><durationType>quarter</durationType></Rest>"
HIDDEN = ("<Rest><visible>0</visible><dots>1</dots><durationType>eighth</durationType>"
          "<NoteDot><visible>0</visible></NoteDot></Rest>")


def barline(kind=None):
    return f"<BarLine><subtype>{kind}</subtype></BarLine>" if kind else "<BarLine/>"


def tags(voice):
    return [c.tag for c in voice]


def test_a_hidden_rest_on_a_staff_of_its_own_is_shown():
    root = score([[CHORD + HIDDEN + CHORD]])
    assert show_lone_rests(root) == 1
    assert not root.findall(".//visible")


def test_a_hidden_rest_beside_another_voice_stays_hidden():
    """On a staff two voices still share, the other voice prints that rest."""
    root = score([[CHORD + HIDDEN, CHORD + REST]])
    assert show_lone_rests(root) == 0
    assert len(root.findall(".//visible")) == 2


def test_a_barline_before_the_last_rest_moves_to_the_end_of_the_bar():
    root = score([[CHORD + CHORD + CHORD + barline("double") + REST]])
    assert barlines_to_bar_end(root) == 1
    assert tags(root.find(".//voice")) == ["Chord", "Chord", "Chord", "Rest", "BarLine"]


def test_a_barline_already_at_the_end_stays():
    root = score([[CHORD + REST + barline("double")]])
    assert barlines_to_bar_end(root) == 0


def test_the_lower_staff_gets_the_double_barline_the_split_left_upstairs():
    root = score([[CHORD + barline("double")], [CHORD + REST]],
                 [[CHORD], [CHORD]])
    assert share_end_barlines(root) == 1
    lower = root.findall(".//Staff")[1].findall("Measure")[0]
    assert lower.find("voice/BarLine/subtype").text == "double"


def test_a_plain_barline_is_not_shared():
    root = score([[CHORD + barline()]], [[CHORD]])
    assert share_end_barlines(root) == 0
    assert root.findall(".//Staff")[1].find(".//BarLine") is None


def test_a_staff_with_a_barline_of_its_own_keeps_it():
    root = score([[CHORD + barline("double")]], [[CHORD + barline("end-repeat")]])
    assert share_end_barlines(root) == 0
    assert root.findall(".//Staff")[1].find(".//subtype").text == "end-repeat"


def test_a_plain_barline_on_the_last_bar_goes_so_the_final_one_is_drawn():
    """Jouluyö T1: homr read the final barline as heavy-heavy, MuseScore 3 wrote
    a plain one, and on the last bar that overrides the final barline."""
    root = score([[CHORD + barline()], [CHORD + barline()]], [[CHORD], [CHORD]])
    assert drop_plain_final_barlines(root) == 1
    first = root.findall(".//Staff")[0].findall("Measure")
    assert first[0].find(".//BarLine") is not None      # not the last bar: left alone
    assert first[1].find(".//BarLine") is None


def test_a_final_barline_on_the_last_bar_stays():
    root = score([[CHORD + barline("end")]])
    assert drop_plain_final_barlines(root) == 0


def test_running_it_twice_changes_nothing():
    root = score([[CHORD + HIDDEN + barline("double") + REST], [CHORD + barline()]],
                 [[CHORD + HIDDEN + CHORD], [CHORD]])
    first = fix_staff_display(root)
    assert sum(first.values()) == 5
    assert sum(fix_staff_display(root).values()) == 0


def test_a_final_barline_is_not_copied():
    """MuseScore draws it on every staff by itself, so an ordinary score must
    come through unchanged -- otherwise every video renders from a copy."""
    root = score([[CHORD + barline("end")], [CHORD + barline("end")]], [[CHORD], [CHORD]])
    assert fix_staff_display(root) == {"rests": 0, "moved": 0, "dropped": 0, "shared": 0}


def test_ordinary_test_scores_pass_through_unchanged():
    import glob
    import os
    here = os.path.dirname(__file__)
    files = glob.glob(os.path.join(here, "..", "..", "scrollvideo", "tests", "test_files", "*.mscx"))
    files.append(os.path.join(here, "test_files", "simple_1_output.mscx"))
    assert files
    for path in files:
        root = etree.parse(path).getroot()
        assert not any(fix_staff_display(root).values()), path
