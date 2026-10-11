"""Reload behavior for the workspace in a real browser."""

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
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


if not _browser_installed():
    pytest.skip(_NEEDS, allow_module_level=True)

import uvicorn

from src.song_app import server, state

pytestmark = pytest.mark.browser
PHONE = {"width": 390, "height": 844}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rendering-improvements")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Reload Song", per_system=False)
    cleaned = song.path("reload_cleaned.mscx")
    with open(cleaned, "w") as handle:
        handle.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = os.path.basename(cleaned)
    song.data["stage"] = "review"
    song.save()

    port = _free_port()
    running = uvicorn.Server(uvicorn.Config(
        server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    try:
        thread.start()
        deadline = time.time() + 30
        while not running.started and time.time() < deadline:
            time.sleep(0.05)
        assert running.started, "the app did not start"
        yield f"http://127.0.0.1:{port}", song.slug
    finally:
        running.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _choose_stage(page, label):
    stages = page.locator("#stagemenu")
    if stages.is_visible():
        stages.click()
    page.locator(".stagebar .step", has_text=label).click()


def _open_lyrics(page, live):
    base, slug = live
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar", state="attached")
    # Make this a deliberate selection so the companion script stores it.
    _choose_stage(page, "Review")
    _choose_stage(page, "Lyrics")
    return slug


def test_workflow_mobile_pane_and_viewer_tab_survive_a_reload(live, page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size(PHONE)
    slug = _open_lyrics(page, live)

    page.locator(".mobilebar").get_by_role("button", name="Score").click()
    cleaned_tab = page.locator(".viewtabs .vtab", has_text="Cleaned MSCX").first
    cleaned_tab.click()
    assert cleaned_tab.evaluate("node => node.classList.contains('active')")

    song = state.load(slug)
    song.data["stage"] = "publish"
    song.save()
    try:
        page.reload()
        page.wait_for_function("""
            () => [...document.querySelectorAll('.stagebar .step')]
              .some(step => step.classList.contains('active') && step.textContent.trim() === 'Lyrics')
        """)
        page.wait_for_function("""
            () => [...document.querySelectorAll('.viewtabs .vtab.active')]
              .some(tab => tab.textContent.trim() === 'Cleaned MSCX')
        """)
        page.wait_for_function("""
            () => [...document.querySelectorAll('.mobilebar .mtab')]
              .some(tab => tab.classList.contains('active') && tab.textContent.trim() === 'Score')
        """)
        assert not errors, f"the reloaded workspace raised: {errors}"
    finally:
        song = state.load(slug)
        song.data["stage"] = "review"
        song.save()
