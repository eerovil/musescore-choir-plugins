"""A rest that fills its bar alone is written as a bar rest, so MuseScore centres it (#298)."""
from lxml import etree

from src.clean_score.utils.measure_rests import centre_measure_rests, fit_measure_rests


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


def test_a_rest_shifted_by_a_location_is_left_alone():
    body = "<location><fractions>1/4</fractions></location>" + _rest("whole")
    root = _score((body, ""))
    assert centre_measure_rests(root) == 0
    assert _first_rest(root).findtext("durationType") == "whole"


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


# A bar rest whose length is not its bar's: a scanned song came back with bar rests of
# 4/4 in a 3/4 piece, and MuseScore refused the score when publishing it.

def _bar_rest(length, extra=""):
    return f"<Rest>{extra}<durationType>measure</durationType><duration>{length}</duration></Rest>"


def test_a_four_four_bar_rest_in_three_four_is_fitted_to_the_bar():
    root = _score((_bar_rest("4/4"), ""), sig="3/4")
    assert fit_measure_rests(root) == 1
    assert _is_bar_rest(_first_rest(root), "3/4")


def test_a_bar_rest_takes_the_bars_own_length_over_the_signature():
    root = _score((_bar_rest("3/4"), ""), (_bar_rest("4/4"), ' len="6/8"'), sig="3/4")
    assert fit_measure_rests(root) == 1
    assert _is_bar_rest(_first_rest(root, 2), "6/8")


def test_a_bar_rest_of_the_right_length_is_left_alone():
    root = _score((_bar_rest("3/4"), ""), sig="3/4")
    before = etree.tostring(root)
    assert fit_measure_rests(root) == 0
    assert etree.tostring(root) == before


def test_a_bar_whose_length_cannot_be_read_is_left_alone():
    root = _score((_bar_rest("4/4"), ""), sig="4/0")
    before = etree.tostring(root)
    assert fit_measure_rests(root) == 0
    assert etree.tostring(root) == before


def test_a_hidden_bar_rest_is_fitted_and_stays_hidden():
    root = _score((_bar_rest("4/4", "<visible>0</visible>"), ""), sig="3/4")
    assert fit_measure_rests(root) == 1
    rest = _first_rest(root)
    assert _is_bar_rest(rest, "3/4") and rest.findtext("visible") == "0"


def test_a_bar_after_a_time_signature_change_takes_the_new_meter():
    xml = ("<museScore><Score><Staff id=\"1\">"
           "<Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"
           + _bar_rest("4/4") + "</voice></Measure>"
           "<Measure><voice><TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>"
           + _bar_rest("4/4") + "</voice></Measure>"
           "<Measure><voice>" + _bar_rest("4/4") + "</voice></Measure>"
           "</Staff></Score></museScore>")
    root = etree.fromstring(xml.encode())
    assert fit_measure_rests(root) == 2
    assert _is_bar_rest(_first_rest(root, 1), "4/4")
    assert _is_bar_rest(_first_rest(root, 2), "3/4")
    assert _is_bar_rest(_first_rest(root, 3), "3/4")


def test_only_the_staff_with_the_wrong_bar_rest_changes():
    ts = "<TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>"
    xml = ("<museScore><Score>"
           f"<Staff id=\"1\"><Measure><voice>{ts}{_bar_rest('3/4')}</voice></Measure></Staff>"
           f"<Staff id=\"2\"><Measure><voice>{ts}{_bar_rest('4/4')}</voice></Measure></Staff>"
           "</Score></museScore>")
    root = etree.fromstring(xml.encode())
    assert fit_measure_rests(root) == 1
    first, second = [s.find("Measure/voice/Rest") for s in root.findall(".//Staff")]
    assert _is_bar_rest(first, "3/4") and _is_bar_rest(second, "3/4")


def test_the_clean_logs_how_many_bar_rests_it_fitted(caplog):
    from src.clean_score.main import _centre_measure_rests
    root = _score((_bar_rest("4/4"), ""), (_bar_rest("4/4"), ""), sig="3/4")
    with caplog.at_level("INFO"):
        _centre_measure_rests(root)
    assert "Fitted 2 bar rest(s) to their bar's length" in caplog.text
