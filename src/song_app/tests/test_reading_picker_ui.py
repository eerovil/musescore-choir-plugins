"""Picking between homr's readings of an unsure bar, in a real browser (#269).

`test_bar_readings.py` pins the matching and the write. What only exists in the
browser is the half a person meets: the bar's options drawn under the page, one tap
to pick, and the bar showing as decided afterwards.
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

from src.song_app import scan, server, state
from src.song_app.tests.test_bar_readings import READINGS, _fragment, _score

pytestmark = pytest.mark.browser


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    # No score render: the system crop is a nicety, and MuseScore is not under test.
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")

    song = state.create("Reading Panel Song", per_system=False)
    os.makedirs(song.path("scan"))
    first, second = song.path("scan/system-01.musicxml"), song.path("scan/system-02.musicxml")
    with open(first, "w") as fh:
        fh.write(_fragment(None))
    with open(second, "w") as fh:
        fh.write(_fragment(READINGS))
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
        yield f"http://127.0.0.1:{port}", song
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
    # The song sits at Fix, so that is the panel it opens on, at any width.
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".readpick")
    return errors


def _fixes(song):
    path = os.path.join(song.dir, "fixes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def test_the_options_are_drawn_and_one_tap_picks(live, page):
    base, song = live
    errors = _open_fix(page, base, song.slug)
    row = page.locator(".problem")
    assert "m3" in row.inner_text() and "B1" in row.inner_text()
    card = page.locator(".readpick")
    assert card.locator(".readopt").count() == 3
    assert "as read now" in card.locator(".readopt").first.inner_text()
    # Each option really is drawn, not a broken image.
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readsvg')].every(i => i.complete && i.naturalWidth > 0)")

    card.locator(".readopt").nth(1).click()
    page.wait_for_selector(".readdone")
    assert "Bar 3, B1: reading b" in page.locator(".readdone").inner_text()
    assert page.locator(".readpick").count() == 0
    [entry] = _fixes(song)
    assert entry["kind"] == "rhythm" and entry["to"] == ["note_4.", "note_8"]
    assert errors == []


def test_none_of_these_is_said_as_decided(live, page):
    base, song = live
    _open_fix(page, base, song.slug)
    page.locator(".readnone").click()
    page.wait_for_selector(".readdone")
    assert "none of these" in page.locator(".readdone").inner_text()
    assert _fixes(song) == []


def test_it_fits_a_phone(live, page):
    base, song = live
    page.set_viewport_size({"width": 390, "height": 844})
    _open_fix(page, base, song.slug)
    card = page.locator(".readpick")
    card.scroll_into_view_if_needed()
    box = card.bounding_box()
    assert box["width"] <= 390
    for option in card.locator(".readopt").all():
        assert option.bounding_box()["x"] + option.bounding_box()["width"] <= 390
