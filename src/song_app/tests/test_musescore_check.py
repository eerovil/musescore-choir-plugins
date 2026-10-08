"""MuseScore 3's own corruption check, run on every clean (#235).

MuseScore checks a score as it opens it and calls one that fails "corrupted" (on
Sangerhilsen the app crashed instead). Only the app runs that check, so nothing here
had ever met it. The clean now asks MuseScore itself (`-o x.mlog`), resets each bar it
rejects to a rest, writes the notes it took out into `fixes.json`, and checks again.
"""
import json
import os
import shutil

import pytest
from lxml import etree

from src.clean_score.utils.rejected_bars import clear_bar
from src.song_app import pipeline, verification

TEST_FILES = os.path.join(os.path.dirname(__file__), "..", "..", "clean_score", "tests", "test_files")


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


needs_musescore = pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")

# Three bars on one staff: a half tied over the barline into bar 2, bar 2 the bar to
# reset (a slur starts in it and ends in bar 3), and a second staff left alone.
TIE_OUT = ('<Spanner type="Tie"><Tie/><next><location><measures>1</measures>'
           '<fractions>-1/2</fractions></location></next></Spanner>')
TIE_IN = ('<Spanner type="Tie"><prev><location><measures>-1</measures>'
          '<fractions>1/2</fractions></location></prev></Spanner>')
TIE_INSIDE = ('<Spanner type="Tie"><Tie/><next><location><fractions>1/2</fractions>'
              '</location></next></Spanner>')
# Ends at the start of bar 3, begun on beat 2 of bar 2.
SLUR_IN = ('<Spanner type="Slur"><prev><location><measures>-1</measures>'
           '<fractions>1/4</fractions></location></prev></Spanner>')


def _chord(kind, pitch, extra=""):
    return f"<Chord><durationType>{kind}</durationType><Note>{extra}<pitch>{pitch}</pitch></Note></Chord>"


def _score():
    staff1 = (
        '<Staff id="1">'
        f'<Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>'
        f'{_chord("half", 60, TIE_INSIDE)}{_chord("half", 60, TIE_OUT)}</voice></Measure>'
        f'<Measure><voice>{_chord("half", 60, TIE_IN)}<Tuplet><normalNotes>2</normalNotes>'
        f'<actualNotes>3</actualNotes></Tuplet>{_chord("eighth", 62)}{_chord("eighth", 64)}'
        f'<location><fractions>-1/12</fractions></location>{_chord("eighth", 65)}<endTuplet/>'
        f'</voice><voice>{_chord("whole", 55)}</voice></Measure>'
        f'<Measure><voice>{SLUR_IN}{_chord("whole", 67)}</voice></Measure>'
        '</Staff>')
    staff2 = ('<Staff id="2">' + "".join(
        f'<Measure><voice>{_chord("whole", 48)}</voice></Measure>' for _ in range(3)) + '</Staff>')
    return etree.fromstring(
        ('<museScore><Score><Part><trackName>T1</trackName><Staff id="1"/></Part>'
         '<Part><trackName>B1</trackName><Staff id="2"/></Part>'
         f'{staff1}{staff2}</Score></museScore>').encode())


def test_a_reset_bar_is_one_whole_bar_rest_and_says_what_it_held():
    root = _score()
    removed = clear_bar(root, 1, 2)
    assert removed == ["C4", "D4", "E4", "F4", "G3"]
    bar = root.findall(".//Score/Staff")[0].findall("Measure")[1]
    voices = bar.findall("voice")
    assert len(voices) == 1
    assert [(el.tag, el.findtext("durationType"), el.findtext("duration")) for el in voices[0]] == [
        ("Rest", "measure", "1/1")]


def test_only_the_named_staff_and_bar_change():
    root = _score()
    other = etree.tostring(root.findall(".//Score/Staff")[1])
    clear_bar(root, 1, 2)
    assert etree.tostring(root.findall(".//Score/Staff")[1]) == other
    assert clear_bar(root, 3, 1) is None
    assert clear_bar(root, 1, 9) is None


def test_ties_and_slurs_reaching_into_the_reset_bar_are_cut_and_others_kept():
    root = _score()
    clear_bar(root, 1, 2)
    first, _, third = root.findall(".//Score/Staff")[0].findall("Measure")
    ties = first.findall(".//Note/Spanner")
    # The tie inside bar 1 stays; the one that crossed into bar 2 has nowhere to land.
    assert len(ties) == 1 and ties[0].find("next/location/measures") is None
    assert third.find(".//Spanner") is None


def _spanning_score():
    """Four bars on one staff. A slur and a tie run from bar 1 over bar 2 to bar 3,
    and a slur runs from bar 2 to bar 4 -- MuseScore writes each end as a bar offset
    plus a position offset from where that end stands."""
    over_out = ('<Spanner type="Slur"><Slur/><next><location><measures>2</measures>'
                '</location></next></Spanner>')
    over_in = ('<Spanner type="Slur"><prev><location><measures>-2</measures>'
               '</location></prev></Spanner>')
    tie_over_out = ('<Spanner type="Tie"><Tie/><next><location><measures>2</measures>'
                    '<fractions>-1/2</fractions></location></next></Spanner>')
    tie_over_in = ('<Spanner type="Tie"><prev><location><measures>-2</measures>'
                   '<fractions>1/2</fractions></location></prev></Spanner>')
    from_out = ('<Spanner type="Slur"><Slur/><next><location><measures>2</measures>'
                '</location></next></Spanner>')
    from_in = ('<Spanner type="Slur"><prev><location><measures>-2</measures>'
               '</location></prev></Spanner>')
    bars = [
        f'{over_out}{_chord("half", 60)}{_chord("half", 60, tie_over_out)}',
        f'{from_out}{_chord("whole", 62)}',
        f'{over_in}{_chord("half", 64, tie_over_in)}{_chord("half", 64)}',
        f'{from_in}{_chord("whole", 65)}',
    ]
    staff = '<Staff id="1">' + "".join(
        f"<Measure><voice>{body}</voice></Measure>" for body in bars) + "</Staff>"
    return etree.fromstring(('<museScore><Score><Part><trackName>T1</trackName>'
                             f'<Staff id="1"/></Part>{staff}</Score></museScore>').encode())


def test_a_tie_or_slur_passing_over_the_reset_bar_keeps_both_ends():
    root = _spanning_score()
    clear_bar(root, 1, 2)
    first, _, third, fourth = root.findall(".//Score/Staff")[0].findall("Measure")
    # Bar 1 to bar 3 never touched bar 2: both ends of the slur and the tie stay.
    assert [s.get("type") for s in first.iter("Spanner")] == ["Slur", "Tie"]
    assert [s.get("type") for s in third.iter("Spanner")] == ["Slur", "Tie"]
    # The slur that began in bar 2 has lost its start, so its end in bar 4 goes too.
    assert fourth.find(".//Spanner") is None


def test_the_rest_takes_the_bars_own_length():
    root = _score()
    root.findall(".//Score/Staff")[0].findall("Measure")[1].set("len", "9/8")
    clear_bar(root, 1, 2)
    rest = root.findall(".//Score/Staff")[0].findall("Measure")[1].find("voice/Rest")
    assert rest.findtext("duration") == "9/8"


def test_no_musescore_is_not_checked_rather_than_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    score = tmp_path / "s.mscx"
    shutil.copyfile(os.path.join(TEST_FILES, "simple_1_output.mscx"), score)
    assert pipeline.musescore_check(str(score)) is None
    assert pipeline.check_opens_in_musescore(str(score), str(tmp_path))["status"] == "not_checked"


@needs_musescore
def test_musescore_names_the_bar_it_refuses_and_accepts_it_once_reset(tmp_path):
    score = tmp_path / "s.mscx.building"   # the name a clean checks it under
    tree = etree.parse(os.path.join(TEST_FILES, "simple_1_output.mscx"))
    voice = tree.getroot().findall(".//Score/Staff")[0].findall("Measure")[1].find("voice")
    voice.append(etree.fromstring(_chord("quarter", 72)))
    tree.write(str(score), encoding="UTF-8", xml_declaration=True)

    found = pipeline.musescore_check(str(score))
    assert [(f["measure"], f["staff"]) for f in found] == [(2, 1)]
    assert "incomplete" in found[0]["message"]

    outcome = pipeline.check_opens_in_musescore(str(score), str(tmp_path))
    assert outcome["status"] == "repaired"
    assert [(r["measure"], r["staff"]) for r in outcome["reset"]] == [(2, 1)]
    assert pipeline.musescore_check(str(score)) == []


@needs_musescore
def test_a_sound_score_passes():
    assert pipeline.musescore_check(os.path.join(TEST_FILES, "simple_1_output.mscx")) == []


REJECTED = {"measure": 2, "staff": 1, "voice": 1,
            "message": "Measure 2, staff 1 incomplete. Expected: 1; Found: 5/4"}


def _stub(monkeypatch, *answers):
    queue = list(answers)
    monkeypatch.setattr(pipeline, "musescore_check", lambda _path: queue.pop(0))


def _write_score(tmp_path):
    path = tmp_path / "song_cleaned.mscx"
    etree.ElementTree(_score()).write(str(path), encoding="UTF-8", xml_declaration=True)
    return str(path)


def _fixes(tmp_path):
    path = tmp_path / "fixes.json"
    return json.loads(path.read_text()) if path.exists() else []


def test_a_rejected_bar_is_reset_and_written_down_once(tmp_path, monkeypatch):
    score = _write_score(tmp_path)
    (tmp_path / "fixes.json").write_text(json.dumps([{"kind": "text", "what": "typed by a person"}]))

    _stub(monkeypatch, [REJECTED], [])
    assert pipeline.check_opens_in_musescore(score, str(tmp_path))["status"] == "repaired"
    score = _write_score(tmp_path)   # a re-clean rebuilds the score from the source
    _stub(monkeypatch, [REJECTED], [])
    pipeline.check_opens_in_musescore(score, str(tmp_path))

    written = [f for f in _fixes(tmp_path) if f.get("source") == pipeline.MUSESCORE_CHECK_SOURCE]
    assert len(written) == 1
    assert written[0]["what"].startswith("Bar 2, T1: MuseScore 3 called this bar corrupted")
    assert "Taken out: C4 D4 E4 F4 G3" in written[0]["what"]
    assert any(f["what"] == "typed by a person" for f in _fixes(tmp_path))
    # Free text, so the Fix panel lists it as outstanding.
    assert written[0]["what"] in pipeline.free_text_fixes(str(tmp_path))


def test_a_clean_that_passes_takes_the_old_sentences_away(tmp_path, monkeypatch):
    score = _write_score(tmp_path)
    _stub(monkeypatch, [REJECTED], [])
    pipeline.check_opens_in_musescore(score, str(tmp_path))
    _stub(monkeypatch, [])
    assert pipeline.check_opens_in_musescore(score, str(tmp_path))["status"] == "passed"
    assert _fixes(tmp_path) == []


def test_what_is_still_refused_becomes_a_health_finding(tmp_path, monkeypatch):
    score = _write_score(tmp_path)
    _stub(monkeypatch, [REJECTED], [REJECTED])
    outcome = pipeline.check_opens_in_musescore(score, str(tmp_path))
    assert outcome["status"] == "rejected"
    rows = pipeline.musescore_findings(score, outcome["rejected"])
    assert rows == [{
        "id": "musescore-corrupt-m2-s1-v1", "kind": "musescore-corrupt", "measure": 2,
        "staff": "T1", "detail": "MuseScore 3 calls this corrupted: " + REJECTED["message"],
    }]


@pytest.mark.parametrize("stored,status,says", [
    (None, "not_checked", "Not checked yet"),
    ({"status": "passed", "checked_against": "old"}, "stale", "older cleaned score"),
    ({"status": "passed", "checked_against": "now"}, "passed", "without calling it corrupted"),
    ({"status": "repaired", "reset": [{}, {}], "checked_against": "now"}, "warning", "2 staff-bar(s)"),
    ({"status": "rejected", "rejected": [{}], "checked_against": "now"}, "warning", "still calls"),
    ({"status": "not_checked", "checked_against": "now"}, "not_checked", "No MuseScore 3"),
])
def test_the_review_row_says_what_musescore_said(stored, status, says):
    result = verification._musescore_result(stored, "now")
    assert result["status"] == status and says in result["detail"]


def test_a_reset_bar_keeps_what_a_fix_for_it_meets(tmp_path, monkeypatch):
    """`/bar` shows the bar as scanned beside the reset one (#357).

    Fixes replay before the check, so on the next clean a new entry meets the bar as it
    read before the reset. On meeste-laul that only showed up in a failed clean's error.
    """
    from src.clean_score.utils.score_fixes import FixError, apply_fixes, bar_tokens
    before = bar_tokens(_score(), 1, 2)
    score = _write_score(tmp_path)
    _stub(monkeypatch, [REJECTED], [])
    pipeline.check_opens_in_musescore(score, str(tmp_path))

    entry = next(f for f in _fixes(tmp_path) if f.get("source") == pipeline.MUSESCORE_CHECK_SOURCE)
    assert entry["scanned_from"] == before
    assert entry["staff_id"] == "1"

    bar = pipeline.bar_for_fix(score, 1, 2)
    assert bar["from"] == ["measure:R"]
    assert bar["reset"]["from"] == before
    assert [n["token"] for n in bar["reset"]["notes"]] == [t for t in before
                                                           if t[0] != "[" and t[-1] != "]"]
    assert "reset" not in pipeline.bar_for_fix(score, 1, 1)
    assert "reset" not in pipeline.bar_for_fix(score, 2, 2)

    # The rebuilt score reads as `reset.from` when the fixes run, not as the rest.
    fix = {"kind": "append", "staff": 1, "measure": 2, "add": [], "why": "check"}
    apply_fixes(_score(), [{**fix, "from": bar["reset"]["from"]}])
    with pytest.raises(FixError):
        apply_fixes(_score(), [{**fix, "from": bar["from"]}])
