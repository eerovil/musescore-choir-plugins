"""Where does a repeat start? (#312)

homr misses a start-repeat sign that opens a printed system, so the score keeps the
end sign and loses the start, and the practice track repeats the wrong bars. The Fix
stage asks about every end repeat with no start since the previous one, offering the
printed systems that begin in between; **a** leaves the score as it is. This pins the
question, both kinds of answer, the `repeat` fix kind they write, and the replay on a
re-clean.
"""
import json
import os

import pytest
from lxml import etree

from src.clean_score.utils import score_fixes
from src.clean_score.utils.score_fixes import FixError
from src.song_app import pdf_systems, pipeline, problems, state


def _score(bars=8, ends=(4, 8), starts=(), staves=2):
    """A cleaned score: ``staves`` single-voice staves of ``bars`` whole rests."""
    def measure(n):
        start = "<startRepeat/>" if n in starts else ""
        end = "<endRepeat>2</endRepeat>" if n in ends else ""
        return (f"<Measure>{start}{end}<voice><Rest><durationType>measure</durationType>"
                "<duration>4/4</duration></Rest></voice></Measure>")
    parts = "".join(f'<Part><trackName>T{n}</trackName><Staff id="{n}"/></Part>'
                    for n in range(1, staves + 1))
    body = "".join(f'<Staff id="{n}">' + "".join(measure(m) for m in range(1, bars + 1))
                   + "</Staff>" for n in range(1, staves + 1))
    return f'<museScore version="3.02"><Score>{parts}{body}</Score></museScore>'


@pytest.fixture
def song(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    made = state.create("Repeats", per_system=False)
    with open(made.path("song_cleaned.mscx"), "w") as fh:
        fh.write(_score())
    made.data["cleaned"] = "song_cleaned.mscx"
    made.save()
    # Four printed systems of two bars: 1-2, 3-4, 5-6, 7-8.
    pdf_systems.save_bounds(made.dir, [
        pdf_systems.SystemBounds(index=i, page=1, top=0.2 * i, bottom=0.2 * i + 0.15,
                                 measure_start=2 * i - 1, measure_end=2 * i)
        for i in range(1, 5)])
    return made


def _starts(song):
    root = etree.parse(song.cleaned_path()).getroot()
    return [[n for n, m in enumerate(staff.findall("Measure"), start=1)
             if m.find("startRepeat") is not None]
            for staff in root.findall(".//Score/Staff")]


def _fixes(song):
    path = song.path("fixes.json")
    return json.load(open(path)) if os.path.exists(path) else []


def _choices(song):
    return [(row["measure"], choice) for row in problems.problems(song)
            for choice in row["choices"] if choice["kind"] == "repeat"]


def test_each_end_repeat_with_no_start_is_asked_about(song):
    asked = _choices(song)
    assert [measure for measure, _ in asked] == [4, 8]
    first, second = (choice for _, choice in asked)
    # a leaves it alone; the rest are the systems that start since the previous end.
    assert [(o["letter"], o["current"]) for o in first["options"]] == [
        ("a", True), ("b", False), ("c", False)]
    assert "Bar 1 " in first["options"][1]["label"]
    assert "Bar 3 " in first["options"][2]["label"]
    assert ["Bar 5 " in second["options"][1]["label"], "Bar 7 " in second["options"][2]["label"]] \
        == [True, True]
    assert "since bar 5" in second["title"]
    assert first["decision"] is None


def test_an_end_repeat_with_its_start_is_not_asked_about(song):
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(starts=(3, 5)))
    assert _choices(song) == []


def test_a_one_bar_repeat_has_its_start_in_the_same_bar(song):
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(ends=(4,), starts=(4,)))
    assert _choices(song) == []


def test_a_song_with_no_marked_systems_is_not_asked(song):
    os.remove(os.path.join(song.dir, ".systems.json"))
    assert _choices(song) == []


def test_picking_a_system_puts_the_start_on_every_staff_and_survives_a_re_clean(song):
    [(_, first), _] = _choices(song)
    problems.record_repeat_choice(song, first["id"], "c")   # bar 3
    assert _starts(song) == [[3], [3]]
    [entry] = _fixes(song)
    assert (entry["kind"], entry["measure"], entry["source"]) == ("repeat", 3, "repeat-choice")
    # Answered: the question about bar 4 is gone, the one about bar 8 is still there.
    assert [measure for measure, _ in _choices(song)] == [8]

    # A re-clean writes the score again without it; the recorded answer puts it back.
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score())
    pipeline.apply_recorded_fixes(song.cleaned_path(), song.dir)
    assert _starts(song) == [[3], [3]]


def test_a_leaves_the_score_alone_and_is_asked_once(song):
    [(_, first), _] = _choices(song)
    before = open(song.cleaned_path(), "rb").read()
    problems.record_repeat_choice(song, first["id"], "a")
    assert open(song.cleaned_path(), "rb").read() == before
    assert _fixes(song) == []
    # Still listed, as decided -- and still decided after a re-clean.
    decided = {measure: choice["decision"] for measure, choice in _choices(song)}
    assert decided == {4: {"picked": "a"}, 8: None}
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score())
    assert state.load(song.slug).data["repeats"] == {"kept": [4]}
    assert {m: c["decision"] for m, c in _choices(song)}[4] == {"picked": "a"}


def test_a_wrong_answer_is_refused_and_writes_nothing(song):
    [(_, first), _] = _choices(song)
    with pytest.raises(FixError):
        problems.record_repeat_choice(song, first["id"], "z")
    with pytest.raises(FixError):
        problems.record_repeat_choice(song, "repeat-6", "b")
    assert _fixes(song) == [] and _starts(song) == [[], []]
    problems.record_repeat_choice(song, first["id"], "a")
    with pytest.raises(FixError):
        problems.record_repeat_choice(song, first["id"], "b")


# ------------------------------------------------------------ the fix kind


def test_a_repeat_fix_refuses_a_bar_that_already_opens_one():
    root = etree.fromstring(_score(starts=(3,)))
    with pytest.raises(FixError, match="m3"):
        score_fixes.apply_fixes(root, [{"kind": "repeat", "measure": 3, "why": "x"}])
    with pytest.raises(FixError, match="no measure 9"):
        score_fixes.apply_fixes(root, [{"kind": "repeat", "measure": 9, "why": "x"}])


def test_a_repeat_fix_goes_ahead_of_the_bars_music():
    root = etree.fromstring(_score())
    [line] = score_fixes.apply_fixes(root, [{"kind": "repeat", "measure": 5, "why": "page"}])
    assert line.startswith("m5: repeat starts here on all 2 staves")
    for staff in root.findall(".//Score/Staff"):
        assert [c.tag for c in staff.findall("Measure")[4]] == ["startRepeat", "voice"]
