"""The by-system lyric editor's routes (#397): an import of changed boxes, the warnings
kept per box, and where each staff of a printed system is.

Runs on a copy of the Virta fixture (a real scan, its bands and its cleaned score).
The staff positions need poppler and skip without it, as the other crop tests do.
"""

import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from src.song_app import pipeline, server, state, system_finder

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "fixtures", "virta-venhetta-vie")
CLEANED = "Virta-venhetta-vie_cleaned.mscx"
needs_poppler = pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs poppler")


@pytest.fixture
def song(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-musescore"))
    dest = tmp_path / "virta-venhetta-vie"
    dest.mkdir()
    for stage in ("00-registered", "10-cleaned"):
        src = os.path.join(FIXTURE, stage)
        for name in os.listdir(src):
            shutil.copyfile(os.path.join(src, name), dest / name)
    data = json.loads((dest / ".song.json").read_text())
    data.update(cleaned=CLEANED, stage="lyrics")
    (dest / ".song.json").write_text(json.dumps(data))
    return TestClient(server.app), "virta-venhetta-vie", dest


def _text(api, slug, system, key, verse=1):
    grid = api.get(f"/api/songs/{slug}/lyric-grid").json()
    return [b for b in grid["boxes"][str(system)] if b["key"] == key][0]["text"].get(str(verse), "")


def test_an_import_of_one_box_changes_that_box_only(song):
    api, slug, dest = song
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {
        "1": " ".join(["la"] * 11), "3": " ".join(["do"] * 8)}}})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == []
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"1": {
        "1": " ".join(["mi"] * 11)}}})
    assert r.status_code == 200, r.text
    assert _text(api, slug, 0, "1") == " ".join(["la"] * 11)
    assert _text(api, slug, 0, "3") == " ".join(["do"] * 8)
    assert _text(api, slug, 1, "1") == " ".join(["mi"] * 11)
    # The editor's import is kept beside the paste, not over it.
    assert (dest / "lyrics-editor.json").exists()
    assert api.get(f"/api/songs/{slug}/lyrics-json").text == ""


def test_a_warning_stays_until_its_own_box_is_imported_again(song):
    api, slug, _ = song
    first = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {"1": "yk"}}})
    assert [w["kind"] for w in first.json()["imported"]] == ["too_few"]
    other = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"1": {"2": "kak"}}})
    kinds = sorted((w["measure_start"], w["staff_ids"][0]) for w in other.json()["mismatches"])
    assert kinds == [(1, 1), (4, 2)]                    # system 1's T1 is still short
    fixed = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {
        "1": " ".join(["la"] * 11)}}})
    assert [(w["measure_start"], w["staff_ids"]) for w in fixed.json()["mismatches"]] == [(4, [2])]
    # A verse-2 import does not answer a verse-1 warning.
    v2 = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 2, "boxes": {"1": {
        "2": " ".join(["la"] * 10)}}})
    assert [(w["measure_start"], w.get("verse", 1)) for w in v2.json()["mismatches"]] == [(4, 1)]
    assert _text(api, slug, 1, "2", verse=2) == " ".join(["la"] * 10)


def test_an_emptied_box_takes_its_words_out_and_says_nothing_changed_otherwise(song):
    api, slug, _ = song
    api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {"2": " ".join(["oo"] * 13)}}})
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {"2": ""}}})
    assert r.status_code == 200, r.text
    assert _text(api, slug, 0, "2") == ""
    nothing = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {"X9": "a"}}})
    assert nothing.status_code == 400


def test_a_paste_changes_only_the_systems_it_names(song):
    api, slug, _ = song
    api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"1": {"3": "a b"}}})
    paste = json.dumps([{"measure_start": 1, "lyrics": [
        {"parts": ["T1"], "text": " ".join(["la"] * 11)},
        {"parts": ["T1"], "verse": 2, "text": " ".join(["lu"] * 11)}]}])
    r = api.post(f"/api/songs/{slug}/lyrics", json={"json": paste})
    assert r.status_code == 200, r.text
    assert _text(api, slug, 0, "1") == " ".join(["la"] * 11)
    assert _text(api, slug, 0, "1", verse=2) == " ".join(["lu"] * 11)
    assert _text(api, slug, 1, "3").startswith("a b")
    assert api.get(f"/api/songs/{slug}/lyrics-json").text == paste


@needs_poppler
def test_the_staves_of_a_system_are_found_once_and_paired_with_the_printed_staves(song, monkeypatch):
    api, slug, dest = song
    r = api.get(f"/api/songs/{slug}/lyric-staves/0").json()
    assert r["crop"] == 1 and r["printed"] == [1, 2]
    assert len(r["rows"]) == 2 and r["rows"][0]["top"] == 0.0
    assert r["rows"][0]["bottom"] == r["rows"][1]["top"]
    # Cut just above the lower staff, below the upper one.
    assert r["rows"][0]["staff"] < r["rows"][1]["top"] < r["rows"][1]["staff"]
    assert r["aspect"] > 1

    def no(*_a, **_k):
        raise AssertionError("found again although the crop did not change")

    monkeypatch.setattr(system_finder, "_staves", no)
    assert api.get(f"/api/songs/{slug}/lyric-staves/0").json() == r
    store = dest / ".pages" / "lyric-staves.json"
    saved = json.loads(store.read_text())
    saved["1"]["crop"] = "an-older-crop.png"
    store.write_text(json.dumps(saved))
    monkeypatch.undo()
    monkeypatch.setattr(state, "SONGS_DIR", str(dest.parent))
    seen = []
    real = system_finder._staves
    monkeypatch.setattr(system_finder, "_staves", lambda ink: seen.append(1) or real(ink))
    assert api.get(f"/api/songs/{slug}/lyric-staves/0").json()["printed"] == [1, 2]
    assert seen == [1]


@needs_poppler
def test_staves_that_do_not_match_the_score_are_not_paired(song, monkeypatch):
    api, slug, _ = song
    monkeypatch.setattr(system_finder, "_staves", lambda ink: [(10.0, 30.0, 5.0)])
    r = api.get(f"/api/songs/{slug}/lyric-staves/2").json()
    assert r["printed"] is None and len(r["rows"]) == 1
    assert api.get(f"/api/songs/{slug}/lyric-staves/99").status_code == 404


def test_a_song_without_marked_systems_has_no_picture_to_pair(song):
    api, slug, dest = song
    os.remove(dest / ".systems.json")
    grid = api.get(f"/api/songs/{slug}/lyric-grid").json()
    assert all(s["crop"] is None for s in grid["systems"])
    assert api.get(f"/api/songs/{slug}/lyric-staves/0").json() == {
        "crop": None, "rows": [], "printed": None}
    assert pipeline.LYRIC_STAFF_DPI == system_finder.QUICK_DPI


def _musescore():
    import dotenv
    dotenv.load_dotenv(".env")
    return os.getenv("MUSESCORE_CLI_PATH")


_CLI = _musescore()


@pytest.mark.skipif(not (_CLI and os.path.exists(_CLI)), reason="needs the MuseScore CLI")
def test_what_landed_is_this_systems_bars_with_their_words(song, monkeypatch):
    api, slug, _ = song
    monkeypatch.setenv("MUSESCORE_CLI_PATH", _CLI)
    api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"2": {
        "1": "Pii-pa-loo"}}})
    r = api.get(f"/api/songs/{slug}/landed/2")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg")
    svg = r.text
    assert "Pii" in svg and "loo" in svg
    grid = api.get(f"/api/songs/{slug}/lyric-grid").json()
    first, last = grid["systems"][2]["start"], grid["systems"][2]["end"]
    assert svg.count('class="measure"') == last - first + 1
    assert api.get(f"/api/songs/{slug}/landed/99").status_code == 404


def test_a_warning_shared_by_two_parts_stays_on_the_part_not_fixed(song):
    """Parts with the same range, kind and counts share one warning; fixing one part
    keeps the warning for the other, so the song does not move on with it wrong."""
    api, slug, dest = song
    data = json.loads((dest / ".song.json").read_text())
    data["lyrics"] = {"json": "lyrics.json",
                      "imported_against": state.file_fingerprint(str(dest / CLEANED)),
                      "warnings": [{
        "kind": "too_many", "message": "Measures 1-3 (staffs 1, 2): too many tokens",
        "measure_start": 1, "measure_end": 3, "staff_ids": [1, 2],
        "syllables": 14, "slots": 11, "verse": 1, "voice": 1}]}
    (dest / ".song.json").write_text(json.dumps(data))
    fixed = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {
        "1": " ".join(["la"] * 11)}}})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["imported"] == []
    left = fixed.json()["mismatches"]
    assert [(w["kind"], w["measure_start"], w["staff_ids"]) for w in left] == [
        ("too_many", 1, [2])]
    assert json.loads((dest / ".song.json").read_text())["stage"] != "review"


def test_warnings_made_against_an_older_score_are_not_carried_on(song):
    """After a re-clean the old warnings' staves and bars may mean something else."""
    api, slug, dest = song
    data = json.loads((dest / ".song.json").read_text())
    data["lyrics"] = {"json": "lyrics.json", "imported_against": "the score before a re-clean",
                      "warnings": [{"kind": "too_few", "message": "old", "measure_start": 40,
                                    "measure_end": 42, "staff_ids": [3], "syllables": 1,
                                    "slots": 9}]}
    (dest / ".song.json").write_text(json.dumps(data))
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {
        "1": " ".join(["la"] * 11)}}})
    assert r.json()["mismatches"] == []
    assert json.loads((dest / ".song.json").read_text())["stage"] == "review"


def test_the_reply_names_bars_a_line_ran_on_into(song):
    """A hyphen-ended line runs into the next system while its box is empty, so the
    reply's `written` covers that system too, for the page to refresh its picture."""
    api, slug, _ = song
    grid = api.get(f"/api/songs/{slug}/lyric-grid").json()
    second = grid["systems"][1]
    r = api.post(f"/api/songs/{slug}/lyrics", json={"verse": 1, "boxes": {"0": {
        "1": " ".join(["la"] * 13)}}})
    spans = [(w["start"], w["end"]) for w in r.json()["written"] if w["staff"] == 1]
    assert spans and spans[0][0] == 1 and spans[0][1] >= second["start"]
