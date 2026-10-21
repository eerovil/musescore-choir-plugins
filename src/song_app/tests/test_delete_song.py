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


def test_a_stale_save_does_not_bring_a_deleted_song_back(client):
    c, song, songs = client
    stale = state.load(song.slug)                  # loaded before the delete
    c.delete(f"/api/songs/{song.slug}")
    stale.data["name"] = "Renamed"
    with pytest.raises(job_state.SongGone):
        stale.save()
    assert not os.path.exists(songs / "talviuni")


def test_a_stale_save_does_not_overwrite_a_new_song_with_the_same_slug(client):
    c, song, songs = client
    stale = state.load(song.slug)
    c.delete(f"/api/songs/{song.slug}")
    fresh = state.create("Talviuni", per_system=False)
    assert fresh.slug == "talviuni"
    assert fresh.data["created_at"] != stale.data["created_at"]
    stale.data["name"] = "Old copy"
    with pytest.raises(job_state.SongGone):
        stale.save()
    assert state.load("talviuni").data["created_at"] == fresh.data["created_at"]
    assert state.load("talviuni").name == "Talviuni"


def test_a_route_saving_after_the_delete_answers_404(client, monkeypatch):
    """Rename found the song, then the delete moved it before the save."""
    c, song, songs = client
    real = state.Song.save

    def delete_then_save(self):
        if not getattr(delete_then_save, "done", False):
            delete_then_save.done = True
            state.trash(self.slug)
        real(self)

    monkeypatch.setattr(state.Song, "save", delete_then_save)
    r = c.post(f"/api/songs/{song.slug}/rename", json={"name": "Renamed"})
    assert r.status_code == 404
    assert not os.path.exists(songs / "talviuni")


def test_an_old_clean_cannot_start_on_a_new_song_with_the_same_slug(client, monkeypatch):
    """Clean loaded song A; A was deleted and B created as `talviuni` before the job
    started. The old request is refused and B gets no job."""
    c, song, songs = client
    made = {}

    def replace_then_not_scanning(s):
        if "b" not in made:
            state.trash(s.slug)
            made["b"] = state.create("Talviuni", per_system=False)
        return False

    monkeypatch.setattr(server, "is_scanning", replace_then_not_scanning)
    r = c.post(f"/api/songs/{song.slug}/clean")
    assert r.status_code == 404
    assert made["b"].slug == "talviuni"
    assert job_state.load(made["b"].dir) == {}
    assert state.load("talviuni").data == made["b"].data


def test_a_song_made_while_an_upload_is_read_keeps_its_files(client, monkeypatch):
    """#428: while song A's PDF upload is still being read, A (if it exists yet) is
    deleted and B takes the slug. A's files must never land in B."""
    from starlette.datastructures import UploadFile
    c, song, songs = client
    c.delete(f"/api/songs/{song.slug}")            # free `talviuni` for A
    made = {}
    real_read = UploadFile.read

    async def read_then_replace(self, *args, **kwargs):
        data = await real_read(self, *args, **kwargs)
        if self.filename == "a.pdf" and "b" not in made:
            if (songs / "talviuni").exists():
                state.trash("talviuni")
            made["b"] = state.create("Talviuni", per_system=False,
                                     sources={"pdf": ("a.pdf", b"B's own pdf")}, stage="scan")
        return data

    monkeypatch.setattr(UploadFile, "read", read_then_replace)
    r = c.post("/api/songs", data={"name": "Talviuni"},
               files={"xml": ("a.mscx", b"<museScore/>"), "pdf": ("a.pdf", b"A's pdf")})
    assert r.status_code == 200, r.text
    b = made["b"]
    assert b.slug == "talviuni" and r.json()["slug"] == "talviuni-2"
    assert (songs / "talviuni" / "a.pdf").read_bytes() == b"B's own pdf"
    assert not (songs / "talviuni" / "a.mscx").exists()
    assert state.load("talviuni").data == b.data
    a = state.load("talviuni-2")
    assert a.data["sources"] == {"xml": "a.mscx", "pdf": "a.pdf"} and a.stage == "clean"
    assert (songs / "talviuni-2" / "a.pdf").read_bytes() == b"A's pdf"
