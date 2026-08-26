"""Video margin settings travel from the Record API to the scrolling renderer."""

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
    s = state.create("Margin Song", per_system=False)
    cleaned = s.path("margin_cleaned.mscx")
    with open(cleaned, "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    s.data["cleaned"] = os.path.basename(cleaned)
    s.data["stage"] = "record"
    s.data["review"] = {"approved_against": state.file_fingerprint(cleaned)}
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


def _finished(client, slug):
    for _ in range(250):
        data = client.get(f"/api/songs/{slug}").json()
        if not data.get("recording"):
            return data
        time.sleep(0.02)
    raise AssertionError("the record run never finished")


def _record_watching_the_renderer(monkeypatch) -> dict:
    """Stand in for the renderer and report the settings it was handed."""
    seen = {}

    def fake(song_dir, cleaned, name, **kwargs):
        seen.update(kwargs)
        out = os.path.join(song_dir, "media", "video")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{name} ALL.mp4")
        open(path, "wb").close()
        return [path]

    monkeypatch.setattr(pipeline, "run_scroll_video", fake)
    return seen


def test_top_and_bottom_margins_reach_the_renderer_and_are_remembered(
        client, song, monkeypatch):
    seen = _record_watching_the_renderer(monkeypatch)
    response = client.post(f"/api/songs/{song.slug}/record", json={
        "top_margin": 12,
        "bottom_margin": -8,
    })
    assert response.status_code == 200
    data = _finished(client, song.slug)

    assert seen["top_margin_percent"] == 12
    assert seen["bottom_margin_percent"] == -8
    assert data["record"]["top_margin"] == 12
    assert data["record"]["bottom_margin"] == -8


def test_a_chosen_margin_is_remembered_even_when_the_render_fails(
        client, song, monkeypatch):
    """Framing is a decision, and a failed render must not throw it away.

    The margins used to be written into the song only after a render succeeded,
    so a framing choice made against a render that then failed was gone by the
    next page load and the panel offered the default again.
    """
    def boom(*_args, **_kwargs):
        raise RuntimeError("MuseScore fell over")

    monkeypatch.setattr(pipeline, "run_scroll_video", boom)
    assert client.post(f"/api/songs/{song.slug}/record",
                       json={"bottom_margin": 14}).status_code == 200
    data = _finished(client, song.slug)

    assert data["record"]["error"], "the render really did fail"
    assert data["record"]["bottom_margin"] == 14


def test_a_later_render_inherits_the_margins_this_song_last_chose(
        client, song, monkeypatch):
    """The default only fills a setting this song has never answered."""
    seen = _record_watching_the_renderer(monkeypatch)
    client.post(f"/api/songs/{song.slug}/record", json={"bottom_margin": 14})
    _finished(client, song.slug)

    client.post(f"/api/songs/{song.slug}/record", json={})
    _finished(client, song.slug)

    assert seen["bottom_margin_percent"] == 14


def test_a_song_nobody_framed_renders_with_the_default_bottom_margin(
        client, song, monkeypatch):
    """Asking for nothing is not asking for zero.

    The renderer's own default is 0 — leave the framing alone — but the app wants
    a little white space under the bottom staff so its lyrics are not against the
    frame edge, and nobody should have to type that in every song.
    """
    seen = _record_watching_the_renderer(monkeypatch)
    assert client.post(f"/api/songs/{song.slug}/record", json={}).status_code == 200
    data = _finished(client, song.slug)

    assert seen["top_margin_percent"] == server.DEFAULT_TOP_MARGIN_PERCENT == 0
    assert seen["bottom_margin_percent"] == server.DEFAULT_BOTTOM_MARGIN_PERCENT == 5
    assert data["record"]["bottom_margin"] == 5


def test_an_explicit_zero_bottom_margin_is_not_overridden_by_the_default(
        client, song, monkeypatch):
    """Choosing 0 has to survive — the default only fills an unanswered setting."""
    seen = _record_watching_the_renderer(monkeypatch)
    assert client.post(f"/api/songs/{song.slug}/record",
                       json={"bottom_margin": 0}).status_code == 200
    _finished(client, song.slug)
    assert seen["bottom_margin_percent"] == 0


@pytest.mark.parametrize("payload", [
    {"top_margin": -41},
    {"bottom_margin": 101},
    {"top_margin": "wide"},
])
def test_invalid_video_margins_are_rejected_before_rendering(client, song, payload):
    response = client.post(f"/api/songs/{song.slug}/record", json=payload)
    assert response.status_code == 400
    assert not client.get(f"/api/songs/{song.slug}").json()["recording"]


def test_pipeline_passes_margin_adjustments_to_build_videos(song, monkeypatch):
    seen = {}
    import src.scrollvideo as scrollvideo

    monkeypatch.setattr(scrollvideo, "build_videos",
                        lambda *args, **kwargs: seen.update(kwargs) or [])

    pipeline.run_scroll_video(
        song.dir, song.cleaned_path(), song.slug,
        quality="720p",
        top_margin_percent=7,
        bottom_margin_percent=-3,
    )

    assert (seen["width"], seen["height"], seen["fps"]) == (1280, 720, 30)
    assert seen["top_margin_percent"] == 7
    assert seen["bottom_margin_percent"] == -3
