"""The Delete button on the Start panel (#428), in a real browser.

The route is pinned in `test_delete_song.py`. What only exists here: the
confirmation names the song and says whether it is on the site, cancelling
leaves it, and accepting takes you back to a library without it.
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


@pytest.fixture
def live(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")
    song = state.create("Talviuni", per_system=False)
    song.data["publish"] = {"sites": {"jm": {"choir": "jm", "prefix": "x/1"}}}
    song.save()
    state.create("Kevät", per_system=False)

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
        yield f"http://127.0.0.1:{port}", song, songs
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _open_start(page, base, slug):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Start").first.click()
    page.wait_for_selector(".deletesong")
    return errors


def _evidence(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name), full_page=True)


def test_cancelling_keeps_the_song(live, page):
    base, song, songs = live
    errors = _open_start(page, base, song.slug)
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    page.locator(".deletesong").click()
    assert asked and "Talviuni" in asked[0] and "jm" in asked[0]
    assert "does not touch the site" in asked[0]
    assert os.path.isdir(songs / "talviuni")
    assert not errors, errors


def test_deleting_goes_back_to_a_library_without_it(live, page):
    base, song, songs = live
    errors = _open_start(page, base, song.slug)
    assert "stays on the site (jm)" in page.locator(".deletebox").inner_text()
    _evidence(page, "issue-428-start-delete.png")
    page.on("dialog", lambda d: d.accept())
    page.locator(".deletesong").click()
    page.wait_for_selector("text=Kevät")
    assert page.locator("text=Talviuni").count() == 0
    assert not os.path.exists(songs / "talviuni")
    assert len(os.listdir(songs / ".trash")) == 1
    assert not errors, errors
