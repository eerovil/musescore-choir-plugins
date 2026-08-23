"""The Record stage offers two renderers and defaults to the scrolling one.

Both write "<slug> <part>" files into media/video, so review and upload do not
care which one ran. Neither renderer actually runs here — what is under test is
the routing, the state it records, and the stage it leaves the song in.
"""
import os
import time

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import pipeline, server, state


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
    s.data["stage"] = "record"
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


def _finished(client, slug):
    """The record endpoint hands off to a worker thread; wait for it."""
    for _ in range(250):
        data = client.get(f"/api/songs/{slug}").json()
        if not data.get("recording"):
            return data
        time.sleep(0.02)
    raise AssertionError("the record run never finished")


def _fake_scroll(monkeypatch, seen, parts=("S1", "A1")):
    def fake(song_dir, cleaned, name, *, quality="4k", log=lambda m: None):
        seen.update(song_dir=song_dir, cleaned=cleaned, name=name, quality=quality)
        out = os.path.join(song_dir, "media", "video")
        os.makedirs(out, exist_ok=True)
        made = []
        for part in parts:
            path = os.path.join(out, f"{name} {part}.mp4")
            open(path, "wb").close()
            made.append(path)
        return made
    monkeypatch.setattr(pipeline, "run_scroll_video", fake)


def test_the_scrolling_renderer_is_what_runs_by_default(client, song, monkeypatch):
    seen = {}
    _fake_scroll(monkeypatch, seen)

    assert client.post(f"/api/songs/{song.slug}/record", json={}).json()["started"]
    data = _finished(client, song.slug)

    assert seen["name"] == song.slug, "outputs must be named for the slug"
    assert seen["quality"] == "4k"
    assert data["record"]["renderer"] == "scroll"
    assert data["record"]["outputs"] == [f"{song.slug} S1.mp4", f"{song.slug} A1.mp4"]
    assert data["stage"] == "upload"


def test_the_rendered_files_are_what_review_and_upload_look_for(client, song, monkeypatch):
    """The whole point of the naming: neither stage knows which renderer ran."""
    from src.stemmanauha.create_video import find_merged_outputs
    _fake_scroll(monkeypatch, {})
    client.post(f"/api/songs/{song.slug}/record", json={})
    data = _finished(client, song.slug)

    assert [m["label"] for m in data["media"]] == ["A1", "S1"]
    assert all(m["merged"] for m in data["media"])
    assert [os.path.basename(p) for p in find_merged_outputs(song.dir)] == [
        f"{song.slug} A1.mp4", f"{song.slug} S1.mp4"]


def test_the_size_choice_is_passed_through(client, song, monkeypatch):
    seen = {}
    _fake_scroll(monkeypatch, seen)
    client.post(f"/api/songs/{song.slug}/record", json={"quality": "1080p"})
    _finished(client, song.slug)
    assert seen["quality"] == "1080p"


def test_asking_for_the_screen_recorder_still_gets_it(client, song, monkeypatch):
    called = {}

    def fake_run(**kwargs):
        called.update(kwargs)
        return []

    import src.stemmanauha.create_video as create_video
    monkeypatch.setattr(create_video, "run", fake_run)
    monkeypatch.setattr(pipeline, "run_scroll_video",
                        lambda *a, **k: pytest.fail("scrolling renderer ran instead"))

    client.post(f"/api/songs/{song.slug}/record", json={"renderer": "screen"})
    data = _finished(client, song.slug)
    assert called, "the screen recorder should have been invoked"
    assert data["record"]["renderer"] == "screen"


def test_progress_reporting_cannot_kill_the_render():
    """A render runs for minutes in a worker thread while the browser may come and
    go. Emitting progress to a loop that has closed must not surface there and
    abort work that is going perfectly well."""
    import asyncio

    hub = server.Hub()
    loop = asyncio.new_event_loop()
    loop.close()
    hub.loop = loop

    hub.emit("mysong", {"type": "log", "line": "still going"})   # must not raise
    assert hub.loop is None or hub.loop.is_closed()


def test_rendering_without_a_cleaned_score_is_refused(client, song, monkeypatch):
    os.remove(song.path("mysong_cleaned.mscx"))
    song.data.pop("cleaned", None)
    song.save()
    monkeypatch.setattr(pipeline, "run_scroll_video",
                        lambda *a, **k: pytest.fail("should not render without a score"))

    client.post(f"/api/songs/{song.slug}/record", json={})
    data = _finished(client, song.slug)
    assert "clean" in (data["record"].get("error") or "").lower()
    assert data["stage"] != "upload"
