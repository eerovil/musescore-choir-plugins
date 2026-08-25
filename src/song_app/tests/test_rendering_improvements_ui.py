"""Reload behavior for the workspace and completed renders in a real browser."""

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

from src.song_app import job_state, server, state

pytestmark = pytest.mark.browser


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
    song.data["stage"] = "record"
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


def _open_record(page, live):
    base, slug = live
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    # Make this a deliberate selection so the companion script stores it.
    page.locator(".stagebar .step", has_text="Review").click()
    page.locator(".stagebar .step", has_text="Record").click()
    page.wait_for_selector("text=How to make the videos")
    return slug


def test_workflow_and_viewer_tabs_survive_a_reload(live, page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    slug = _open_record(page, live)

    cleaned_tab = page.locator(".viewtabs .vtab", has_text="Cleaned MSCX").first
    cleaned_tab.click()
    assert cleaned_tab.evaluate("node => node.classList.contains('active')")

    song = state.load(slug)
    song.data["stage"] = "upload"
    song.save()
    try:
        page.reload()
        page.wait_for_selector("text=How to make the videos")
        page.wait_for_function("""
            () => [...document.querySelectorAll('.stagebar .step')]
              .some(step => step.classList.contains('active') && step.textContent.trim() === 'Record')
        """)
        page.wait_for_function("""
            () => [...document.querySelectorAll('.viewtabs .vtab.active')]
              .some(tab => tab.textContent.trim() === 'Cleaned MSCX')
        """)
        assert not errors, f"the reloaded workspace raised: {errors}"
    finally:
        song = state.load(slug)
        song.data["stage"] = "record"
        song.save()


def test_finished_render_appears_without_a_websocket_state_message(live, page):
    """The polling fallback must notice completion on a suspended/disconnected phone."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    slug = _open_record(page, live)
    song = state.load(slug)
    lock = server._lock_path(song)
    video = song.path("media", "video", f"{slug} S1.mp4")

    try:
        job_state.start(song.dir, "render", state.file_fingerprint(song.cleaned_path()))
        with open(lock, "w") as handle:
            handle.write(str(os.getpid()))

        page.reload()
        page.wait_for_selector("text=Rendering… leave this running.")
        # Let rendering_state.js observe the running baseline before completing it.
        page.wait_for_timeout(1200)

        os.makedirs(os.path.dirname(video), exist_ok=True)
        with open(video, "wb") as handle:
            handle.write(b"new take")
        song = state.load(slug)
        song.data["record"] = {
            "renderer": "scroll",
            "outputs": [os.path.basename(video)],
            "rendered_against": state.file_fingerprint(song.cleaned_path()),
        }
        song.data["stage"] = "upload"
        song.save()
        job_state.finish(song.dir, "render")
        os.remove(lock)
        # Deliberately do not call server.hub.emit(..., {"type": "state"}).

        page.wait_for_selector(".result video", timeout=10000)
        assert page.locator(".result .rlabel").first.inner_text() == "S1"
        source = page.locator(".result video").first.get_attribute("src")
        assert "completed=" in source, source
        active = page.locator(".stagebar .step.active").inner_text()
        assert active == "Record"
        assert not errors, f"the completion refresh raised: {errors}"
    finally:
        if os.path.exists(lock):
            os.remove(lock)
        if os.path.exists(video):
            os.remove(video)
        song = state.load(slug)
        song.data["record"] = {}
        song.data["stage"] = "record"
        song.save()
        if job_state.is_running(song.dir, "render"):
            job_state.finish(song.dir, "render", error="test cleanup")
