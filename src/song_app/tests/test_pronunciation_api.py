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
    assert data["stale"] == [] and data["everywhere"] == {} and data["saved"] == []
    assert {"code": "la-de", "name": "Latin (German style)"} in data["languages"]
    assert not any(x["code"] in ("fi", "no") for x in data["languages"])


def test_a_choice_is_kept_with_the_song_and_a_lost_place_reported(client):
    body = {"places": [{"bar": 1, "beat": 1, "word": "Wind", "sounds": "W IH1 N D"},
                       {"bar": 9, "beat": 0, "word": "wind", "sounds": "W IH1 N D"}],
            "everywhere": {"read": "R EH1 D"}}
    r = client.post(f"/api/songs/{SLUG}/pronunciation", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert [p["chosen"] for p in data["places"]] == ["W IH1 N D", None]
    assert data["stale"] == [{"bar": 9, "beat": 0.0, "word": "wind", "sounds": "W IH1 N D", "lang": "en"}]
    saved = json.loads((client.dir / "pronounce.json").read_text())
    assert saved["everywhere"] == {"en": {"read": "R EH1 D"}} and len(saved["places"]) == 2


def test_a_malformed_choice_is_refused(client):
    r = client.post(f"/api/songs/{SLUG}/pronunciation", json={"places": [{"bar": "x"}]})
    assert r.status_code == 400
    assert not (client.dir / "pronounce.json").exists()


def pack(tmp_path, monkeypatch, sounds=("it/s", "it/e", "it/i", "de/h", "it/o", "it/d", "en/ch", "en/ay")):
    (tmp_path / "ufr" / "core").mkdir(parents=True)
    (tmp_path / "ufr" / "core" / "phonemes.json").write_text(json.dumps(list(sounds)))
    monkeypatch.setenv("UFR_DIR", str(tmp_path / "ufr"))


def save(client, body):
    return client.post(f"/api/songs/{SLUG}/pronunciation", json=body)


def test_a_reading_in_voice_sounds_is_kept_in_its_language_and_shown(client, tmp_path, monkeypatch):
    # #484: a Latin reading in the pack's sounds, keyed with the word's own letters
    pack(tmp_path, monkeypatch)
    r = save(client, {"places": [{"bar": 3, "beat": 0, "word": "Hodie,", "sounds": " de/h  it/o it/d it/i it/e ",
                                  "lang": "la-de"}],
                      "everywhere": {"la": {"Seimessä": "it/s it/e it/i"}}, "langs": ["la", "la-de"]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert {"bar": 3, "beat": 0.0, "word": "hodie", "sounds": "de/h it/o it/d it/i it/e", "lang": "la-de"} in data["saved"]
    assert {"bar": None, "beat": None, "word": "seimessä", "sounds": "it/s it/e it/i", "lang": "la"} in data["saved"]
    assert data["pack"]["la"] == ["de/h", "it/d", "it/e", "it/i", "it/o", "it/s"] and data["note"] is None
    assert "en/ch" not in data["pack"]["la"] and data["language"] is None   # the fixture names none


def test_a_save_replaces_only_the_languages_it_names(client, tmp_path, monkeypatch):
    pack(tmp_path, monkeypatch)
    assert save(client, {"everywhere": {"la": {"jesu": "it/s it/e"}}, "langs": ["la"]}).status_code == 200
    # an older page: no langs and no lang on its entries, a flat everywhere map — all English
    r = save(client, {"places": [{"bar": 1, "beat": 1, "word": "wind", "sounds": "W IH1 N D"}], "everywhere": {"read": "R EH1 D"}})
    assert r.status_code == 200, r.text
    saved = json.loads((client.dir / "pronounce.json").read_text())
    assert saved["everywhere"] == {"la": {"jesu": "it/s it/e"}, "en": {"read": "R EH1 D"}}
    assert saved["places"] == [{"bar": 1, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D", "lang": "en"}]
    assert save(client, {"langs": ["la"]}).status_code == 200
    assert json.loads((client.dir / "pronounce.json").read_text())["everywhere"] == {"en": {"read": "R EH1 D"}}


@pytest.mark.parametrize("entry,says", [
    ({"word": "child", "sounds": "en/ch AY1 L D", "lang": "en"}, "not both"),
    ({"word": "Jesu", "sounds": "Y EY1 S UW0", "lang": "la"}, "ARPAbet is for English"),
    ({"word": "Jesu", "sounds": "it/j it/e", "lang": "la"}, "no sound 'it/j'"),
    ({"word": "Jesu", "sounds": "it/s", "lang": "fi"}, "not a language g2p sings"),
])
def test_a_reading_that_cannot_be_sung_is_refused_naming_the_word(client, tmp_path, monkeypatch, entry, says):
    pack(tmp_path, monkeypatch)
    r = save(client, {"everywhere": {entry["lang"]: {entry["word"]: entry["sounds"]}}, "langs": [entry["lang"]]})
    assert r.status_code == 400 and says in r.text and entry["word"] in r.text
    assert not (client.dir / "pronounce.json").exists()


def test_without_a_voice_pack_only_the_shape_is_checked(client, tmp_path, monkeypatch):
    monkeypatch.setenv("UFR_DIR", str(tmp_path / "nowhere"))
    r = save(client, {"everywhere": {"la": {"jesu": "en/y it/e"}}, "langs": ["la"]})
    assert r.status_code == 400 and "not a Latin voice sound" in r.text
    r = save(client, {"everywhere": {"la": {"jesu": "it/zz it/e"}}, "langs": ["la"]})
    assert r.status_code == 200, r.text
    assert r.json()["pack"] is None and "not checked against a voice pack" in r.json()["note"]


def test_a_reading_outside_langs_is_refused(client, tmp_path, monkeypatch):
    pack(tmp_path, monkeypatch)
    r = save(client, {"everywhere": {"la": {"jesu": "it/s"}}, "langs": ["en"]})
    assert r.status_code == 400 and "not in langs" in r.text


def test_an_english_reading_in_voice_sounds_is_kept_and_a_mix_refused(client, tmp_path, monkeypatch):
    pack(tmp_path, monkeypatch, ("en/ch", "en/aa", "en/iy", "en/l", "en/d"))
    r = save(client, {"everywhere": {"en": {"child": "en/ch en/aa en/iy en/l en/d"}}, "langs": ["en"]})
    assert r.status_code == 200, r.text
    assert json.loads((client.dir / "pronounce.json").read_text())["everywhere"] == {
        "en": {"child": "en/ch en/aa en/iy en/l en/d"}}
    r = save(client, {"everywhere": {"en": {"child": "en/ch AA1 L"}}, "langs": ["en"]})
    assert r.status_code == 400 and "child" in r.text


def test_without_a_pack_a_variant_takes_its_bases_prefixes(client, tmp_path, monkeypatch):
    # la-de has no "prefixes" of its own: Latin's it/ and de/ are what its readings are checked by
    monkeypatch.setenv("UFR_DIR", str(tmp_path / "nowhere"))
    r = save(client, {"everywhere": {"la-de": {"hodie": "de/h it/o it/d it/i it/e"}}, "langs": ["la-de"]})
    assert r.status_code == 200, r.text


def test_two_saves_at_once_both_land(client, tmp_path, monkeypatch):
    # #484: an English save and a Latin save from two pages; the first is held inside the song's
    # lock while the second starts, and the file keeps both
    import threading
    pack(tmp_path, monkeypatch)
    real, inside, go, calls = server.song_places.load, threading.Event(), threading.Event(), []

    def load(song_dir):
        calls.append(1)
        if len(calls) == 1:
            inside.set()
            assert go.wait(5)
        return real(song_dir)
    monkeypatch.setattr(server.song_places, "load", load)
    out = {}

    def post(name, body):
        out[name] = TestClient(server.app).post(f"/api/songs/{SLUG}/pronunciation", json=body)
    first = threading.Thread(target=post, args=("en", {"everywhere": {"en": {"read": "R EH1 D"}}, "langs": ["en"]}))
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=post, args=("la", {"everywhere": {"la": {"jesu": "it/s it/e"}}, "langs": ["la"]}))
    second.start()
    second.join(0.3)
    assert second.is_alive()   # waiting for the lock the first holds
    go.set()
    first.join(5)
    second.join(5)
    assert out["en"].status_code == out["la"].status_code == 200
    assert json.loads((client.dir / "pronounce.json").read_text())["everywhere"] == {
        "en": {"read": "R EH1 D"}, "la": {"jesu": "it/s it/e"}}
