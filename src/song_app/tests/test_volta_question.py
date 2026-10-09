"""Does a repeat have 1. and 2. brackets? (#319)

homr does not read volta brackets, and a repeat without them looks the same in the
score, so the Fix stage asks about every end repeat with no bracket over it. **a**
leaves the score as it is; the others put "1." over the last 1-4 bars and "2." over
the bar after. This pins the question, both kinds of answer, the `volta` fix kind
they write, its replay on a re-clean, and that MuseScore reads the brackets back.
"""
import json
import os
import subprocess

import pytest
from lxml import etree

from src.clean_score.utils import score_fixes
from src.clean_score.utils.score_fixes import FixError
from src.song_app import pipeline, problems, state

TEST_FILES = os.path.join(os.path.dirname(__file__), "..", "..", "clean_score", "tests",
                          "test_files")


def _score(bars=10, ends=(4, 8), starts=(), staves=2):
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
    made = state.create("Voltas", per_system=False)
    with open(made.path("song_cleaned.mscx"), "w") as fh:
        fh.write(_score())
    made.data["cleaned"] = "song_cleaned.mscx"
    made.save()
    return made


def _spans(song):
    root = etree.parse(song.cleaned_path()).getroot()
    return [score_fixes.volta_spans(staff) for staff in root.findall(".//Score/Staff")]


def _fixes(song):
    path = song.path("fixes.json")
    return json.load(open(path)) if os.path.exists(path) else []


def _choices(song):
    return [(row["measure"], choice) for row in problems.problems(song)
            for choice in row["choices"] if choice["kind"] == "volta"]


def test_each_end_repeat_with_no_brackets_is_asked_about(song):
    asked = _choices(song)
    assert [measure for measure, _ in asked] == [4, 8]
    first, second = (choice for _, choice in asked)
    # a leaves it alone; then 1. over the last 1, 2, 3 bars -- never the repeat's
    # own first bar, or the second pass would have nothing to play.
    assert [(o["letter"], o["current"]) for o in first["options"]] == [
        ("a", True), ("b", False), ("c", False), ("d", False)]
    assert first["options"][1]["label"] == "1. over bar 4, 2. over bar 5"
    assert first["options"][3]["label"] == "1. over bars 2–4, 2. over bar 5"
    # The second repeat runs from bar 5, after the first one's end.
    assert second["options"][-1]["label"] == "1. over bars 6–8, 2. over bar 9"
    assert first["decision"] is None
    # No marked systems needed: the question is about bars, not the page's layout.
    assert not os.path.exists(os.path.join(song.dir, ".systems.json"))


def test_the_bracket_is_offered_back_to_the_start_sign_and_no_further(song):
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(bars=12, ends=(10,), starts=(9,)))
    root = etree.parse(song.cleaned_path()).getroot()
    [only] = problems.volta_questions(root)
    assert [o["bars"] for o in only["options"]] == [None, 1]


def test_at_most_four_bars_are_offered(song):
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(bars=12, ends=(9,)))
    root = etree.parse(song.cleaned_path()).getroot()
    [only] = problems.volta_questions(root)
    assert [o["bars"] for o in only["options"]] == [None, 1, 2, 3, 4]


def test_a_repeat_with_no_bar_after_it_for_the_2_bracket_is_not_asked(song):
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score(bars=10, ends=(9, 10)))
    assert _choices(song) == []


def test_a_repeat_that_already_has_brackets_is_not_asked(song):
    root = etree.parse(song.cleaned_path()).getroot()
    score_fixes.apply_fixes(root, [{"kind": "volta", "measure": 4, "bars": 1, "why": "x"}])
    etree.ElementTree(root).write(song.cleaned_path())
    assert [measure for measure, _ in _choices(song)] == [8]


def test_picking_a_length_writes_both_brackets_and_survives_a_re_clean(song):
    [(_, first), _] = _choices(song)
    problems.record_volta_choice(song, first["id"], "c")   # 1. over bars 3-4
    assert _spans(song) == [[(3, 4), (5, 5)], []]
    [entry] = _fixes(song)
    assert (entry["kind"], entry["measure"], entry["bars"], entry["source"]) == (
        "volta", 4, 2, "volta-choice")
    assert [measure for measure, _ in _choices(song)] == [8]

    # A re-clean writes the score again without them; the recorded answer puts them back.
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score())
    pipeline.apply_recorded_fixes(song.cleaned_path(), song.dir)
    assert _spans(song) == [[(3, 4), (5, 5)], []]


def test_a_leaves_the_score_alone_and_is_asked_once(song):
    [(_, first), _] = _choices(song)
    before = open(song.cleaned_path(), "rb").read()
    problems.record_volta_choice(song, first["id"], "a")
    assert open(song.cleaned_path(), "rb").read() == before
    assert _fixes(song) == []
    assert {m: c["decision"] for m, c in _choices(song)} == {4: {"picked": "a"}, 8: None}
    with open(song.cleaned_path(), "w") as fh:
        fh.write(_score())
    assert state.load(song.slug).data["voltas"] == {"kept": [4]}
    assert {m: c["decision"] for m, c in _choices(song)}[4] == {"picked": "a"}


def test_a_wrong_answer_is_refused_and_writes_nothing(song):
    [(_, first), _] = _choices(song)
    with pytest.raises(FixError):
        problems.record_volta_choice(song, first["id"], "z")
    with pytest.raises(FixError):
        problems.record_volta_choice(song, "volta-6", "b")
    assert _fixes(song) == [] and _spans(song) == [[], []]
    problems.record_volta_choice(song, first["id"], "a")
    with pytest.raises(FixError):
        problems.record_volta_choice(song, first["id"], "b")


def test_the_volta_and_the_repeat_question_sit_on_the_same_row(song):
    rows = [row for row in problems.problems(song) if row["measure"] == 4]
    assert len(rows) == 1
    # No systems are marked, so the repeat question cannot be asked; the volta one is.
    assert [c["kind"] for c in rows[0]["choices"]] == ["volta"]


# ------------------------------------------------------------ the fix kind


def test_the_brackets_are_written_the_way_musescore_writes_them():
    """The same elements MuseScore wrote for Shakkitarina's hand-made brackets."""
    root = etree.fromstring(_score(bars=6, ends=(3,)))
    score_fixes.apply_fixes(root, [{"kind": "volta", "measure": 3, "bars": 2, "why": "p"}])
    bars = root.find(".//Score/Staff").findall("Measure")

    def spanners(bar):
        return [etree.tostring(s).decode() for s in bar.find("voice")
                if s.tag == "Spanner"]
    assert spanners(bars[1]) == [
        '<Spanner type="Volta"><Volta><endHookType>1</endHookType><beginText>1.</beginText>'
        '<endings>1</endings></Volta><next><location><measures>2</measures></location>'
        '</next></Spanner>']
    # The end of 1. comes before the start of 2. in the bar they share.
    assert spanners(bars[3]) == [
        '<Spanner type="Volta"><prev><location><measures>-2</measures></location></prev>'
        '</Spanner>',
        '<Spanner type="Volta"><Volta><beginText>2.</beginText><endings>2</endings>'
        '</Volta><next><location><measures>1</measures></location></next></Spanner>']
    assert spanners(bars[4]) == [
        '<Spanner type="Volta"><prev><location><measures>-1</measures></location></prev>'
        '</Spanner>']
    # Only the top staff carries them.
    assert score_fixes.volta_spans(root.findall(".//Score/Staff")[1]) == []


@pytest.mark.parametrize("fix, refusal", [
    ({"measure": 5, "bars": 1}, "no repeat ends here"),
    ({"measure": 4, "bars": 5}, "cannot end at bar 4"),
    ({"measure": 4, "bars": 0}, "cannot end at bar 4"),
    # A "2." bracket may close the score (#378), but not reach past it.
    ({"measure": 9, "bars": 1, "second": 2}, "no bar 11 for a 2. bracket of 2 bars"),
    ({"measure": 10, "bars": 1}, "a bar after it"),
])
def test_a_volta_fix_refuses_what_it_cannot_draw(fix, refusal):
    root = etree.fromstring(_score(ends=(4, 9)))
    with pytest.raises(FixError, match=refusal):
        score_fixes.apply_fixes(root, [{"kind": "volta", "why": "x", **fix}])


def test_a_volta_fix_refuses_bars_already_under_a_bracket():
    root = etree.fromstring(_score())
    score_fixes.apply_fixes(root, [{"kind": "volta", "measure": 4, "bars": 1, "why": "x"}])
    with pytest.raises(FixError, match="already have a bracket"):
        score_fixes.apply_fixes(root, [{"kind": "volta", "measure": 4, "bars": 2, "why": "x"}])


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_reads_the_brackets_back(tmp_path):
    """The written brackets are ones MuseScore opens and exports as real endings."""
    root = etree.parse(os.path.join(TEST_FILES, "medium_1_output.mscx")).getroot()
    staff = root.find(".//Score/Staff")
    bars = staff.findall("Measure")
    assert len(bars) >= 4
    end = etree.Element("endRepeat")
    end.text = "2"
    bars[1].insert(0, end)
    score_fixes.apply_fixes(root, [{"kind": "volta", "measure": 2, "bars": 1, "why": "x"}])
    src = tmp_path / "volta.mscx"
    etree.ElementTree(root).write(str(src), encoding="UTF-8", xml_declaration=True)
    out = tmp_path / "volta.musicxml"
    subprocess.run([os.environ["MUSESCORE_CLI_PATH"], "-o", str(out), str(src)],
                   check=True, capture_output=True, timeout=120)
    xml = etree.parse(str(out)).getroot()
    endings = [(m.get("number"), e.get("number"), e.get("type"))
               for m in xml.find("part").findall("measure") for e in m.iter("ending")]
    assert endings == [("2", "1", "start"), ("2", "1", "stop"),
                       ("3", "2", "start"), ("3", "2", "discontinue")]
