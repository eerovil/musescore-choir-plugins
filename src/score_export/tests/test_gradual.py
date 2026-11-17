"""A printed rit. or accel. eases the tempo beat by beat in the publish copy (#483).

MuseScore plays a "rit." text at the speed before it, so a song slowing into its
last bars played straight through, and so did the voices sung off its clock.
"""

import pytest
from lxml import etree

from src.score_export.clock import TempoMap
from src.score_export.musescore import run_musescore
from src.score_export.score import add_gradual, add_tempo_changes, gradual_marks, prepare
from .conftest import needs_musescore


def _score(bars, staves=1, opening=60):
    """`bars` is a list of bars, each a list of (quarters, text or None, fermata?).

    Every staff gets the same notes; the texts and fermatas go on the top staff."""
    def bar(n, items, top):
        out = ["<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>" if n == 0 else ""]
        if n == 0 and top and opening:
            out.append(f"<Tempo><tempo>{opening / 60}</tempo></Tempo>")
        for quarters, text, fermata in items:
            if top and text:
                out.append(f"<StaffText><text>{text}</text></StaffText>")
            if top and fermata:
                out.append("<Fermata><subtype>fermataAbove</subtype></Fermata>")
            kind = {1: "quarter", 2: "half", 4: "whole"}[quarters]
            out.append(f"<Chord><durationType>{kind}</durationType>"
                       "<Note><pitch>60</pitch><tpc>14</tpc></Note></Chord>")
        return "<Measure><voice>" + "".join(out) + "</voice></Measure>"

    staff_xml = "".join(
        f'<Staff id="{s}">' + "".join(bar(n, items, s == 1) for n, items in enumerate(bars))
        + "</Staff>" for s in range(1, staves + 1))
    return etree.fromstring(f"<museScore><Score>{staff_xml}</Score></museScore>")


Q = (1, None, False)


def _quarters(text_at=None, count=4, **marks):
    """`count` bars of four quarters; marks={(bar, beat): text} on the top staff."""
    bars = []
    for b in range(1, count + 1):
        bars.append([(1, marks.get(f"b{b}q{q}"), False) for q in range(1, 5)])
    return bars


def _speeds(root):
    """(bar, number of the chord it stands before, bpm) of every tempo mark on the
    top staff, in order."""
    out = []
    for n, measure in enumerate(root.find("./Score/Staff").findall("Measure"), 1):
        beat = 0
        for element in measure.find("voice"):
            if element.tag == "Tempo":
                out.append((n, beat + 1, round(float(element.findtext("tempo")) * 60, 2)))
            elif element.tag == "Chord":
                beat += 1
    return out


def test_the_marks_the_score_prints_are_listed_once_each():
    root = _score(_quarters(b2q1="poco rit.", b3q3="a tempo", b4q1="accel."), staves=2)
    root.find("./Score/Staff[2]/Measure[2]/voice").insert(
        0, etree.fromstring("<StaffText><text>poco rit.</text></StaffText>"))
    assert gradual_marks(root) == [
        {"measure": 2, "kind": "rit", "text": "poco rit."},
        {"measure": 4, "kind": "accel", "text": "accel."}]


@pytest.mark.parametrize("text", ["rit.", "ritard.", "rall.", "ritardando", "Rit.",
                                  "dim. e rit.", "poco a poco rallentando"])
def test_every_spelling_of_slowing_down_is_a_rit(text):
    assert [m["kind"] for m in gradual_marks(_score(_quarters(b2q1=text)))] == ["rit"]


@pytest.mark.parametrize("text", ["ritmico", "spirito", "a tempo", "cresc."])
def test_words_that_only_look_alike_are_not(text):
    assert gradual_marks(_score(_quarters(b2q1=text))) == []


def test_a_rit_eases_to_three_quarters_by_the_last_beat_and_a_tempo_goes_back():
    root = _score(_quarters(b2q1="rit.", b3q1="a tempo"))
    add_gradual(root)
    # bar 2 beat 1 plays at 60; beats 2-4 ease to 45; bar 3 goes back to 60
    assert _speeds(root) == [(1, 1, 60), (2, 2, 55), (2, 3, 50), (2, 4, 45), (3, 1, 60)]


def test_an_accel_eases_up_and_runs_to_the_end_without_an_a_tempo():
    root = _score(_quarters(count=2, b2q1="accel."))
    add_gradual(root)
    assert _speeds(root)[-1] == (2, 4, 75)
    assert len(_speeds(root)) == 4


def test_the_ramp_stops_at_a_fermata_and_the_music_goes_on_at_the_old_speed():
    bars = _quarters(count=3, b1q3="rit.")
    bars[1][1] = (1, None, True)            # a fermata on bar 2 beat 2
    root = _score(bars)
    add_gradual(root)
    assert _speeds(root) == [(1, 1, 60), (1, 4, 52.5), (2, 1, 45), (2, 3, 60)]


def test_a_tempo_change_ends_the_ramp_and_keeps_its_own_speed():
    root = _score(_quarters(b1q1="rit."))
    add_tempo_changes(root, [{"measure": 3, "bpm": 100}])
    add_gradual(root)
    speeds = _speeds(root)
    assert speeds[-1] == (3, 1, 100)
    assert (2, 4, 45) in speeds


def test_a_second_rit_starts_from_where_the_first_left_off():
    root = _score(_quarters(count=3, b1q1="rit.", b2q1="rit."))
    add_gradual(root)
    speeds = _speeds(root)
    assert (1, 4, 45) in speeds
    assert speeds[-1] == (3, 4, 33.75)


def test_the_panel_sets_the_speed_or_switches_a_mark_off():
    root = _score(_quarters(b1q1="rit.", b2q1="a tempo", b3q1="accel."))
    add_gradual(root, [{"measure": 1, "bpm": 40, "off": False},
                       {"measure": 3, "bpm": None, "off": True}])
    assert _speeds(root) == [(1, 1, 60), (1, 2, 53.33), (1, 3, 46.67), (1, 4, 40),
                             (2, 1, 60)]


def test_a_mark_with_no_beat_left_is_played_as_written_and_said():
    said = []
    root = _score(_quarters(count=1, b1q4="rit."))
    assert add_gradual(root, log=said.append) == 0
    assert "bar 1" in said[0] and "played as written" in said[0]


def test_a_beat_where_nothing_starts_is_skipped_but_still_counted():
    root = _score([[Q] * 4, [(1, "rit.", False), (2, None, False), Q], [(4, "a tempo", False)]])
    add_gradual(root)
    # beat 3 of bar 2 is inside the half note: no mark, but beat 4 is still the last step
    assert _speeds(root) == [(1, 1, 60), (2, 2, 55), (2, 3, 45), (3, 1, 60)]


def test_prepare_eases_only_in_its_copy(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_bytes(etree.tostring(_score(_quarters(b2q1="rit."))))
    before = original.read_bytes()
    source, _ = prepare(str(original), str(tmp_path))
    assert source != str(original) and original.read_bytes() == before
    assert len(_speeds(etree.parse(source).getroot())) > 1


@needs_musescore
def test_musescore_plays_the_ramp(fermata_mscx, tmp_path):
    """The fixture's bar 1 is four quarters with a fermata on the last; a rit. on
    beat 1 eases beats 2 and 3, and bar 2 goes on at the speed before it."""
    root = etree.parse(fermata_mscx).getroot()
    voice = root.find("./Score/Staff/Measure/voice")
    first = next(i for i, e in enumerate(voice) if e.tag == "Chord")
    voice.insert(first, etree.fromstring("<StaffText><text>rit.</text></StaffText>"))
    marked = tmp_path / "rit.mscx"
    marked.write_bytes(etree.tostring(root))
    source, _ = prepare(str(marked), str(tmp_path), initial_bpm=80)
    tempo = TempoMap.from_midi(run_musescore(source, str(tmp_path / "r.mid")))

    def bpm_at(quarter):
        return round(60 * 0.5 / (tempo.seconds(quarter + 0.5) - tempo.seconds(quarter)))

    assert [bpm_at(q) for q in (0, 1, 2, 4)] == [80, 70, 60, 80]
