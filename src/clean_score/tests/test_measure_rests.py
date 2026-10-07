"""A rest that fills its bar alone is written as a bar rest, so MuseScore centres it (#298)."""
from lxml import etree

from src.clean_score.utils.measure_rests import centre_measure_rests


def _score(*bars, sig="4/4"):
    n, d = sig.split("/")
    ts = f"<TimeSig><sigN>{n}</sigN><sigD>{d}</sigD></TimeSig>"
    measures = []
    for i, (body, attr) in enumerate(bars):
        head = ts if i == 0 else ""
        measures.append(f"<Measure{attr}><voice>{head}{body}</voice></Measure>")
    xml = f'<museScore><Score><Staff id="1">{"".join(measures)}</Staff></Score></museScore>'
    return etree.fromstring(xml.encode())


def _rest(kind, extra=""):
    return f"<Rest>{extra}<durationType>{kind}</durationType></Rest>"


def _first_rest(root, bar=1):
    return root.findall(".//Measure")[bar - 1].find("voice/Rest")


def _is_bar_rest(rest, length):
    return rest.findtext("durationType") == "measure" and rest.findtext("duration") == length


def test_a_whole_rest_alone_in_four_four_becomes_a_bar_rest():
    root = _score((_rest("whole"), ""))
    assert centre_measure_rests(root) == 1
    assert _is_bar_rest(_first_rest(root), "4/4")


def test_a_whole_rest_alone_in_cut_time_becomes_a_bar_rest():
    root = _score((_rest("whole"), ""), sig="2/2")
    centre_measure_rests(root)
    assert _is_bar_rest(_first_rest(root), "2/2")


def test_a_half_rest_alone_in_two_four_becomes_a_bar_rest():
    root = _score((_rest("half"), ""), sig="2/4")
    centre_measure_rests(root)
    assert _is_bar_rest(_first_rest(root), "2/4")


def test_a_whole_rest_in_three_four_is_left_alone():
    root = _score((_rest("whole"), ""), sig="3/4")
    assert centre_measure_rests(root) == 0
    assert _first_rest(root).findtext("durationType") == "whole"


def test_a_dotted_rest_is_left_alone():
    root = _score((_rest("half", "<dots>1</dots>"), ""), sig="3/4")
    assert centre_measure_rests(root) == 0


def test_a_rest_in_a_tuplet_is_left_alone():
    body = ("<Tuplet id=\"1\"><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>"
            + _rest("whole", "<Tuplet>1</Tuplet>"))
    root = _score((body, ""))
    assert centre_measure_rests(root) == 0


def test_a_rest_beside_a_note_is_left_alone():
    chord = "<Chord><durationType>half</durationType><Note><pitch>60</pitch></Note></Chord>"
    root = _score((chord + _rest("half"), ""), sig="4/4")
    assert centre_measure_rests(root) == 0


def test_the_bars_own_length_wins_over_the_signature():
    root = _score((_rest("whole"), ""), (_rest("half"), ' len="2/4"'))
    centre_measure_rests(root)
    assert _is_bar_rest(_first_rest(root, 2), "2/4")


def test_a_hidden_rest_stays_hidden():
    root = _score((_rest("whole", "<visible>0</visible>"), ""))
    centre_measure_rests(root)
    rest = _first_rest(root)
    assert _is_bar_rest(rest, "4/4") and rest.findtext("visible") == "0"


def test_a_second_run_changes_nothing_and_a_bar_rest_is_untouched():
    root = _score((_rest("whole"), ""),
                  ("<Rest><durationType>measure</durationType><duration>4/4</duration></Rest>", ""))
    centre_measure_rests(root)
    before = etree.tostring(root)
    assert centre_measure_rests(root) == 0
    assert etree.tostring(root) == before
