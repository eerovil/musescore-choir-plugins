"""The lyric import as a job, and Dismiss on a lyric warning (#482)."""

import glob
import io
import json
import os
import shutil
import stat

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from src.clean_score import lyric_txt
from src.song_app import job_state, pipeline, server, state

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "fixtures", "virta-venhetta-vie")
CLEANED = "Virta-venhetta-vie_cleaned.mscx"
PASTE = json.dumps([{"measure_start": 1, "lyrics": [{"staff_number": 1, "text": "yk-si"}]}])


@pytest.fixture
def song(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    dest = tmp_path / "virta-venhetta-vie"
    dest.mkdir()
    for stage in ("00-registered", "10-cleaned"):
        src = os.path.join(FIXTURE, stage)
        for name in os.listdir(src):
            shutil.copyfile(os.path.join(src, name), dest / name)
    data = json.loads((dest / ".song.json").read_text())
    data.update(cleaned=CLEANED, stage="lyrics")
    (dest / ".song.json").write_text(json.dumps(data))
    return TestClient(server.app), "virta-venhetta-vie", dest


def _song(slug):
    return state.load(slug)


def _leftovers(dest):
    return glob.glob(str(dest / f"*{server.LYRICS_TMP}")) + glob.glob(str(dest / ".*.tmp"))


def test_an_import_is_refused_while_another_writer_runs_and_writes_nothing(song):
    api, slug, dest = song
    before = (dest / CLEANED).read_bytes()
    assert state.start_job(_song(slug), "clean", server.WRITERS)
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE})
    assert r.status_code == 409
    assert (dest / CLEANED).read_bytes() == before
    assert not (dest / "lyrics.json").exists()
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {"1": "a"}}})
    assert r.status_code == 409
    assert not (dest / "lyrics-editor.json").exists()


def test_other_writers_and_an_upload_are_refused_while_an_import_runs(song, monkeypatch):
    api, slug, dest = song
    seen = {}
    real = pipeline.run_lyric_import

    def during(*args, **kwargs):
        seen["clean"] = state.start_job(_song(slug), "clean", server.WRITERS)
        seen["upload"] = api.post(
            f"/api/songs/{slug}/score-file",
            files={"file": (CLEANED, io.BytesIO((dest / CLEANED).read_bytes()))}).status_code
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "run_lyric_import", during)
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE})
    assert r.status_code == 200, r.text
    assert seen == {"clean": None, "upload": 409}
    assert not job_state.is_running(str(dest), "lyrics")
    assert _leftovers(dest) == []


def test_a_score_saved_during_the_import_is_kept_and_the_import_refused(song, monkeypatch):
    """The words were placed into the old score; moving them over the new one would
    lose the save. The pasted JSON may already be written: the score is what matters."""
    api, slug, dest = song
    real = pipeline.run_lyric_import
    saved_meanwhile = {}

    def during(*args, **kwargs):
        result = real(*args, **kwargs)
        with open(dest / CLEANED, "ab") as f:
            f.write(b"\n<!-- saved in MuseScore -->\n")
        saved_meanwhile["bytes"] = (dest / CLEANED).read_bytes()
        return result

    monkeypatch.setattr(pipeline, "run_lyric_import", during)
    before = _song(slug).data.get("lyrics")
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE})
    assert r.status_code == 409
    assert "changed" in r.json()["detail"]
    assert (dest / CLEANED).read_bytes() == saved_meanwhile["bytes"]
    assert _song(slug).data.get("lyrics") == before
    assert not job_state.is_running(str(dest), "lyrics")
    assert _leftovers(dest) == []


def test_an_import_that_raises_leaves_no_job_running_and_no_temp_file(song, monkeypatch):
    api, slug, dest = song
    before = (dest / CLEANED).read_bytes()

    def broken(json_path, cleaned, **kwargs):
        with open(kwargs["out_path"], "w") as f:
            f.write("half")
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "run_lyric_import", broken)
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE})
    assert r.status_code == 400 and "boom" in r.json()["detail"]
    assert (dest / CLEANED).read_bytes() == before
    assert not job_state.is_running(str(dest), "lyrics")
    assert job_state.load(str(dest))["lyrics"]["status"] == "failed"
    assert _leftovers(dest) == []
    # And the next import is not refused as busy.
    monkeypatch.undo()
    monkeypatch.setattr(state, "SONGS_DIR", str(dest.parent))
    assert api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE}).status_code == 200


def test_nothing_changed_writes_no_editor_json_and_ends_the_job(song):
    api, slug, dest = song
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"999": {"nope": "a"}}})
    assert r.status_code == 400
    assert not (dest / "lyrics-editor.json").exists()
    assert not job_state.is_running(str(dest), "lyrics")


def test_health_is_not_written_for_a_score_saved_after_the_import(song, monkeypatch):
    api, slug, dest = song
    real = server._health_scan
    marker = {"id": "from-the-import", "kind": "x", "status": "open"}

    def scan_then_save(s, cleaned):
        found = real(s, cleaned)
        with open(cleaned, "ab") as f:
            f.write(b"\n<!-- saved in MuseScore -->\n")
        return found + [marker]

    monkeypatch.setattr(server, "_health_scan", scan_then_save)
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE})
    assert r.status_code == 200, r.text
    issues = (_song(slug).data.get("health") or {}).get("issues") or []
    assert "from-the-import" not in [i.get("id") for i in issues]
    # The import itself landed and was claimed.
    assert _song(slug).data["lyrics"]["imported_against"] != state.file_fingerprint(
        str(dest / CLEANED))


def test_a_dismiss_during_an_import_leaves_the_stage_to_the_import(song, monkeypatch):
    api, slug, dest = song
    w = {"kind": "too_few", "message": "m", "measure_start": 30, "staff_ids": [1]}
    s = _song(slug)
    fp = state.file_fingerprint(str(dest / CLEANED))
    s.data["lyrics"] = {"imported_against": fp, "warnings": [w]}
    s.save()
    real = pipeline.run_lyric_import
    during = {}

    def import_and_dismiss(*args, **kwargs):
        r = api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss",
                     json={"id": lyric_txt.warning_id(w)})
        during["status"] = r.status_code
        during["stage"] = _song(slug).stage
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "run_lyric_import", import_and_dismiss)
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": PASTE})
    assert r.status_code == 200, r.text
    assert during == {"status": 200, "stage": "lyrics"}
    left = _song(slug).data["lyrics"]["warnings"]
    assert lyric_txt.warning_id(w) not in [lyric_txt.warning_id(x) for x in left]
    assert _song(slug).stage == ("review" if not left else "lyrics")


def test_dismiss_needs_an_id_and_a_warning_on_the_list(song):
    api, slug, _dest = song
    assert api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss", json={}).status_code == 400
    assert api.post(f"/api/songs/{slug}/lyrics/warnings/dismiss",
                    json={"id": "nope"}).status_code == 404


def test_the_score_is_saved_whole_through_a_temp_file(tmp_path):
    path = tmp_path / "s_cleaned.mscx"
    path.write_text("<museScore/>")
    os.chmod(path, 0o640)
    lyric_txt._save_mscx(etree.fromstring(b"<museScore><Score/></museScore>"), str(path))
    assert b"<Score/>" in path.read_bytes()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
    assert os.listdir(tmp_path) == ["s_cleaned.mscx"]


def test_a_temp_file_left_by_a_restart_is_removed_on_startup(song, monkeypatch):
    _api, slug, dest = song
    left = dest / (CLEANED + server.LYRICS_TMP)
    left.write_text("half")
    with TestClient(server.app):
        pass
    assert not left.exists()
