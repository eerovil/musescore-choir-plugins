"""The Scan panel in a real browser.

The Python tests pin what the stage records; this pins what the operator is
actually shown, which is the whole point of the stage. Four things only exist
here: the panel opens on the Systems editor and the Scan button waits for the
bands; a hole is visible with a retry of its own; the OK is a wall that has to be
pressed; and after it lapses the panel says which systems changed.

Nothing here runs homr, poppler or MuseScore. The fragments are written straight
into the song the way a scan would leave them, and `MUSESCORE_CLI_PATH` points at
nothing so the crops and renders 404 -- a missing picture is not what is under
test, and a real render would make this slow and host-dependent.
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

from src.song_app import pdf_systems, scan, server, state

pytestmark = pytest.mark.browser


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("scanpanel")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Scan Panel Song", per_system=True)
    with open(song.path("scan.pdf"), "wb") as fh:
        fh.write(b"%PDF-1.4 not really a pdf\n")
    song.data["sources"]["pdf"] = "scan.pdf"
    song.set_stage("scan")
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


def _bands(song, count):
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=i, page=1,
                                 top=0.1 * i, bottom=0.1 * i + 0.08)
        for i in range(1, count + 1)
    ])


def _read(song, systems, failed=()):
    """Leave the song looking as a scan of `systems` bands would leave it."""
    _bands(song, systems)
    os.makedirs(song.path(scan.FRAGMENT_DIR), exist_ok=True)
    # Real band stamps, or the app discards every fragment on the next read as
    # having been made from geometry that has since moved -- which is the rule
    # working, not a test detail to route around.
    source = pdf_systems.file_version(song.source_path("pdf"))
    stamps = {b.index: scan.band_stamp(b, source)
              for b in pdf_systems.load_bounds(song.dir)}
    fragments = {}
    for index in range(1, systems + 1):
        if index in failed:
            fragments[str(index)] = {"index": index, "band": stamps[index],
                                     "musicxml": None, "staves": 0, "bars": 0,
                                     "error": "homr could not read this band"}
            continue
        name = os.path.join(scan.FRAGMENT_DIR, f"system-{index:02d}.musicxml")
        with open(song.path(name), "w", encoding="utf-8") as fh:
            fh.write("<score-partwise/>")
        fragments[str(index)] = {"index": index, "band": stamps[index],
                                 "musicxml": name, "content": f"read{index}",
                                 "staves": 2, "bars": 4, "error": None}
    song.data["scan"] = {"systems": fragments}
    song.data.pop("review", None)
    song.set_stage("scan")
    if not failed:
        song.data["scan"]["assembled"] = scan.ASSEMBLED_NAME
        song.data["scan"]["assembled_revision"] = scan.revision(song)
    song.save()
    return song


def _reset(song):
    song.data.pop("scan", None)
    pdf_systems.save_bounds(song.dir, [])
    song.set_stage("scan")
    song.save()


def _open(page, base, slug):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".panel h2:text('Scan')")
    # A song at `scan` opens on the Scan panel already, and on a phone the stage
    # rail is behind the pane switcher, so clicking it there is not the way in.
    step = page.locator(".stagebar .step", has_text="Scan").first
    if step.is_visible():
        step.click()
        page.wait_for_selector(".panel h2:text('Scan')")
    return errors


def test_it_opens_on_the_systems_editor_and_waits_for_the_bands(live, page):
    base, song = live
    _reset(state.load(song.slug))
    errors = _open(page, base, song.slug)

    # The bands are drawn by hand and nothing proposes them, so the editor is the
    # thing in front of you rather than a tab to go and find.
    assert page.locator(".vtab.active").first.inner_text() == "Systems"
    assert page.get_by_text("No printed systems marked yet").is_visible()
    assert page.get_by_role("button", name="Scan the score").is_disabled()
    assert not errors, f"the panel raised: {errors}"


def test_a_hole_is_visible_blocking_and_retried_on_its_own(live, page):
    base, song = live
    _read(state.load(song.slug), 3, failed=(2,))
    errors = _open(page, base, song.slug)

    assert page.get_by_text("homr could not read this band").is_visible()
    assert page.get_by_role("button", name="Read system 2 again").is_visible()
    # Nothing whole to approve: the gate is not offered while a system is missing.
    assert page.get_by_role(
        "button", name="This reading is right — continue to Clean").count() == 0
    assert not errors, f"the panel raised: {errors}"


def test_the_ok_is_a_wall_the_operator_opens_by_hand(live, page):
    base, song = live
    _read(state.load(song.slug), 3)
    errors = _open(page, base, song.slug)

    ok = page.get_by_role("button", name="This reading is right — continue to Clean")
    assert ok.is_visible()
    assert state.load(song.slug).stage == "scan", "nothing advanced on its own"

    ok.click()
    page.wait_for_selector(".panel h2:text('Clean')")
    assert state.load(song.slug).stage == "clean"
    assert not errors, f"the panel raised: {errors}"


def test_after_a_re_read_the_ok_lapses_and_the_panel_says_where_to_look(live, page):
    base, song = live
    fresh = _read(state.load(song.slug), 3)
    fresh.data["scan"]["ok"] = {
        "revision": "an-older-reading",
        "systems": {"1": "read1", "2": "something-else", "3": "read3"},
    }
    fresh.save()
    errors = _open(page, base, song.slug)

    assert page.get_by_text("Your OK lapsed").is_visible()
    assert page.get_by_text("system(s) 2").is_visible()
    assert not errors, f"the panel raised: {errors}"


def test_it_fits_a_phone(live, page):
    base, song = live
    _read(state.load(song.slug), 3, failed=(2,))
    page.set_viewport_size({"width": 390, "height": 844})
    errors = _open(page, base, song.slug)

    # Below the breakpoint one pane shows at a time, and the middle tab names the
    # stage, so the panel is reachable and its actions are on screen.
    assert page.locator(".mobilebar").is_visible()
    assert page.get_by_role("button", name="Read system 2 again").is_visible()
    assert page.evaluate("document.body.scrollWidth <= window.innerWidth + 1")
    page.set_viewport_size({"width": 1280, "height": 900})
    assert not errors, f"the panel raised: {errors}"
