"""The song's language in the app (#484): set when the song is made, changed on the Start panel,
kept in its own file so a job saving an older .song.json cannot undo it."""
import json
import os

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.sing_song import language_check, progress
from src.song_app import server, state


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    return TestClient(server.app), songs


def new_song(c, **form):
    return c.post("/api/songs", data=dict(name="Messias", **form),
                  files={"pdf": ("messias.pdf", b"%PDF-1.4\n", "application/pdf")})


def test_the_new_song_form_saves_the_language(client):
    c, songs = client
    r = new_song(c, language="en")
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    on_disk = json.loads((songs / slug / language_check.FILE).read_text())
    assert on_disk["language"] == "en" and on_disk["by"] == "app"
    assert c.get(f"/api/songs/{slug}").json()["language"] == "en"
    assert [s["language"] for s in c.get("/api/songs").json()] == ["en"]


def test_no_language_on_the_form_saves_none_and_a_wrong_one_is_refused(client):
    c, songs = client
    slug = new_song(c).json()["slug"]
    assert not (songs / slug / language_check.FILE).exists()
    assert c.get(f"/api/songs/{slug}").json()["language"] is None
    r = new_song(c, language="xx")
    assert r.status_code == 400 and "language must be one of" in r.text
    assert len(os.listdir(songs)) == 1


def test_the_languages_list_starts_with_finnish(client):
    c, _ = client
    langs = c.get("/api/languages").json()["languages"]
    assert langs[0] == {"code": "fi", "name": "Finnish"}
    assert {"code": "en", "name": "English"} in langs


def test_the_start_panel_changes_the_language(client):
    c, songs = client
    slug = new_song(c, language="fi").json()["slug"]
    r = c.post(f"/api/songs/{slug}/language", json={"language": "en"})
    assert r.json() == {"language": "en", "previous": "fi", "note": None}
    assert language_check.saved(str(songs / slug)) == "en"
    assert c.post(f"/api/songs/{slug}/language", json={"language": "xx"}).status_code == 400
    assert c.post("/api/songs/nobody/language", json={"language": "en"}).status_code == 404


def test_a_run_singing_in_another_language_is_named(client):
    c, songs = client
    slug = new_song(c, language="fi").json()["slug"]
    progress.write(str(songs / slug), {"state": "singing", "pid": os.getpid(), "language": "fi", "sing": True})
    note = c.post(f"/api/songs/{slug}/language", json={"language": "en"}).json()["note"]
    assert f"pid {os.getpid()}" in note and "Finnish (fi)" in note and "re-singing" in note
    # the same language, or a finished run, says nothing
    assert c.post(f"/api/songs/{slug}/language", json={"language": "fi"}).json()["note"] is None
    progress.write(str(songs / slug), {"state": "done", "pid": os.getpid(), "language": "fi", "sing": True})
    assert c.post(f"/api/songs/{slug}/language", json={"language": "en"}).json()["note"] is None


def test_a_job_saving_an_older_copy_does_not_undo_the_language(client):
    c, songs = client
    slug = new_song(c, language="fi").json()["slug"]
    loaded_by_a_job = state.load(slug)              # a clean starts with this copy
    c.post(f"/api/songs/{slug}/language", json={"language": "en"})
    loaded_by_a_job.save()                           # ... and saves it back minutes later
    assert state.load(slug).language == "en"
