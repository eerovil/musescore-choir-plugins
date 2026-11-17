"""What the app says when MuseScore 4 refuses a score, and which MuseScore checked (#481).

MuseScore 4 exits without a word when it refuses a file; the reason is only in its
own log file. These pin reading that reason, the error built from it, the staff list
answering 422 instead of 500, and the check using the Python rule for MuseScore 4.
"""

import os
import time

import pytest

from src import musescore_cli
from src.song_app import pipeline

LOG = """2026-11-18T10:00:00 | INFO | main_thread | something else
2026-11-18T10:00:01 | ERROR | main_thread | NotationProject::load | failed load notation, err: [2009] <b>Voice too long</b>: Full score, measure 2, staff 1, voice 2. Found: 8/2. Expected: 6/2.
Voice too long: Full score, measure 5, staff 3, voice 2. Found: 5/4. Expected: 4/4., path: /tmp/in.musicxml
2026-11-18T10:00:02 | INFO | main_thread | shutting down
"""


def _log(folder, name, text, age=0.0):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_the_reason_is_read_off_the_log_written_during_the_run(tmp_path):
    since = time.time() - 5
    _log(str(tmp_path / "mac"), "MuseScore_261118_100000.log", LOG)
    reason = musescore_cli.failed_load_reason(since, [str(tmp_path / "linux"), str(tmp_path / "mac")])
    assert reason == ("Voice too long: Full score, measure 2, staff 1, voice 2. Found: 8/2. Expected: 6/2.\n"
                      "Voice too long: Full score, measure 5, staff 3, voice 2. Found: 5/4. Expected: 4/4.")


def test_a_log_older_than_the_run_is_not_this_runs_reason(tmp_path):
    _log(str(tmp_path), "MuseScore_261117_090000.log", LOG, age=3600)
    assert musescore_cli.failed_load_reason(time.time() - 5, [str(tmp_path)]) is None


def test_a_log_with_no_refusal_says_nothing(tmp_path):
    _log(str(tmp_path), "MuseScore_261118_100000.log", "INFO | all fine\n")
    assert musescore_cli.failed_load_reason(time.time() - 5, [str(tmp_path)]) is None


def _outcome(stderr="", started=0.0):
    return musescore_cli.Outcome(40, "", stderr, "/tmp/out.mscx", None, started)


def test_the_error_gives_musescores_reason_when_its_log_has_one(monkeypatch):
    monkeypatch.setattr(musescore_cli, "failed_load_reason", lambda since, *a: "Voice too long: measure 2")
    said = musescore_cli.explain(_outcome(started=time.time()), "conversion failed")
    assert said == ("MuseScore conversion failed: MuseScore refused to open the score.\n"
                    "Voice too long: measure 2")
    assert "MUSESCORE_CLI_PATH" not in said


def test_the_error_gives_what_musescore_printed(monkeypatch):
    monkeypatch.setattr(musescore_cli, "failed_load_reason", lambda since, *a: None)
    said = musescore_cli.explain(_outcome("cannot read file", time.time()), "render failed")
    assert said == "MuseScore render failed.\ncannot read file"


def test_only_a_silent_run_with_no_log_points_at_the_path(monkeypatch):
    monkeypatch.setattr(musescore_cli, "failed_load_reason", lambda since, *a: None)
    said = musescore_cli.explain(_outcome(started=time.time()), "conversion failed")
    assert said == "MuseScore conversion failed and said nothing. Check MUSESCORE_CLI_PATH."


SCORE = """<museScore><Score><Part><trackName>T1</trackName><Staff id="1"/></Part>
<Staff id="1"><Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>
<Chord><durationType>whole</durationType><Note><pitch>60</pitch></Note></Chord>
<Chord><durationType>half</durationType><Note><pitch>62</pitch></Note></Chord>
</voice></Measure></Staff></Score></museScore>"""


def test_with_musescore_4_the_check_is_the_python_rule_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(musescore_cli, "major", lambda cli=None: 4)
    score = tmp_path / "song_cleaned.mscx"
    score.write_text(SCORE)
    found = pipeline.musescore_check(str(score))
    assert [(f["measure"], f["staff"], f["voice"]) for f in found] == [(1, 1, 1)]
    result = pipeline.check_opens_in_musescore(str(score), str(tmp_path))
    assert result["status"] == "repaired" and result["by"] == "MuseScore 4"
    fixes = (tmp_path / "fixes.json").read_text()
    assert "Bar 1, T1: MuseScore 4 called this bar" in fixes
    assert "MuseScore 4 refuses this bar" in score.read_text()


def test_the_staff_list_answers_422_with_musescores_reason(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from src.song_app import server, state

    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    song = state.create("Talviuni", per_system=True)
    (tmp_path / "songs" / song.slug / "in.musicxml").write_text("<score-partwise/>")
    song.data.setdefault("sources", {})["xml"] = "in.musicxml"
    song.save()

    def refuse(*_a, **_k):
        raise RuntimeError("MuseScore conversion failed: MuseScore refused to open the score.\n"
                           "Voice too long: Full score, measure 33, staff 5, voice 2.")

    monkeypatch.setattr(pipeline, "convert_to_mscx", refuse)
    reply = TestClient(server.app).get(f"/api/songs/{song.slug}/systems")
    assert reply.status_code == 422
    assert "measure 33, staff 5" in reply.json()["detail"]
