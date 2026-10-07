"""Saving the Record panel's settings without rendering (#301).

The panel's Preview and Save settings buttons both post here. Before this, a
setting was kept only once a preview had come out or a render had been asked
for, and the tempo, quality and encoder choice not even then, so opening the
preview and leaving lost them.
"""

import os

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import pipeline, server, state  # noqa: E402


def _two_parts(path):
    chord = "<Chord><durationType>whole</durationType><Note><pitch>60</pitch></Note></Chord>"
    parts = "".join(f'<Part><Staff id="{i}"/><trackName>{n}</trackName></Part>'
                    for i, n in ((1, "S1"), (2, "B1")))
    staves = "".join(f'<Staff id="{i}"><Measure><voice>{chord}</voice></Measure></Staff>'
                     for i in (1, 2))
    with open(path, "w") as fh:
        fh.write(f"<museScore><Score>{parts}{staves}</Score></museScore>")


@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    s = state.create("My Song", per_system=False)
    cleaned = s.path("mysong_cleaned.mscx")
    _two_parts(cleaned)
    s.data["cleaned"] = os.path.basename(cleaned)
    s.data["stage"] = "record"
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


SETTINGS = {"quality": "1080p", "hardware_encoding": False, "top_margin": 4,
            "bottom_margin": 9, "staff_groups": "S1 + B1", "bpm": 96}


def _record(song):
    return state.load(song.slug).data.get("record", {})


def test_every_setting_is_kept(client, song):
    response = client.post(f"/api/songs/{song.slug}/record-settings", json=SETTINGS)

    assert response.status_code == 200
    rec = _record(song)
    assert rec["quality"] == "1080p"
    assert rec["hardware_encoding"] is False
    assert (rec["top_margin"], rec["bottom_margin"]) == (4, 9)
    assert rec["staff_groups"] == "S1+B1"      # stored the one way it is written
    assert rec["bpm"] == 96
    assert response.json()["record"]["bpm"] == 96


@pytest.mark.parametrize("bad, message", [
    ({"bottom_margin": 500}, "margin"),
    ({"bpm": 5}, "BPM"),
    ({"bpm": "fast"}, "BPM"),
    ({"staff_groups": "S1+T9"}, "T9"),
])
def test_what_a_render_would_refuse_is_refused_and_nothing_is_kept(client, song,
                                                                   bad, message):
    response = client.post(f"/api/songs/{song.slug}/record-settings",
                           json={**SETTINGS, **bad})

    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert _record(song) == {}


def test_a_score_with_its_own_tempo_keeps_no_tempo(client, song, monkeypatch):
    monkeypatch.setattr(pipeline, "has_opening_tempo", lambda _path: True)
    client.post(f"/api/songs/{song.slug}/record-settings", json=SETTINGS)
    assert "bpm" not in _record(song)


def test_saving_is_refused_while_a_render_runs(client, song):
    with open(server._lock_path(song), "w") as fh:
        fh.write(str(os.getpid()))
    try:
        response = client.post(f"/api/songs/{song.slug}/record-settings", json=SETTINGS)
    finally:
        os.remove(server._lock_path(song))

    assert response.status_code == 409
    assert _record(song) == {}


def test_saving_the_same_settings_again_writes_nothing(client, song, monkeypatch):
    client.post(f"/api/songs/{song.slug}/record-settings", json=SETTINGS)
    saves = []
    original = state.Song.save
    monkeypatch.setattr(state.Song, "save",
                        lambda self: (saves.append(1), original(self))[1])

    client.post(f"/api/songs/{song.slug}/record-settings", json=SETTINGS)
    assert saves == []


def test_a_preview_keeps_the_quality_and_tempo_too(client, song, monkeypatch):
    monkeypatch.setattr(pipeline, "scroll_preview", lambda *_a, **_k: {"ok": True})
    client.get(f"/api/songs/{song.slug}/scroll-preview",
               params={"quality": "720p", "bpm": 104})

    rec = _record(song)
    assert (rec["quality"], rec["bpm"]) == ("720p", 104)
