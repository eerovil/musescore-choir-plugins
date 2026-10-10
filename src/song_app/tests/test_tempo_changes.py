"""Tempo changes recorded on the Record panel (#387).

Cleaning keeps no tempo marks and a scan never had any, so a song that changes
speed on the page played at one speed. The Record panel now keeps a list of
changes beside the opening BPM, and every render, preview and publish puts them
back. Here: how the list is checked and kept, and that each path is handed it.
The marks themselves and MuseScore's clock are pinned in `src/scrollvideo/tests`.
"""

import json
import os
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from src.song_app import pipeline, server, state  # noqa: E402

CHANGES = [{"measure": 13, "bpm": 112}, {"measure": 23, "bpm": "start"},
           {"measure": 34, "bpm": 132}]


@pytest.fixture(autouse=True)
def _no_real_deck(monkeypatch):
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)


@pytest.fixture
def song(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    s = state.create("My Song", per_system=False)
    cleaned = s.path("mysong_cleaned.mscx")
    bar = "<Measure><voice><Chord><durationType>whole</durationType>" \
          "<Note><pitch>60</pitch></Note></Chord></voice></Measure>"
    with open(cleaned, "w") as fh:
        fh.write('<museScore><Score><Part><Staff id="1"/><trackName>T1</trackName>'
                 f'</Part><Staff id="1">{bar * 40}</Staff></Score></museScore>')
    s.data["cleaned"] = os.path.basename(cleaned)
    s.data["stage"] = "record"
    s.data["review"] = {"approved_against": state.file_fingerprint(cleaned)}
    s.save()
    return s


@pytest.fixture
def client(song):
    return TestClient(server.app)


def _record(song):
    return state.load(song.slug).data.get("record", {})


def test_the_changes_are_kept_sorted_by_bar(client, song):
    response = client.post(f"/api/songs/{song.slug}/record-settings",
                           json={"tempo_changes": list(reversed(CHANGES))})

    assert response.status_code == 200
    assert _record(song)["tempo_changes"] == CHANGES
    assert response.json()["record"]["tempo_changes"] == CHANGES


def test_the_same_list_as_text_is_read_the_same(client, song):
    client.post(f"/api/songs/{song.slug}/record-settings",
                json={"tempo_changes": json.dumps(CHANGES)})
    assert _record(song)["tempo_changes"] == CHANGES


def test_saving_other_settings_keeps_the_changes(client, song):
    client.post(f"/api/songs/{song.slug}/record-settings", json={"tempo_changes": CHANGES})
    client.post(f"/api/songs/{song.slug}/record-settings", json={"top_margin": 3})
    assert _record(song)["tempo_changes"] == CHANGES


@pytest.mark.parametrize("bad, message", [
    ([{"measure": 1, "bpm": 100}], "bar 1"),
    ([{"measure": 41, "bpm": 100}], "to 40"),
    ([{"measure": 5, "bpm": 10}], "between 20 and 300"),
    ([{"measure": 5, "bpm": "fast"}], "whole number"),
    ([{"bpm": 100}], "bar number"),
    ([{"measure": 5, "bpm": 90}, {"measure": 5, "bpm": 100}], "two tempo changes"),
    ("not json", "not readable"),
])
def test_a_change_the_render_could_not_play_is_refused(client, song, bad, message):
    response = client.post(f"/api/songs/{song.slug}/record-settings",
                           json={"tempo_changes": bad})
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert _record(song) == {}


def test_the_render_is_handed_the_changes(client, song, monkeypatch):
    seen = {}

    def fake(song_dir, cleaned, name, **kwargs):
        seen.update(kwargs)
        return []
    monkeypatch.setattr(pipeline, "run_scroll_video", fake)
    monkeypatch.setattr(server.verification, "verify_media", lambda *a, **k: {})
    client.post(f"/api/songs/{song.slug}/record",
                json={"renderer": "scroll", "tempo_changes": CHANGES})
    for _ in range(250):
        if not client.get(f"/api/songs/{song.slug}").json().get("recording"):
            break
        time.sleep(0.02)

    assert seen["tempo_changes"] == CHANGES
    assert _record(song)["tempo_changes"] == CHANGES


def test_a_preview_is_of_the_changes_it_was_asked_for(client, song, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "scroll_preview",
                        lambda *_a, **kw: (calls.append(kw), {"ok": True})[1])
    client.get(f"/api/songs/{song.slug}/scroll-preview",
               params={"tempo_changes": json.dumps(CHANGES)})

    assert calls[0]["tempo_changes"] == CHANGES
    assert _record(song)["tempo_changes"] == CHANGES


def test_a_preview_that_names_none_plays_the_songs_own(client, song, monkeypatch):
    """Opened without the list, a preview must neither drop nor un-save it."""
    client.post(f"/api/songs/{song.slug}/record-settings", json={"tempo_changes": CHANGES})
    calls = []
    monkeypatch.setattr(pipeline, "scroll_preview",
                        lambda *_a, **kw: (calls.append(kw), {"ok": True})[1])
    client.get(f"/api/songs/{song.slug}/scroll-preview")

    assert calls[0]["tempo_changes"] == CHANGES
    assert _record(song)["tempo_changes"] == CHANGES


def test_changing_a_tempo_prepares_the_preview_again(song, monkeypatch):
    calls = []

    def fake(mscx_path, out_dir, **kwargs):
        calls.append(kwargs)
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, "audio-source.mscx"), "w").close()
        return {"duration": 1.0}
    monkeypatch.setattr("src.scrollvideo.preview.preview", fake)
    cleaned = song.cleaned_path()

    pipeline.scroll_preview(song.dir, cleaned, tempo_changes=CHANGES)
    pipeline.scroll_preview(song.dir, cleaned, tempo_changes=CHANGES)
    pipeline.scroll_preview(song.dir, cleaned, tempo_changes=CHANGES[:1])
    pipeline.scroll_preview(song.dir, cleaned)

    assert [c["tempo_changes"] for c in calls] == [CHANGES, CHANGES[:1], None]
