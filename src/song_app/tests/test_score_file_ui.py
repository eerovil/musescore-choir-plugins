"""Downloading and uploading the score, in a real browser.

The routes are pinned in `test_score_file.py`. What only exists here is whether a
person on another machine can actually reach them: the download has to be a link
the browser saves under the score's own name, and the upload has to be a picker
that asks before it overwrites and says what happened afterwards.
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

from src.song_app import server, state
from src.song_app.tests.test_score_file import CLEANED, FIXED, SCORE

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
    with open(song.path(CLEANED), "w", encoding="utf-8") as fh:
        fh.write(SCORE)
    song.data["cleaned"] = CLEANED
    song.data["cleaned_fingerprint"] = state.file_fingerprint(song.cleaned_path())
    song.set_stage("fix")
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
        yield f"http://127.0.0.1:{port}", song, tmp_path
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
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Fix").first.click()
    page.wait_for_selector(".scorefile")
    return errors


def _evidence(page, name):
    """Screenshot only where the run was told to put one; none is committed here."""
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name), full_page=True)


def test_the_score_downloads_under_its_own_name(live, page):
    base, song, _tmp = live
    errors = _open_fix(page, base, song.slug)

    with page.expect_download() as caught:
        page.get_by_text("Download score").click()
    assert caught.value.suggested_filename == CLEANED
    assert not errors, f"the panel raised: {errors}"


def test_a_fixed_score_is_uploaded_from_the_panel_and_the_song_re_checks(live, page):
    base, song, tmp = live
    errors = _open_fix(page, base, song.slug)
    page.on("dialog", lambda d: d.accept())          # it asks before overwriting
    fixed = tmp / "talviuni_cleaned.mscx"
    fixed.write_text(FIXED, encoding="utf-8")

    page.locator(".scorefilepick").set_input_files(str(fixed))

    # The re-check is what redraws the panel, so waiting for the new health row
    # waits for the redraw — and the confirmation has to have survived it.
    page.wait_for_selector("text=fills 1/2")
    assert "Replaced with talviuni_cleaned.mscx" in page.locator(".scorefilestatus").inner_text()
    with open(song.cleaned_path(), encoding="utf-8") as fh:
        assert fh.read() == FIXED                    # the real file, really replaced
    _evidence(page, "issue-216-fix-panel-upload.png")
    assert not errors, f"the panel raised: {errors}"


def test_a_refused_upload_says_so_in_the_panel(live, page):
    base, song, tmp = live
    errors = _open_fix(page, base, song.slug)
    page.on("dialog", lambda d: d.accept())
    wrong = tmp / "talviuni.pdf"
    wrong.write_bytes(b"%PDF-1.4 not a score")

    page.locator(".scorefilepick").set_input_files(str(wrong))

    page.wait_for_selector(".scorefileerr:not(:empty)")
    assert ".mscx or .mscz" in page.locator(".scorefileerr").inner_text()
    with open(song.cleaned_path(), encoding="utf-8") as fh:
        assert fh.read() == SCORE                    # and the score is untouched
    _evidence(page, "issue-216-fix-panel-refused.png")
    assert not errors, f"the panel raised: {errors}"
