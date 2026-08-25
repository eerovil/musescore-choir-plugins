"""The preview endpoint: what it prepares, what it reuses, and what it never does.

Preparing a preview engraves the score, so it is cached — and a cache is exactly
where a preview stops being a preview. These tests hold it to the one rule that
matters: what comes back is this score, under these settings, or it is prepared
again.

The engraving itself is pinned in `src/scrollvideo/tests/test_preview.py`; here it
is stubbed, so these run without MuseScore.
"""

import json
import os

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import pipeline, server, state  # noqa: E402

PAYLOAD = {"svg": "<svg/>", "duration": 12.0, "scroll": {"times": [0.0], "xs": [0.0],
                                                         "jump": 1.0}}


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


@pytest.fixture
def prepared(monkeypatch):
    """Stand in for the engraving, and record what it was asked for."""
    calls = []

    def fake(mscx_path, **kwargs):
        calls.append({"path": mscx_path, **kwargs})
        return dict(PAYLOAD, call=len(calls))

    monkeypatch.setattr("src.scrollvideo.preview.preview", fake)
    return calls


def test_the_preview_is_the_current_score_at_the_chosen_size(client, song, prepared):
    response = client.get(f"/api/songs/{song.slug}/scroll-preview",
                          params={"quality": "720p", "top_margin": 12,
                                  "bottom_margin": -5})
    assert response.status_code == 200
    assert response.json()["duration"] == 12.0
    assert response.headers["cache-control"] == "no-cache"

    asked = prepared[0]
    assert asked["path"] == song.cleaned_path()
    assert (asked["width"], asked["height"], asked["fps"]) == (1280, 720, 30)
    assert asked["top_margin_percent"] == 12
    assert asked["bottom_margin_percent"] == -5


def test_looking_again_reuses_the_prepared_preview(client, song, prepared):
    first = client.get(f"/api/songs/{song.slug}/scroll-preview").json()
    second = client.get(f"/api/songs/{song.slug}/scroll-preview").json()
    assert first == second
    assert len(prepared) == 1


def test_editing_the_score_throws_the_old_preview_away(client, song, prepared):
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    with open(song.cleaned_path(), "w") as fh:
        fh.write("<museScore><Score><Staff/></Score></museScore>")
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert len(prepared) == 2


@pytest.mark.parametrize("params", [
    {"top_margin": 8},
    {"bottom_margin": 8},
    {"quality": "1080p"},
])
def test_a_setting_that_moves_the_picture_throws_it_away_too(client, song, prepared,
                                                             params):
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    client.get(f"/api/songs/{song.slug}/scroll-preview", params=params)
    assert len(prepared) == 2


def test_the_tempo_the_app_supplies_is_part_of_what_the_preview_is_of(client, song,
                                                                     prepared):
    """A score with no opening tempo is previewed at the BPM the panel offers."""
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"bpm": 96})
    assert prepared[0]["initial_bpm"] == 96
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"bpm": 120})
    assert prepared[1]["initial_bpm"] == 120


def test_a_score_with_its_own_tempo_ignores_the_offered_one(client, song, prepared,
                                                            monkeypatch):
    monkeypatch.setattr(pipeline, "has_opening_tempo", lambda _path: True)
    client.get(f"/api/songs/{song.slug}/scroll-preview", params={"bpm": 96})
    assert prepared[0]["initial_bpm"] is None


def test_previewing_changes_nothing_about_the_song(client, song, prepared):
    """It is a look at the score, not a step in the workflow."""
    before = open(song.path(".song.json")).read()
    client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert open(song.path(".song.json")).read() == before

    data = client.get(f"/api/songs/{song.slug}").json()
    assert data["stage"] == "record"
    assert not data["recording"]
    assert not data.get("record", {}).get("outputs")


def test_what_a_render_would_refuse_comes_back_as_a_message(client, song, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise NotImplementedError("This score uses Jump (a D.C./D.S. jump)")

    monkeypatch.setattr("src.scrollvideo.preview.preview", refuse)
    response = client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert response.status_code == 400
    assert "D.C./D.S." in response.json()["detail"]


def test_there_is_nothing_to_preview_before_the_score_is_cleaned(client, song,
                                                                 prepared):
    song.data.pop("cleaned")
    song.save()
    response = client.get(f"/api/songs/{song.slug}/scroll-preview")
    assert response.status_code == 400
    assert "clean" in response.json()["detail"].lower()
    assert not prepared


@pytest.mark.parametrize("params", [{"top_margin": -41}, {"bottom_margin": 101}])
def test_a_margin_the_renderer_could_not_use_is_refused(client, song, prepared, params):
    response = client.get(f"/api/songs/{song.slug}/scroll-preview", params=params)
    assert response.status_code == 400
    assert not prepared


def test_a_damaged_cache_file_is_prepared_again_rather_than_served(song, prepared):
    """A half-written file is not a preview; it is just a file."""
    with open(os.path.join(song.dir, pipeline.PREVIEW_CACHE), "w") as fh:
        fh.write('{"key": "whatever", "prev')
    payload = pipeline.scroll_preview(song.dir, song.cleaned_path())
    assert payload["duration"] == 12.0
    assert json.load(open(os.path.join(song.dir, pipeline.PREVIEW_CACHE)))["preview"]
