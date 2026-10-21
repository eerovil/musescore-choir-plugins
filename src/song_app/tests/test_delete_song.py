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


def test_a_job_starting_after_the_delete_is_refused_and_recreates_nothing(client, monkeypatch):
    """The clean route found the song, then the delete moved it before the job started."""
    c, song, songs = client
    monkeypatch.setattr(server, "is_scanning", lambda s: bool(state.trash(s.slug)) and False)
    r = c.post(f"/api/songs/{song.slug}/clean")
    assert r.status_code == 404
    assert not os.path.exists(songs / "talviuni")


def test_start_if_idle_on_a_moved_folder_raises_and_writes_nothing(tmp_path):
    gone = tmp_path / "songs" / "talviuni"
    with pytest.raises(job_state.SongGone):
        job_state.start_if_idle(str(gone), "clean", ("clean",))
    assert not gone.exists()


def test_delete_and_job_start_racing_never_leave_a_phantom_folder(client):
    """Either the job starts first and the delete is refused, or the delete wins and
    the job is refused -- never a recreated folder holding a running job."""
    import threading
    from fastapi import HTTPException

    _c, _song, songs = client
    for i in range(40):
        s = state.create("Talviuni", per_system=False)
        barrier = threading.Barrier(2)
        out = {}

        def delete():
            barrier.wait()
            try:
                server.api_delete_song(s.slug)
                out["deleted"] = True
            except HTTPException as exc:
                out["deleted"] = exc.status_code

        def start():
            barrier.wait()
            try:
                out["started"] = bool(job_state.start_if_idle(s.dir, "clean", ("clean",)))
            except job_state.SongGone:
                out["started"] = False

        threads = [threading.Thread(target=delete), threading.Thread(target=start)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if out["started"]:
            assert out["deleted"] == 409
            assert (songs / s.slug / ".song.json").exists()
            job_state.finish(s.dir, "clean")
            server.api_delete_song(s.slug)
        else:
            assert out["deleted"] is True
        assert not os.path.exists(songs / s.slug), f"round {i} left a folder behind"
