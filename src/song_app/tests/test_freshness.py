"""What the browser is told about how long it may keep a file.

The app deploys over itself every couple of minutes and a re-recorded part is
written back under the same name, so "the phone shows the old one" is a bug that
happens on an ordinary day. Nothing here checks that a file is *never* cached —
that would throw away the 304s that make the app usable on a phone. It checks
that the browser is made to ask.
"""
import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import server, state


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    with TestClient(server.app) as c:
        yield c


def test_the_app_shell_must_be_revalidated(client):
    for path in ("/", "/index.html", "/app.js", "/style.css"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path


def test_an_unchanged_file_is_still_a_cheap_304(client):
    """no-cache means "ask first", not "send it again every time"."""
    first = client.get("/app.js")
    etag = first.headers["etag"]
    again = client.get("/app.js", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_a_video_url_changes_when_the_video_does(tmp_path):
    vdir = tmp_path / "media" / "video"
    vdir.mkdir(parents=True)
    video = vdir / "demo S1.mp4"
    video.write_bytes(b"first take")

    song = type("S", (), {"slug": "demo", "path": lambda self, *p: str(tmp_path.joinpath(*p))})()
    before = server._media_list(song)[0]["url"]

    video.write_bytes(b"a rather longer second take")
    after = server._media_list(song)[0]["url"]

    assert "?v=" in before
    assert before != after, "a re-recorded part must not reuse the cached URL"


def test_a_video_is_served_with_no_cache(client, tmp_path):
    vdir = tmp_path / "demo" / "media" / "video"
    vdir.mkdir(parents=True)
    (vdir / "demo S1.mp4").write_bytes(b"take")
    (tmp_path / "demo" / ".song.json").write_text('{"name": "Demo", "stage": "record"}')

    response = client.get("/api/songs/demo/media/demo S1.mp4?v=1-4")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
