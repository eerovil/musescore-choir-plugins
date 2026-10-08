"""The file watcher must not put back a song state it loaded before a route saved.

On 2026-10-04 a lyric import on Ketun joululaulu answered `stage: review` with its
`lyrics` record, and moments later `.song.json` had neither (#252). The import
rewrites the cleaned score in place, which wakes the watcher; the watcher loaded the
song before the import saved, checked the score's health, and saved the whole copy
it had loaded — after the import. Nothing said the import had been undone.

These drive that interleaving on purpose rather than hoping a thread loses a race.
"""
import os
import types

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from src.song_app import health, pipeline, server, state

SCORE = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Staff id="1">
  <Measure><voice>
    <Chord><durationType>whole</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
</Staff>
</Score></museScore>"""

CLEANED = "kettu_cleaned.mscx"
LYRICS = '[{"measure_start": 1, "lyrics": [{"text": "La", "parts": ["T1"]}]}]'


@pytest.fixture
def client(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-such-musescore"))
    song = state.create("Kettu", per_system=False)
    with open(song.path(CLEANED), "w", encoding="utf-8") as fh:
        fh.write(SCORE)
    song.data["cleaned"] = CLEANED
    song.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    song.set_stage("fix")
    song.save()

    def fake_import(json_path, cleaned, replace=True):
        # The real import writes the words into the score in place; that write is
        # what wakes the watcher. Its content is not under test here.
        _edit(cleaned, "<!-- lyrics -->")
        return types.SimpleNamespace(mismatches=[], ok=True)

    monkeypatch.setattr(pipeline, "run_lyric_import", fake_import)
    return TestClient(server.app), song


def _edit(path, marker):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(marker + "\n")


def _import(api, slug):
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": LYRICS})
    assert r.status_code == 200, r.text
    assert state.load(slug).stage == "review"


def _assert_import_survived(slug):
    fresh = state.load(slug)
    assert fresh.stage == "review"
    assert fresh.data["lyrics"]["json"] == "lyrics.json"
    assert fresh.data["lyrics"]["imported_against"] == state.file_fingerprint(fresh.cleaned_path())


def test_a_rescan_from_a_copy_loaded_before_the_import_keeps_the_import(client):
    """The watcher loaded first and saved last: the import's record still stands."""
    api, song = client
    stale = state.load(song.slug)
    _import(api, song.slug)
    server._rescan(stale)
    _assert_import_survived(song.slug)
    # And the caller is answered with what is on disk, not with its old copy.
    assert stale.stage == "review"


def test_an_import_landing_while_the_watcher_checks_health_survives(client, monkeypatch):
    """The shape seen on the host: a hand edit wakes the watcher, the import runs
    while the watcher is still checking that edit, and the watcher writes last."""
    api, song = client
    _edit(song.cleaned_path(), "<!-- ties added in MuseScore -->")
    real_scan = health.scan
    ran = []

    def scan_with_an_import_in_the_middle(path):
        found = real_scan(path)
        if not ran:
            ran.append(True)
            _import(api, song.slug)
        return found

    monkeypatch.setattr(health, "scan", scan_with_an_import_in_the_middle)
    assert server._on_cleaned_saved(song.slug)
    assert ran
    _assert_import_survived(song.slug)
    # The import's health record, checked against the file as it is now, is kept
    # rather than replaced by the watcher's findings about the edit before it.
    fresh = state.load(song.slug)
    assert fresh.data["health"]["checked_against"] == state.file_fingerprint(fresh.cleaned_path())
    assert fresh.data["cleaned_fingerprint"] == state.file_fingerprint(fresh.cleaned_path())


def test_the_watcher_leaves_an_import_alone_while_its_health_is_checked(client, monkeypatch):
    """The import claims its write before checking health (#336): the watcher
    waking in between finds nothing to do and tells the page nothing, so the page
    redraws only the systems the import says it changed."""
    api, song = client
    woke = []
    real = server._health_scan

    def scan_with_the_watcher_waking(s, cleaned):
        woke.append(server._on_cleaned_saved(song.slug))
        return real(s, cleaned)

    monkeypatch.setattr(server, "_health_scan", scan_with_the_watcher_waking)
    _import(api, song.slug)
    assert woke == [False]
    _assert_import_survived(song.slug)


def test_the_watcher_still_records_an_edit_made_in_musescore(client):
    """Writing only its own fields must not stop it writing them."""
    api, song = client
    song.data["review"] = {"approved": True}
    song.save()
    _edit(song.cleaned_path(), "<!-- slur added -->")
    assert server._on_cleaned_saved(song.slug)
    fresh = state.load(song.slug)
    fp = state.file_fingerprint(fresh.cleaned_path())
    assert fresh.data["cleaned_fingerprint"] == fp
    assert fresh.data["health"]["checked_against"] == fp
    assert fresh.data["review"] == {"approved": True}
    # Nothing changed since, so a second wake-up is nothing to do.
    assert not server._on_cleaned_saved(song.slug)


def test_a_save_never_leaves_half_a_file_or_a_temp_file(client):
    api, song = client
    song.data["name"] = "Kettu " * 1000
    song.save()
    assert not os.path.exists(song.state_path() + ".tmp")
    assert state.load(song.slug).name == song.data["name"]
