"""The Upload panel's Playlists list in a real browser (#338).

YouTube is answered with `page.route`, so nothing here signs in anywhere: the
server side is pinned by test_playlists.py. Screenshots go to `EVIDENCE_DIR` when
the run names one.
"""
import json
import os
import re
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

import uvicorn  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from src.song_app import server, state  # noqa: E402

pytestmark = pytest.mark.browser

PARTS = ["S1", "S2", "A1", "A2", "ALL"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _shot(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name))


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("playlists")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Männyn punerrus", per_system=False)
    song.data["stage"] = "upload"
    song.data["record"] = {"outputs": [f"{song.slug} {p}.mp4" for p in PARTS],
                           "uploads": [{"title": f"Männyn punerrus {p}", "part": p,
                                        "video_id": f"vid{p}", "url": f"https://youtu.be/vid{p}",
                                        "playlist_id": "PLown"} for p in PARTS]}
    song.save()

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                        log_level="warning"))
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


class FakeYouTube:
    """What the server would answer, kept in the test so a tick can be followed."""

    def __init__(self):
        self.rows = [
            {"id": "PLchoir", "title": "Stemmanauhat", "count": 5, "total": 5},
            {"id": "PLwomen", "title": "Naiskuoron stemmanauhat", "count": 0, "total": 5},
            {"id": "PLpublic", "title": "Stemmanauhat yleiseen käyttöön", "count": 3, "total": 5},
        ]
        self.account = [{"id": "PLkazoo", "title": "kazoo", "chosen": False},
                        {"id": "PLtours", "title": "Kuorokonsertti Hanget Soi", "chosen": False}]
        self.posts = []

    def install(self, page, slug):
        def song_lists(route):
            if route.request.method == "GET":
                return route.fulfill(json={"total": 5, "playlists": self.rows})
            body = json.loads(route.request.post_data)
            self.posts.append(body)
            for row in self.rows:
                if row["id"] == body["playlist_id"]:
                    row["count"] = 5 if body["member"] else 0
                    return route.fulfill(json=row)
            return route.fulfill(status=400, json={"detail": "no such playlist"})

        def remember(route):
            if route.request.method != "POST":
                return route.continue_()
            body = json.loads(route.request.post_data)
            self.rows.append({"id": body["playlist_id"], "title": body["title"],
                              "count": 0, "total": 5})
            return route.fulfill(json=[])

        page.route(re.compile(rf"/api/songs/{re.escape(slug)}/playlists$"), song_lists)
        page.route(re.compile(r"/api/youtube-playlists$"),
                   lambda route: route.fulfill(json=self.account))
        page.route(re.compile(r"/api/playlists$"), remember)


def _open_upload(page, base, slug):
    page.goto(f"{base}/#/song/{slug}")  # the song is on Upload, so it opens there
    expect(page.get_by_role("heading", name="Playlists")).to_be_visible()


def _row(page, pid):
    return page.locator(f'.plrow[data-playlist="{pid}"]')


def test_ticks_say_which_playlists_hold_the_song_and_a_tick_adds_it(live, page):
    base, slug = live
    yt = FakeYouTube()
    yt.install(page, slug)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1280, "height": 900})
    _open_upload(page, base, slug)

    choir = _row(page, "PLchoir").locator("input[type=checkbox]")
    women = _row(page, "PLwomen").locator("input[type=checkbox]")
    public = _row(page, "PLpublic").locator("input[type=checkbox]")
    expect(choir).to_be_checked()
    expect(women).not_to_be_checked()
    assert public.evaluate("e => e.indeterminate"), "a partly filled playlist is half ticked"
    expect(_row(page, "PLpublic")).to_contain_text("3 of 5")
    _shot(page, "1-playlists-before.png")

    women.check()
    expect(_row(page, "PLwomen").locator("input[type=checkbox]")).to_be_checked()
    assert yt.posts == [{"playlist_id": "PLwomen", "member": True,
                         "title": "Naiskuoron stemmanauhat"}]

    _row(page, "PLchoir").locator("input[type=checkbox]").uncheck()
    expect(_row(page, "PLchoir").locator("input[type=checkbox]")).not_to_be_checked()
    assert yt.posts[-1]["member"] is False

    page.locator(".playlist-section select").focus()
    expect(page.locator(".playlist-section select option", has_text="kazoo")).to_have_count(1)
    page.locator(".playlist-section select").select_option(label="Kuorokonsertti Hanget Soi")
    expect(_row(page, "PLtours")).to_be_visible()
    _shot(page, "2-playlists-after.png")
    assert not errors, errors


def test_it_fits_a_phone(live, page):
    base, slug = live
    FakeYouTube().install(page, slug)
    page.set_viewport_size({"width": 390, "height": 844})
    _open_upload(page, base, slug)
    expect(_row(page, "PLpublic")).to_contain_text("3 of 5")
    page.get_by_role("heading", name="Playlists").scroll_into_view_if_needed()
    width = page.evaluate("() => document.querySelector('.playlist-section').scrollWidth")
    assert width <= 390
    _shot(page, "3-playlists-phone.png")
