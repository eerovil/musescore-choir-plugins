"""The song page's Pronunciation list (eeron-omat-taskit#144): list, save, and report stale choices."""
import json
import os
import shutil

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import server, state

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie",
)
SLUG = "virta-venhetta-vie"
PLACES = [{"bar": 1, "beat": 0.0, "word": "the", "n": 1, "parts": ["S"], "line": "the wind blows", "at": 0},
          {"bar": 1, "beat": 1.0, "word": "wind", "n": 1, "parts": ["S", "B"], "line": "the wind blows", "at": 1},
          {"bar": 5, "beat": 0.0, "word": "wind", "n": 1, "parts": ["S", "B"], "line": "blows wind the clock", "at": 1}]
WIND = [{"sounds": "W AY1 N D", "say": "wynd", "as_in": "find"}, {"sounds": "W IH1 N D", "say": "wind", "as_in": "sit"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    dest = songs / SLUG
    dest.mkdir(parents=True)
    for stage in ("00-registered", "10-cleaned"):
        for name in os.listdir(os.path.join(FIXTURE, stage)):
            shutil.copyfile(os.path.join(FIXTURE, stage, name), dest / name)
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setattr(server.song_places, "places", lambda score: [dict(p) for p in PLACES])
    asked = []
    monkeypatch.setattr(server, "_readings", lambda words: asked.append(words) or {"wind": WIND})
    c = TestClient(server.app)
    c.asked, c.dir = asked, dest
    return c


def test_the_list_has_each_place_of_a_word_with_two_readings(client):
    r = client.get(f"/api/songs/{SLUG}/pronunciation")
    assert r.status_code == 200, r.text
    data = r.json()
    assert client.asked == [["the", "wind"]]
    assert [(p["bar"], p["word"]) for p in data["places"]] == [(1, "wind"), (5, "wind")]
    assert data["places"][0]["readings"] == WIND and data["places"][0]["chosen"] is None
    assert data["stale"] == [] and data["everywhere"] == {}


def test_a_choice_is_kept_with_the_song_and_a_lost_place_reported(client):
    body = {"places": [{"bar": 1, "beat": 1, "word": "Wind", "sounds": "W IH1 N D"},
                       {"bar": 9, "beat": 0, "word": "wind", "sounds": "W IH1 N D"}],
            "everywhere": {"read": "R EH1 D"}}
    r = client.post(f"/api/songs/{SLUG}/pronunciation", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [p["chosen"] for p in data["places"]] == ["W IH1 N D", None]
    assert data["stale"] == [{"bar": 9, "beat": 0.0, "word": "wind", "sounds": "W IH1 N D"}]
    saved = json.loads((client.dir / "pronounce.json").read_text())
    assert saved["everywhere"] == {"read": "R EH1 D"} and len(saved["places"]) == 2


def test_a_malformed_choice_is_refused(client):
    r = client.post(f"/api/songs/{SLUG}/pronunciation", json={"places": [{"bar": "x"}]})
    assert r.status_code == 400
    assert not (client.dir / "pronounce.json").exists()
