"""The Clean panel names the bars whose voices run past the barline (#481).

MuseScore 4 will not open such a scan, so the person should see which bars before
cleaning. The list comes from the `/systems` reply, read off the converted file, so
it is there on the first view and again after a reload that reuses the conversion.
"""
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
from playwright.sync_api import expect

from src.clean_score.tests.test_overlong_voices import FULL, OVERLAPPING, _score
from src.song_app import server, state

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
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")

    song = state.create("Talviuni", per_system=True, stage="clean")
    source = songs / song.slug / "in.musicxml"
    source.write_text("<score-partwise/>")
    os.utime(source, (1, 1))
    # The conversion MuseScore 4 with -f leaves: bar 2's voice 2 runs long.
    (songs / song.slug / "in.mscx").write_bytes(
        etree.tostring(_score([FULL, FULL], [FULL, OVERLAPPING])))
    song.data.setdefault("sources", {})["xml"] = "in.musicxml"
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


def test_the_clean_panel_lists_the_long_bars_on_first_view_and_after_a_reload(live, page):
    base, song = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{song.slug}")
    for _ in range(2):
        expect(page.locator(".sysblock")).to_have_count(1)
        listed = page.locator(".overlong li")
        expect(listed).to_have_count(1)
        expect(listed).to_contain_text("measure 2, staff 1, voice 2")
        page.reload()
    assert not errors, f"the panel raised: {errors}"
