"""Every problem in one list, each with its choices, in a real browser (#290).

`test_problems.py` pins the rows and the writes. What only exists in the browser is
what a person meets on the Fix stage: one card per bar and part saying everything
wrong there, the lengths and the pitch homr was unsure of offered side by side, a
slur the scan ran between two singers offered back as words, one tap each, and all
of it on a phone.
"""
import json
import os
import socket
import threading
import time

import pytest

_NEEDS = "pip install pytest-playwright && playwright install chromium"
pytest.importorskip("playwright.sync_api", reason=_NEEDS)
pytest.importorskip("pytest_playwright", reason=_NEEDS)
pytest.importorskip("uvicorn")


def _browser_installed() -> bool:
    """Launching is the only honest check; see test_ui_flow for why it runs here."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


if not _browser_installed():
    pytest.skip(_NEEDS, allow_module_level=True)

import uvicorn
from lxml import etree

from src.clean_score.tests.test_cross_voice_slurs import _score as _slur_score
from src.clean_score.utils.cross_voice_slurs import drop_cross_voice_slurs, store_removed
from src.song_app import scan, server, state
from src.song_app.tests.test_bar_readings import _fragment, _score
from src.song_app.tests.test_problems import NOTES

pytestmark = pytest.mark.browser


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unsure_song():
    song = state.create("Unsure Song", per_system=False)
    os.makedirs(song.path("scan"))
    first, second = song.path("scan/system-01.musicxml"), song.path("scan/system-02.musicxml")
    with open(first, "w") as fh:
        fh.write(_fragment(None))
    with open(second, "w") as fh:
        fh.write(_fragment(NOTES))
    song.data["scan"] = {"systems": {
        "1": {"index": 1, "musicxml": "scan/system-01.musicxml",
              "content": scan.content_stamp(first), "bars": 1, "error": None},
        "2": {"index": 2, "musicxml": "scan/system-02.musicxml",
              "content": scan.content_stamp(second), "bars": 2, "error": None}}}
    with open(song.path("song_cleaned.mscx"), "w", encoding="utf-8") as fh:
        fh.write(_score(((1, "B1", 0),)))
    song.data["cleaned"] = "song_cleaned.mscx"
    song.data["stage"] = "fix"
    song.save()
    return song


def _slur_song():
    song = state.create("Slur Song", per_system=False)
    root = _slur_score()
    store_removed(root, drop_cross_voice_slurs(root))
    etree.ElementTree(root).write(song.path("song_cleaned.mscx"), encoding="UTF-8")
    song.data["cleaned"] = "song_cleaned.mscx"
    song.data["stage"] = "fix"
    song.save()
    return song


@pytest.fixture
def live(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")
    unsure, slurred = _unsure_song(), _slur_song()

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(
        server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    try:
        thread.start()
        deadline = time.time() + 30
        while not srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert srv.started, "the app did not start"
        yield f"http://127.0.0.1:{port}", unsure, slurred
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _open_fix(page, base, slug):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".problem")
    return errors


def _fixes(song):
    path = os.path.join(song.dir, "fixes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def _evidence(page, name, what=".problems"):
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.set_viewport_size({"width": 1400, "height": 2000})
        page.locator(what).screenshot(path=os.path.join(out, name))


def test_a_bar_with_unsure_lengths_and_pitch_is_one_card(live, page):
    base, song, _ = live
    errors = _open_fix(page, base, song.slug)
    assert page.locator(".problem").count() == 1
    card = page.locator(".problem")
    assert "m3" in card.inner_text() and "B1" in card.inner_text()
    picks = card.locator(".readpick")
    assert picks.count() == 2
    pitch = picks.nth(1)
    assert "Which note is D3?" in pitch.inner_text()
    assert [o.inner_text().split()[:2] for o in pitch.locator(".readopt").all()] == [
        ["a", "D3"], ["b", "Eb3"], ["c", "C3"]]
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readsvg')].every(i => i.complete && i.naturalWidth > 0)")
    _evidence(page, "unsure-bar-card.png")

    pitch.locator(".readopt").nth(1).click()
    page.wait_for_selector(".readdone")
    assert "Bar 3, B1: pitch b (Eb3)" in page.locator(".readdone").inner_text()
    # The lengths are still asked about.
    assert page.locator(".problem .readpick").count() == 1
    [entry] = _fixes(song)
    assert entry["kind"] == "pitch" and entry["to"] == 51
    assert errors == []


def test_a_removed_slur_is_answered_in_words(live, page):
    base, _, song = live
    errors = _open_fix(page, base, song.slug)
    card = page.locator(".problem")
    assert card.count() == 1
    text = card.inner_text()
    assert "slur to T2 bar 2 removed" in text and "Bar 2, T2: slur from T1 bar 1" in text
    labels = [o.inner_text() for o in card.locator(".readopt").all()]
    assert labels[1].startswith("b") and "Slur in T2 (C4 → C4)" in labels[1]
    assert card.locator(".readnone").count() == 0
    _evidence(page, "removed-slur-card.png")

    card.locator(".readopt").nth(1).click()
    page.wait_for_selector("text=No issues")
    assert "slur answer b (Slur in T2" in page.locator(".readdone").inner_text()
    assert {f["kind"] for f in _fixes(song)} == {"slur", "unmark"}
    _evidence(page, "after-answering.png")
    assert errors == []


def test_it_fits_a_phone(live, page):
    base, song, _ = live
    page.set_viewport_size({"width": 390, "height": 844})
    _open_fix(page, base, song.slug)
    card = page.locator(".problem")
    card.scroll_into_view_if_needed()
    assert card.bounding_box()["width"] <= 390
    for option in card.locator(".readopt").all():
        box = option.bounding_box()
        assert box["x"] + box["width"] <= 390
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readsvg')].every(i => i.complete && i.naturalWidth > 0)")
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        card.screenshot(path=os.path.join(out, "phone-card.png"))
