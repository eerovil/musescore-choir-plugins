"""What the app says when MuseScore 4 refuses a score, and which MuseScore checked (#481).

MuseScore 4 exits without a word when it refuses a file; the reason is only in its
own log file. These pin reading that reason, the error built from it, the staff list
answering 422 instead of 500, and the check using the Python rule for MuseScore 4.
"""

import datetime
import os
import time

import pytest

from src import musescore_cli
from src.song_app import pipeline


def _at(when):
    return datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _refusal(when, bar=2):
    """A MuseScore 4 log with one refusal written at ``when``, as 4.7.5 writes it."""
    return (f"{_at(when - 1)} | INFO  | main_thread | something else\n"
            f"{_at(when)} | ERROR | main_thread | ConverterController::convertFile | failed load "
            f"notation, err: [2009] <b>Voice too long</b>: Full score, measure {bar}, staff 1, "
            "voice 2. Found: 8/2. Expected: 6/2.\n"
            "Voice too long: Full score, measure 5, staff 3, voice 2. Found: 5/4. Expected: 4/4., "
            "path: /tmp/in.musicxml\n"
            f"{_at(when + 1)} | INFO  | main_thread | shutting down\n")


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
    _log(str(tmp_path / "mac"), "MuseScore_261118_100000.log", _refusal(time.time()))
    reason = musescore_cli.failed_load_reason(since, [str(tmp_path / "linux"), str(tmp_path / "mac")])
    assert reason == ("Voice too long: Full score, measure 2, staff 1, voice 2. Found: 8/2. Expected: 6/2.\n"
                      "Voice too long: Full score, measure 5, staff 3, voice 2. Found: 5/4. Expected: 4/4.")


def test_a_log_older_than_the_run_is_not_this_runs_reason(tmp_path):
    _log(str(tmp_path), "MuseScore_261117_090000.log", _refusal(time.time() - 3600), age=3600)
    assert musescore_cli.failed_load_reason(time.time() - 5, [str(tmp_path)]) is None


def test_an_earlier_refusal_in_a_log_written_again_is_not_this_runs_reason(tmp_path):
    # One log file holds an hour-old refusal, then this run's unrelated failure.
    now = time.time()
    text = _refusal(now - 3600) + f"{_at(now)} | ERROR | main_thread | something else broke\n"
    _log(str(tmp_path), "MuseScore_261118_090000.log", text)
    assert musescore_cli.failed_load_reason(now - 5, [str(tmp_path)]) is None


def test_the_newest_refusal_in_the_run_is_the_reason(tmp_path):
    now = time.time()
    _log(str(tmp_path), "MuseScore_261118_090000.log", _refusal(now - 3600, bar=33) + _refusal(now, bar=7))
    assert "measure 7," in musescore_cli.failed_load_reason(now - 5, [str(tmp_path)])


def test_a_refusal_with_no_path_stops_at_the_next_log_line(tmp_path):
    # Read with no file to tie it to (a run with no ``-o``): the refusal ends there.
    now = time.time()
    text = (f"{_at(now)} | ERROR | main | x | failed load notation, err: [2009] Voice too long: measure 2\n"
            f"{_at(now)} | INFO  | main | x | shutting down\n{_at(now)} | INFO  | main | x | bye\n")
    _log(str(tmp_path), "MuseScore_261118_090000.log", text)
    assert musescore_cli.failed_load_reason(now - 5, [str(tmp_path)]) == "Voice too long: measure 2"


def test_refusals_naming_no_file_are_no_runs_reason_when_runs_overlap(tmp_path):
    # Two conversions at once, each refused, neither line naming its file: the bar
    # could be either song's, so neither run is told it.
    now = time.time()
    text = "".join(f"{_at(now)} | ERROR | main | x | failed load notation, err: [2009] "
                   f"Voice too long: measure {bar}\n" for bar in (2, 33))
    _log(str(tmp_path), "MuseScore_261118_090000.log", text)
    for song in ("a", "b"):
        source = str(tmp_path / "songs" / song / "in.musicxml")
        assert musescore_cli.failed_load_reason(now - 5, [str(tmp_path)], source=source) is None


def test_a_run_whose_refusal_names_no_file_falls_back_to_what_it_printed(monkeypatch, tmp_path):
    now = time.time()
    _log(str(tmp_path), "MuseScore_261118_090000.log",
         f"{_at(now)} | ERROR | main | x | failed load notation, err: [2009] Voice too long: measure 9\n")
    real = musescore_cli.failed_load_reason
    monkeypatch.setattr(musescore_cli, "failed_load_reason",
                        lambda since, source=None: real(since, [str(tmp_path)], source=source))
    outcome = musescore_cli.Outcome(1, "", "", "/tmp/out.mscx", None, now - 5,
                                    str(tmp_path / "in.musicxml"))
    assert musescore_cli.explain(outcome, "conversion failed") == \
        "MuseScore conversion failed and said nothing. Check MUSESCORE_CLI_PATH."


def test_another_songs_refusal_at_the_same_time_is_not_this_runs_reason(tmp_path):
    # Two conversions at once write into one day's log; each gets its own bar.
    now = time.time()
    mine = tmp_path / "songs" / "a" / "in.musicxml"
    text = (f"{_at(now)} | ERROR | main | x | failed load notation, err: [2009] Voice too long: "
            f"measure 2, path: {mine}\n"
            f"{_at(now)} | ERROR | main | x | failed load notation, err: [2009] Voice too long: "
            f"measure 33, path: {tmp_path / 'songs' / 'b' / 'in.musicxml'}\n")
    _log(str(tmp_path), "MuseScore_261118_090000.log", text)
    assert musescore_cli.failed_load_reason(now - 5, [str(tmp_path)], source=str(mine)) == \
        "Voice too long: measure 2"
    other = str(tmp_path / "songs" / "c" / "in.musicxml")
    assert musescore_cli.failed_load_reason(now - 5, [str(tmp_path)], source=other) is None


def test_a_run_knows_the_file_it_was_asked_to_read(tmp_path):
    out = musescore_cli.run(["true", "-f", "/x/in.musicxml", "-o", str(tmp_path / "o.mscx")],
                            str(tmp_path / "o.mscx"))
    assert out.source == "/x/in.musicxml"


@pytest.mark.parametrize("said, major", [
    ("MuseScore4 4.7.5", 4),
    ("MuseScore3 3.6.2", 3),
    ("Qt 6.5.3\nMuseScore Studio 4.4.4", 4),
    ("/usr/lib64/libOpenGL.so.0\nMuseScore4 4.7.5", 4),
    ("nothing useful", None),
])
def test_the_version_is_read_off_the_line_naming_musescore(said, major):
    assert musescore_cli._version_major(said) == major


def test_a_failed_version_ask_is_asked_again(monkeypatch, tmp_path):
    import subprocess
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, 120)
        return subprocess.CompletedProcess(argv, 0, "MuseScore4 4.7.5\n", "")

    monkeypatch.setattr(musescore_cli.subprocess, "run", fake)
    cli = str(tmp_path / "mscore")
    assert musescore_cli._major(cli, 1.0) is None
    assert musescore_cli._major(cli, 1.0) == 4
    assert musescore_cli._major(cli, 1.0) == 4
    assert len(calls) == 2


def test_a_log_with_no_refusal_says_nothing(tmp_path):
    _log(str(tmp_path), "MuseScore_261118_100000.log", "INFO | all fine\n")
    assert musescore_cli.failed_load_reason(time.time() - 5, [str(tmp_path)]) is None


def _outcome(stderr="", started=0.0):
    return musescore_cli.Outcome(40, "", stderr, "/tmp/out.mscx", None, started)


def test_the_error_gives_musescores_reason_when_its_log_has_one(monkeypatch):
    monkeypatch.setattr(musescore_cli, "failed_load_reason", lambda since, *a, **k: "Voice too long: measure 2")
    said = musescore_cli.explain(_outcome(started=time.time()), "conversion failed")
    assert said == ("MuseScore conversion failed: MuseScore refused to open the score.\n"
                    "Voice too long: measure 2")
    assert "MUSESCORE_CLI_PATH" not in said


def test_the_error_gives_what_musescore_printed(monkeypatch):
    monkeypatch.setattr(musescore_cli, "failed_load_reason", lambda since, *a, **k: None)
    said = musescore_cli.explain(_outcome("cannot read file", time.time()), "render failed")
    assert said == "MuseScore render failed.\ncannot read file"


def test_only_a_silent_run_with_no_log_points_at_the_path(monkeypatch):
    monkeypatch.setattr(musescore_cli, "failed_load_reason", lambda since, *a, **k: None)
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
    pytest.importorskip("fastapi")
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


def _song_with_scan(tmp_path, monkeypatch):
    from src.song_app import state

    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    song = state.create("Talviuni", per_system=True)
    (songs / song.slug / "in.musicxml").write_text("<score-partwise/>")
    song.data.setdefault("sources", {})["xml"] = "in.musicxml"
    song.save()
    return song


def test_the_staff_list_names_the_long_bars_on_every_load(tmp_path, monkeypatch):
    # Read off the converted file, not said once to a log: the first load and a
    # reload that reuses the conversion carry the same bars (#481).
    pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from lxml import etree

    from src.clean_score.tests.test_overlong_voices import FULL, OVERLAPPING, _score
    from src.song_app import server

    song = _song_with_scan(tmp_path, monkeypatch)
    runs = []

    def fake_run(argv, target, timeout=None):
        # Stands in for MuseScore 4 with -f: an .mscx whose bar 2 voice 2 runs long.
        runs.append(argv)
        with open(target, "wb") as out:
            out.write(etree.tostring(_score([FULL, FULL], [FULL, OVERLAPPING])))
        return musescore_cli.Outcome(0, "", "", target, None, time.time())

    monkeypatch.setattr(musescore_cli, "run", fake_run)
    monkeypatch.setattr(pipeline, "system_grid", lambda mscx: [])
    client = TestClient(server.app)
    first = client.get(f"/api/songs/{song.slug}/systems")
    again = client.get(f"/api/songs/{song.slug}/systems")  # reuses the conversion
    assert first.status_code == again.status_code == 200
    assert len(runs) == 1 and "-f" in runs[0]
    for reply in (first, again):
        assert [(o["measure"], o["staff"], o["voice"]) for o in reply.json()["overlong"]] == [(2, 1, 2)]


def test_a_musescore_that_will_not_start_points_at_the_path(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from src.song_app import server

    song = _song_with_scan(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    reply = TestClient(server.app).get(f"/api/songs/{song.slug}/systems")
    assert reply.status_code == 422
    assert "MUSESCORE_CLI_PATH" in reply.json()["detail"]


MISSING = "no-such-musescore"


def test_every_run_says_the_path_when_musescore_will_not_start(tmp_path):
    with pytest.raises(musescore_cli.MuseScoreError, match="MUSESCORE_CLI_PATH"):
        musescore_cli.run([str(tmp_path / MISSING), "in.mscx", "-o", "out.pdf"], "out.pdf")
    with pytest.raises(musescore_cli.MuseScoreError, match="MUSESCORE_CLI_PATH"):
        musescore_cli.export(str(tmp_path / MISSING), "in.mscx", str(tmp_path / "out.mid"))


def test_a_timeout_is_still_a_timeout(tmp_path):
    import subprocess
    with pytest.raises(subprocess.TimeoutExpired):
        musescore_cli.run(["sleep", "5"], str(tmp_path / "o"), timeout=0.2)


def test_the_publish_export_says_the_path_when_musescore_will_not_start(tmp_path, monkeypatch):
    from src.score_export import musescore
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / MISSING))
    with pytest.raises(RuntimeError, match="MUSESCORE_CLI_PATH"):
        musescore.run_musescore(str(tmp_path / "in.mscx"), str(tmp_path / "score.mid"))


def test_the_scan_picture_and_the_page_preview_say_the_path(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from src.song_app import scan, server

    song = _song_with_scan(tmp_path, monkeypatch)
    fragment = tmp_path / "songs" / song.slug / "frag.musicxml"
    fragment.write_text("<score-partwise/>")
    monkeypatch.setattr(scan, "fragment_path", lambda _song, _i: str(fragment))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / MISSING))
    client = TestClient(server.app)
    for url in (f"/api/songs/{song.slug}/scan-system/0",
                f"/api/songs/{song.slug}/render?doc=original"):
        reply = client.get(url)
        assert reply.status_code >= 400, url
        assert "MUSESCORE_CLI_PATH" in reply.json()["detail"], url
