"""After a YouTube upload the stemmanauhat site is asked to refresh now (#321).

No request leaves the test: ``urlopen`` is stubbed, and what is checked is the
request the app would send, that a failure never fails the upload, and that the
record and delete routes ask exactly when they should.
"""
import io
import json
import os
import time
import urllib.error

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import server, site_refresh, state


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("STEMMANAUHAT_DISPATCH_TOKEN", "STEMMANAUHAT_REPO",
                "AGENTDECK_API_URL", "AGENTDECK_URL"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        return _Response(204)

    monkeypatch.setattr(site_refresh.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_no_token_sends_nothing_and_says_the_refresh_is_off(sent):
    lines = []
    assert site_refresh.refresh_stemmanauhat(lines.append) is False
    assert sent == []
    assert "off" in lines[0]


def test_a_token_dispatches_the_update_workflow_on_main(sent, monkeypatch):
    monkeypatch.setenv("STEMMANAUHAT_DISPATCH_TOKEN", "tok")
    lines = []
    assert site_refresh.refresh_stemmanauhat(lines.append) is True
    [request] = sent
    assert request.full_url == ("https://api.github.com/repos/eerovil/stemmanauhat"
                                "/actions/workflows/update-videos.yml/dispatches")
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer tok"
    assert json.loads(request.data) == {"ref": "main"}
    assert "Asked the stemmanauhat site to refresh" in lines[0]


def test_the_repository_can_be_named(sent, monkeypatch):
    monkeypatch.setenv("STEMMANAUHAT_DISPATCH_TOKEN", "tok")
    monkeypatch.setenv("STEMMANAUHAT_REPO", "someone/site")
    site_refresh.refresh_stemmanauhat(lambda m: None)
    assert "/repos/someone/site/" in sent[0].full_url


@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("u", 403, "Forbidden", {},
                           io.BytesIO(b'{"message": "Resource not accessible"}')),
    urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"")),
    urllib.error.URLError("no network"),
    TimeoutError("timed out"),
])
def test_a_failed_dispatch_is_a_warning_not_an_error(monkeypatch, error):
    monkeypatch.setenv("STEMMANAUHAT_DISPATCH_TOKEN", "tok")

    def fail(request, timeout=None):
        raise error

    monkeypatch.setattr(site_refresh.urllib.request, "urlopen", fail)
    lines = []
    assert site_refresh.refresh_stemmanauhat(lines.append) is False
    assert lines[0].startswith("Could not refresh the stemmanauhat site")
    assert "own schedule" in lines[0]


# --- the routes ------------------------------------------------------------

@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    s = state.create("My Song", per_system=False)
    cleaned = s.path("mysong_cleaned.mscx")
    with open(cleaned, "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    s.data["cleaned"] = os.path.basename(cleaned)
    s.data["stage"] = "upload"
    s.data["record"] = {"outputs": []}
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


@pytest.fixture
def refreshes(monkeypatch):
    calls = []

    def fake(log):
        calls.append(log)
        return True

    monkeypatch.setattr(site_refresh, "refresh_stemmanauhat", fake)
    return calls


def _finished(client, slug):
    for _ in range(250):
        data = client.get(f"/api/songs/{slug}").json()
        if not data.get("recording"):
            return data
        time.sleep(0.02)
    raise AssertionError("the record run never finished")


def _fake_upload(monkeypatch, uploads):
    import src.stemmanauha.create_video as create_video

    def fake_run(**kwargs):
        for n in range(uploads):
            kwargs["on_uploaded"]({"video_id": f"v{n}", "playlist_id": "PL1",
                                   "playlist_title": "Kuoro"})
        return []

    monkeypatch.setattr(create_video, "run", fake_run)


def test_an_upload_asks_the_site_to_refresh_once(client, song, monkeypatch, refreshes):
    _fake_upload(monkeypatch, uploads=3)
    client.post(f"/api/songs/{song.slug}/record", json={"upload_only": True})
    data = _finished(client, song.slug)
    assert len(refreshes) == 1
    assert data["record"]["site_refresh"]["ok"] is True
    assert data["record"]["error"] is None


def test_an_upload_that_uploaded_nothing_does_not_refresh(client, song, monkeypatch,
                                                         refreshes):
    _fake_upload(monkeypatch, uploads=0)
    client.post(f"/api/songs/{song.slug}/record", json={"upload_only": True})
    _finished(client, song.slug)
    assert refreshes == []


def test_a_failed_refresh_leaves_the_upload_done(client, song, monkeypatch):
    _fake_upload(monkeypatch, uploads=1)
    monkeypatch.setenv("STEMMANAUHAT_DISPATCH_TOKEN", "tok")

    def fail(request, timeout=None):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(site_refresh.urllib.request, "urlopen", fail)
    client.post(f"/api/songs/{song.slug}/record", json={"upload_only": True})
    data = _finished(client, song.slug)
    assert data["stage"] == "upload"
    assert data["record"]["error"] is None
    assert data["record"]["site_refresh"]["ok"] is False


def test_deleting_the_uploads_asks_the_site_to_refresh(client, song, monkeypatch,
                                                       refreshes):
    song.data["record"]["uploads"] = [{"video_id": "v1"}]
    song.save()
    import src.stemmanauha.upload_to_youtube as upload_to_youtube
    monkeypatch.setattr(upload_to_youtube, "delete_videos", lambda ids, log=None: None)
    response = client.post(f"/api/songs/{song.slug}/youtube-delete")
    assert response.status_code == 200
    assert len(refreshes) == 1
