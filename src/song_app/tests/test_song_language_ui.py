"""The song's language in a real browser (#484): a select on the New song form, and on the
Start panel, where a change is saved at once. The routes are pinned in test_song_language.py."""
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

from src.sing_song import language_check
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


def _evidence(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name), full_page=True)


def test_the_new_song_form_sends_the_language(live, page, tmp_path):
    base, _, songs = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(base)
    page.get_by_role("button", name="+ New song").click()
    page.wait_for_selector("#f-language option[value=en]", state="attached")
    pdf = tmp_path / "Evening Hymn.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    page.set_input_files("#f-pdf", str(pdf))
    page.locator("#f-voicing").select_option("mixed")
    page.select_option("#f-language", "en")
    _evidence(page, "issue-484-new-song-language.png")
    page.locator("button", has_text="Create").click()
    page.wait_for_selector(".stagebar")
    assert language_check.saved(str(songs / "evening-hymn")) == "en"
    assert not errors, errors


def test_the_start_panel_saves_a_change(live, page):
    base, song, songs = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Start").first.click()
    page.wait_for_selector("#start-language option[value=en]", state="attached")
    assert "Not set" in page.locator(".languagebox").inner_text()
    page.select_option("#start-language", "en")
    page.wait_for_selector(".startlanguage:has-text('Saved.')")
    assert language_check.saved(str(songs / "talviuni")) == "en"
    assert page.locator("#start-language option[value='']").count() == 0
    _evidence(page, "issue-484-start-language.png")
    assert not errors, errors
