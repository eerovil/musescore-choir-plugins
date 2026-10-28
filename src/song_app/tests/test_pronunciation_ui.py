"""The song page's Pronunciation list (eeron-omat-taskit#144), in a real browser.

The routes are pinned in `test_pronunciation_api.py`. What only exists here: the list opens
on the Publish stage, shows each place with its line, and a reading chosen at one place is
saved for that place alone.
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

from src.song_app import server, state

pytestmark = pytest.mark.browser


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PLACES = [{"bar": 1, "beat": 1.0, "word": "wind", "n": 1, "parts": ["S1"], "line": "the wind blows be too", "at": 1},
          {"bar": 5, "beat": 0.0, "word": "wind", "n": 1, "parts": ["S1", "B"], "line": "sorrow wind the clock", "at": 1}]
WIND = [{"sounds": "W AY1 N D", "say": "wynd", "as_in": "find"}, {"sounds": "W IH1 N D", "say": "wind", "as_in": "sit"}]


@pytest.fixture
def live(tmp_path, monkeypatch):
    songs = tmp_path / "songs"
    songs.mkdir()
    monkeypatch.setattr(state, "SONGS_DIR", str(songs))
    monkeypatch.setenv("MUSESCORE_CLI_PATH", str(tmp_path / "no-musescore-here"))
    monkeypatch.setattr(server, "_cleaned_or_400", lambda song: "cleaned.mscx")
    monkeypatch.setattr(server.song_places, "places", lambda score: [dict(p) for p in PLACES])
    monkeypatch.setattr(server, "_readings", lambda words: {"wind": WIND})
    song = state.create("Wind test", per_system=False)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    try:
        thread.start()
        deadline = time.time() + 30
        while not srv.started and time.time() < deadline:
            time.sleep(0.05)
        assert srv.started, "the app did not start"
        yield f"http://127.0.0.1:{port}", song, songs
    finally:
        srv.should_exit = True
        thread.join(timeout=10)


def test_a_reading_chosen_at_one_place_is_saved_for_it(live, page):
    base, song, songs = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Publish").first.click()
    page.locator("[data-pronunciation] summary").click()
    page.wait_for_selector(".pron-row")
    rows = page.locator(".pron-row")
    assert rows.count() == 2
    assert "Bar 1" in rows.nth(0).inner_text() and "the wind blows" in rows.nth(0).inner_text()
    assert page.locator(".pron-row strong").first.inner_text() == "wind"
    pick = page.locator('[data-pron-place="1:1:wind"]')
    assert "same as everywhere" in pick.locator("option").first.inner_text()
    assert "as in sit" in pick.inner_text() and "as in find" in pick.inner_text()
    pick.select_option("W IH1 N D")
    with page.expect_response(lambda r: r.url.endswith("/pronunciation") and r.request.method == "POST") as saved_reply:
        page.locator(".pron-save").click()
    assert saved_reply.value.ok
    saved = json.loads((songs / song.slug / "pronounce.json").read_text())
    assert saved == {"places": [{"bar": 1, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D"}], "everywhere": {}}
    assert not errors, errors
