"""The Record panel in a real browser: the choice of renderer, and what it posts.

The Python tests cover the routing; this covers the half that only exists in the
browser — that the panel offers both renderers, starts on the scrolling one, and
sends the option the server branches on.
"""
import json
import os
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("uvicorn")

import uvicorn
from playwright.sync_api import sync_playwright

from src.song_app import server, state

pytestmark = pytest.mark.browser


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("recordpanel")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Panel Song", per_system=False)
    with open(song.path("panel_cleaned.mscx"), "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = "panel_cleaned.mscx"
    song.data["stage"] = "record"
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
        yield f"http://127.0.0.1:{port}", song.slug
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


@pytest.fixture
def page(live):
    base, slug = live
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/#/song/{slug}")
        page.wait_for_selector(".stagebar")
        page.locator(".stagebar .step", has_text="Record").click()
        page.wait_for_selector("text=How to make the videos")
        yield page, slug, errors
        assert not errors, f"the panel raised: {errors}"
        browser.close()


def test_the_panel_offers_both_renderers_and_starts_on_the_scrolling_one(page):
    view, _, _ = page
    assert view.get_by_text("Scrolling score", exact=True).is_visible()
    assert view.get_by_text("Screen recording", exact=True).is_visible()
    radios = view.locator("input[type=radio][name=renderer]")
    assert radios.nth(0).is_checked(), "the scrolling renderer should be the default"
    assert not radios.nth(1).is_checked()
    assert view.get_by_role("button", name="Render videos").is_visible()


def test_choosing_the_screen_recorder_swaps_the_controls(page):
    view, _, _ = page
    size = view.locator("select")           # the size choice, scrolling renderer only
    assert size.is_visible() and size.input_value() == "4k"
    assert not view.get_by_text("Audio sync offset (ms)").is_visible()

    view.get_by_text("Screen recording", exact=True).click()
    assert view.get_by_role("button", name="Run recording").is_visible()
    assert view.get_by_text("Audio sync offset (ms)").is_visible()
    assert not size.is_visible(), "the size choice does not apply to screen recording"


def test_the_run_button_posts_the_chosen_renderer(page):
    """What the server branches on has to actually leave the browser."""
    view, slug, _ = page
    sent = []
    view.route(f"**/api/songs/{slug}/record", lambda route: (
        sent.append(json.loads(route.request.post_data or "{}")),
        route.fulfill(status=200, content_type="application/json",
                      body='{"started": true}')))

    view.get_by_role("button", name="Render videos").click()
    view.wait_for_timeout(300)
    assert sent and sent[0]["renderer"] == "scroll"
    assert sent[0]["quality"] == "4k"
