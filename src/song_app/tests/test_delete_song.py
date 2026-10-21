"""Deleting a song (#428): the folder goes to songs/.trash/, never while a job runs.

The site keeps its own copy, so a published song is only named in the reply, and
nothing is sent to Cloudflare.
"""
import json
import os

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import job_state, server, state


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    song = state.create("Talviuni", per_system=False)
    with open(song.path("talviuni.mscx"), "w", encoding="utf-8") as fh:
        fh.write("<museScore/>")
    return TestClient(server.app), song, songs


def test_delete_moves_the_folder_to_trash(client):
    c, song, songs = client
    r = c.delete(f"/api/songs/{song.slug}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["published"] == []
    assert not os.path.exists(songs / "talviuni")
    trashed = songs / ".trash" / os.path.basename(body["trash"])
    assert body["trash"].startswith("songs/.trash/talviuni-")
    # Everything is kept, so moving it back restores the song as it was.
    assert json.loads((trashed / ".song.json").read_text())["name"] == "Talviuni"
    assert (trashed / "talviuni.mscx").read_text() == "<museScore/>"
    assert [s["slug"] for s in c.get("/api/songs").json()] == []
    assert c.get(f"/api/songs/{song.slug}").status_code == 404


def test_trash_is_not_imported_as_a_song(client):
    c, song, songs = client
    c.delete(f"/api/songs/{song.slug}")
    assert server.import_legacy() == 0
    assert state.list_songs() == []


def test_a_second_song_of_the_same_name_trashes_beside_the_first(client):
    c, song, songs = client
    first = c.delete(f"/api/songs/{song.slug}").json()["trash"]
    again = state.create("Talviuni", per_system=False)
    assert again.slug == "talviuni"
    second = c.delete(f"/api/songs/{again.slug}").json()["trash"]
    assert first != second
    assert len(os.listdir(songs / ".trash")) == 2


@pytest.mark.parametrize("kind", ["clean", "check", "publish", "scan"])
def test_refused_while_a_job_runs(client, kind):
    c, song, songs = client
    job_state.start(song.dir, kind)
    r = c.delete(f"/api/songs/{song.slug}")
    assert r.status_code == 409
    assert "running" in r.json()["detail"]
    assert os.path.isdir(songs / "talviuni")


def test_refused_while_scanning(client):
    c, song, songs = client
    with open(song.path(".scanning.lock"), "w") as fh:
        fh.write(str(os.getpid()))
    assert c.delete(f"/api/songs/{song.slug}").status_code == 409
    assert os.path.isdir(songs / "talviuni")


def test_a_published_song_is_named_and_the_site_left_alone(client, monkeypatch):
    c, song, songs = client
    song.data["publish"] = {"sites": {"jm": {"choir": "jm", "prefix": "x/1"}}}
    song.save()
    from src.song_app import publish
    monkeypatch.setattr(publish, "publish", lambda *a, **k: pytest.fail("site touched"))
    body = c.delete(f"/api/songs/{song.slug}").json()
    assert body["published"] == ["jm"]


def test_unknown_song_is_404(client):
    c, _song, _songs = client
    assert c.delete("/api/songs/no-such").status_code == 404
    assert c.delete("/api/songs/.trash").status_code == 404
