"""Checking the cleaned score again without cleaning, and undoing a clean (#391).

A clean compares the notes with the scan and asks MuseScore 3 whether it opens the
result, and both answers hold only for the file they were made on. A score fixed by
hand showed them as stale, and refreshing them meant a re-clean that threw the fixes
away. `POST /recheck` asks the same questions about the score that is there, and
changes nothing in it. And a clean pressed by mistake can now be undone: the score it
replaced comes back with the records made against it.
"""
import os

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import clean_backup, job_state, pipeline, server, state, verification

SCORE = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Staff id="1">
  <Measure><voice>
    <Chord><durationType>whole</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
</Staff>
</Score></museScore>"""

EDITED = SCORE.replace("<pitch>62</pitch>", "<pitch>64</pitch>")
CLEANED = "talviuni_cleaned.mscx"
REJECTED = {"measure": 1, "staff": 1, "voice": 1,
            "message": "Measure 1, staff 1 too long. Expected: 4/4; Found: 5/4"}


@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    song = state.create("Talviuni", per_system=False)
    for name, body in (("talviuni.mscx", SCORE), (CLEANED, EDITED)):
        with open(song.path(name), "w", encoding="utf-8") as fh:
            fh.write(body)
    song.data["sources"] = {"xml": "talviuni.mscx"}
    song.data["cleaned"] = CLEANED
    song.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    # What a clean recorded before the score was edited by hand: stale now.
    song.data["verification"] = {
        "notes": {"status": "passed", "detail": "old", "checked_against": "sha1:old"},
        "musescore": {"status": "passed", "reset": [], "rejected": [],
                      "checked_against": "sha1:old"},
    }
    song.set_stage("review")
    song.save()
    return song


def _summary(slug):
    fresh = state.load(slug)
    return verification.summary(fresh, 0)


def test_check_again_refreshes_both_checks_on_the_score_as_it_is(song, monkeypatch):
    monkeypatch.setattr(pipeline, "musescore_check", lambda _path: [])
    assert _summary(song.slug)["notes"]["status"] == "stale"
    before = open(song.cleaned_path(), encoding="utf-8").read()

    server._run_check(song.slug)

    summary = _summary(song.slug)
    # The hand edit moved a pitch, so the notes no longer match the scan -- said, not stale.
    assert summary["notes"]["status"] == "warning"
    assert summary["musescore"]["status"] == "passed"
    assert summary["health"]["status"] != "stale"
    assert open(song.cleaned_path(), encoding="utf-8").read() == before
    assert job_state.load(song.dir)["check"]["status"] == "succeeded"


def test_check_again_reports_a_refused_bar_and_resets_nothing(song, monkeypatch):
    monkeypatch.setattr(pipeline, "musescore_check", lambda _path: [REJECTED])
    before = open(song.cleaned_path(), encoding="utf-8").read()

    server._run_check(song.slug)

    fresh = state.load(song.slug)
    assert open(song.cleaned_path(), encoding="utf-8").read() == before
    assert fresh.data["verification"]["musescore"]["status"] == "rejected"
    assert _summary(song.slug)["musescore"]["detail"].startswith("MuseScore 3 calls it corrupted")
    kinds = [i["kind"] for i in fresh.data["health"]["issues"]]
    assert "musescore-corrupt" in kinds
    # Nothing written into fixes.json either: that is what a clean's reset does.
    assert not pipeline.free_text_fixes(song.dir)


def test_no_musescore_is_said_as_not_checked(song):
    server._run_check(song.slug)
    assert _summary(song.slug)["musescore"]["status"] == "not_checked"


def test_the_route_starts_a_check_and_refuses_a_second(song, monkeypatch):
    started = []
    monkeypatch.setattr(server, "_run_check", lambda slug: started.append(slug))
    client = TestClient(server.app)
    assert client.post(f"/api/songs/{song.slug}/recheck").json() == {"started": True}
    # The stub never finishes the job, so it is still running.
    assert client.post(f"/api/songs/{song.slug}/recheck").status_code == 409
    assert client.post(f"/api/songs/{song.slug}/clean").status_code == 409


def test_a_clean_keeps_the_score_it_replaced_and_undo_puts_it_back(song, monkeypatch):
    song.data["lyrics"] = {"json": "lyrics.json", "imported_against": song.data["cleaned_fingerprint"],
                           "warnings": []}
    song.save()
    edited_records = state.load(song.slug).data

    def fake_clean(xml, out_dir, **kwargs):
        target = os.path.join(out_dir, CLEANED)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(SCORE)
        kwargs["check"].update(status="passed", reset=[], rejected=[])
        return target, xml

    monkeypatch.setattr(pipeline, "run_clean", fake_clean)
    monkeypatch.setattr(server, "_label_bounds", lambda *a: None)
    server._run_clean(song.slug)
    assert open(song.cleaned_path(), encoding="utf-8").read() == SCORE
    kept = clean_backup.latest(state.load(song.slug))
    assert kept and kept["file"] == CLEANED

    client = TestClient(server.app)
    reply = client.post(f"/api/songs/{song.slug}/undo-clean")
    assert reply.status_code == 200
    assert open(song.cleaned_path(), encoding="utf-8").read() == EDITED
    fresh = state.load(song.slug).data
    for key in ("lyrics", "verification", "stage", "cleaned_fingerprint"):
        assert fresh[key] == edited_records[key]
    assert reply.json()["clean_backup"] is None
    assert client.post(f"/api/songs/{song.slug}/undo-clean").status_code == 400


def test_a_failed_clean_keeps_no_backup(song, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("a fix no longer matches")

    monkeypatch.setattr(pipeline, "run_clean", broken)
    server._run_clean(song.slug)
    assert clean_backup.latest(state.load(song.slug)) is None
    assert open(song.cleaned_path(), encoding="utf-8").read() == EDITED


def test_only_the_newest_backups_are_kept(song, monkeypatch):
    monkeypatch.setattr(clean_backup, "KEEP", 3)
    for _ in range(5):
        clean_backup.stash(song)
    assert len(os.listdir(song.path(clean_backup.BACKUP_DIR))) == 3


def test_undo_takes_away_records_the_old_score_never_had(song, monkeypatch):
    # An old score with no health, checks or lyrics: the clean adds them, and they
    # describe the clean's score, so Undo must not leave them on the restored one.
    for key in ("verification", "health", "lyrics"):
        song.data.pop(key, None)
    song.save()

    def fake_clean(xml, out_dir, **kwargs):
        target = os.path.join(out_dir, CLEANED)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(SCORE)
        kwargs["check"].update(status="passed", reset=[], rejected=[])
        return target, xml

    monkeypatch.setattr(pipeline, "run_clean", fake_clean)
    monkeypatch.setattr(server, "_label_bounds", lambda *a: None)
    server._run_clean(song.slug)
    assert "health" in state.load(song.slug).data

    assert TestClient(server.app).post(f"/api/songs/{song.slug}/undo-clean").status_code == 200
    fresh = state.load(song.slug).data
    assert "health" not in fresh and "verification" not in fresh and "lyrics" not in fresh


def test_a_check_and_a_publish_never_overlap(song, monkeypatch):
    monkeypatch.setattr(server, "_run_check", lambda slug: None)
    for key, value in (("CLOUDFLARE_ACCOUNT_ID", "acct"), ("CLOUDFLARE_API_TOKEN", "tok"),
                       ("STEMMANAUHAT_D1_DATABASE_ID", "db1")):
        monkeypatch.setenv(key, value)
    song.data["review"] = {"approved_against": song.data["cleaned_fingerprint"]}
    song.save()
    client = TestClient(server.app)
    job_state.start(song.dir, "publish")
    assert client.post(f"/api/songs/{song.slug}/recheck").status_code == 409
    job_state.finish(song.dir, "publish")

    assert client.post(f"/api/songs/{song.slug}/recheck").status_code == 200
    # The stub leaves the check running: now the publish must wait.
    reply = client.post(f"/api/songs/{song.slug}/publish", json={"choir": "jm"})
    assert reply.status_code == 409


def test_a_score_upload_waits_for_a_running_check(song):
    job_state.start(song.dir, "check")
    reply = TestClient(server.app).post(
        f"/api/songs/{song.slug}/score-file",
        files={"file": (CLEANED, SCORE.encode(), "application/octet-stream")})
    assert reply.status_code == 409
    assert open(song.cleaned_path(), encoding="utf-8").read() == EDITED


def _clean_with(monkeypatch, body=SCORE):
    def fake_clean(xml, out_dir, **kwargs):
        target = os.path.join(out_dir, CLEANED)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(body)
        kwargs["check"].update(status="passed", reset=[], rejected=[])
        return target, xml

    monkeypatch.setattr(pipeline, "run_clean", fake_clean)
    monkeypatch.setattr(server, "_label_bounds", lambda *a: None)


def test_undo_is_refused_once_the_score_is_worked_on_after_the_clean(song, monkeypatch):
    _clean_with(monkeypatch)
    server._run_clean(song.slug)
    assert clean_backup.latest(state.load(song.slug))["changed"] is False

    # Lyrics imported (or a fix, or an upload) after the clean: work Undo would lose.
    worked_on = SCORE.replace("</Chord>", "<Lyrics><text>la</text></Lyrics></Chord>", 1)
    with open(song.cleaned_path(), "w", encoding="utf-8") as fh:
        fh.write(worked_on)

    fresh = state.load(song.slug)
    assert clean_backup.latest(fresh)["changed"] is True
    reply = TestClient(server.app).post(f"/api/songs/{song.slug}/undo-clean")
    assert reply.status_code == 409
    assert "changed since the last clean" in reply.json()["detail"]
    assert open(song.cleaned_path(), encoding="utf-8").read() == worked_on
    # The kept score is still there, untouched, for whoever wants it by hand.
    assert clean_backup.latest(fresh) is not None
