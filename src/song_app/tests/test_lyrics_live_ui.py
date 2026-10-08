"""Importing lyrics keeps your place and redraws only what changed (#336).

Typing lyrics means checking them on the notes again and again. Import used to
redraw the whole Lyrics panel -- back to the top, the box being typed in gone --
and the cleaned score beside it stayed as it was until the page was reloaded.
What is pinned: the panel keeps its scroll, its boxes and the focus through an
import and through the `state` ping that follows it; the warnings change in place;
the viewer shows the cleaned system with its words under the printed one; and
after an import only the systems whose text changed are fetched again -- plus the
ones after them where that part's box is blank, since a blank box carries the line
on -- with the old picture kept on screen until the new one has arrived.

The import and the pictures are answered with `page.route`, so nothing here needs
MuseScore; the pictures are blank stand-ins, never score music. Screenshots go to
`EVIDENCE_DIR` when the run names one.
"""

import io
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
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


if not _browser_installed():
    pytest.skip(_NEEDS, allow_module_level=True)

import uvicorn
from PIL import Image

from src.song_app import server, state

pytestmark = pytest.mark.browser
SYSTEMS = 8
PARTS = [{"name": "T", "id": 1}, {"name": "B", "id": 2}]
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _png(shade) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 160), (shade, shade, shade)).save(buf, "PNG")
    return buf.getvalue()


PRINTED = _png(235)
OLD = _png(200)
NEW = _png(120)


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("lyrics-live")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Lyrics Song", per_system=False)
    cleaned = song.path("lyrics_cleaned.mscx")
    with open(cleaned, "w") as handle:
        handle.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = os.path.basename(cleaned)
    song.data["stage"] = "lyrics"
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


def _shot(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name))


def _wait_for(page, predicate, timeout=10, what="timed out"):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, what
        page.wait_for_timeout(50)


def _cells():
    """Every box filled except the tenor's in system 7 (index 6), which carries the
    tenor's system-6 line on."""
    return {str(i): {"T": "" if i == 6 else f"la-la lu {i}", "B": f"do do {i}"}
            for i in range(SYSTEMS)}


class Song:
    """What the page is told about the song, and what it asked for."""

    def __init__(self, page, slug):
        self.fingerprint = "fp1"
        self.warnings = []
        self.cleaned = []          # (system, version) of every cleaned picture asked for
        self.hold = False          # hold new cleaned pictures back
        self.held = []
        self.imports = []
        bounds = [{"index": i, "page": 1, "top": (i - 1) / SYSTEMS, "bottom": i / SYSTEMS,
                   "measure_start": 4 * i - 3, "measure_end": 4 * i}
                  for i in range(1, SYSTEMS + 1)]
        grid = {"parts": PARTS,
                "systems": [{"index": i, "start": 4 * i + 1, "end": 4 * i + 4}
                            for i in range(SYSTEMS)],
                "cells": _cells(),
                "capacities": {str(i): {"T": 4, "B": 3} for i in range(SYSTEMS)}}

        def song_json(route):
            res = route.fetch()
            data = res.json()
            data.update(has_pdf=True, systems=bounds, has_cleaned=True,
                        cleaned_fingerprint=self.fingerprint,
                        lyrics={"json": "lyrics.json", "warnings": self.warnings})
            route.fulfill(response=res, body=json.dumps(data))

        def lyrics(route):
            self.imports.append(json.loads(route.request.post_data))
            for si, row in self.imports[-1]["cells"].items():
                grid["cells"].setdefault(si, {}).update(row)
            self.fingerprint = "fp2"
            self.warnings = [{"kind": "too_many", "measure_start": 21, "measure_end": 24,
                              "staff_ids": [1], "syllables": 5, "slots": 4,
                              "message": "Measures 21-24 (staffs 1): too many tokens"
                                         " (5 syllables, 4 slots)"}]
            url = re.sub(r"/lyrics$", "", route.request.url)
            data = route.fetch(url=url, method="GET").json()
            data.update(has_pdf=True, systems=bounds, has_cleaned=True, stage="review",
                        cleaned_fingerprint=self.fingerprint,
                        lyrics={"json": "lyrics.json", "warnings": self.warnings})
            route.fulfill(content_type="application/json", body=json.dumps(data))

        def cleaned(route):
            m = re.search(r"/cleaned-system/(\d+)\?.*v=([^&]*)", route.request.url)
            self.cleaned.append((int(m.group(1)), m.group(2)))
            if self.hold:
                self.held.append(route)
            else:
                route.fulfill(body=OLD if m.group(2) == "fp1" else NEW,
                              content_type="image/png")

        page.route(re.compile(rf"/api/songs/{re.escape(slug)}$"), song_json)
        page.route(re.compile(rf"/api/songs/{re.escape(slug)}/lyrics$"), lyrics)
        page.route(re.compile(r"/lyric-grid$"), lambda route: route.fulfill(
            content_type="application/json", body=json.dumps(grid)))
        page.route(re.compile(r"/bounds$"), lambda route: route.fulfill(
            content_type="application/json", body=json.dumps({"systems": bounds})))
        page.route(re.compile(r"/compare$"), lambda route: route.fulfill(
            content_type="application/json", body=json.dumps({"systems": [
                {"index": b["index"], "measure_start": b["measure_start"],
                 "measure_end": b["measure_end"]} for b in bounds]})))
        page.route(re.compile(r"/api/songs/[^/]+/(system|page)/\d+"),
                   lambda route: route.fulfill(body=PRINTED, content_type="image/png"))
        page.route(re.compile(r"/api/songs/[^/]+/pdf$"),
                   lambda route: route.fulfill(status=404, body="{}"))
        page.route(re.compile(r"/cleaned-system/\d+"), cleaned)

    def after(self, n):
        return sorted({s for s, v in self.cleaned[n:]})


def _open(page, live):
    base, slug = live
    page.add_init_script("localStorage.setItem('lyricMode', 'manual');"
                         "localStorage.removeItem('lyricScore');")
    page.goto(f"{base}/#/song/{slug}")
    page.locator("textarea[data-sys]").first.wait_for()


def _box(page, system, part):
    return page.locator(f"textarea[data-sys='{system}'][data-part='{part}']")


def _import(page):
    page.locator(".floatbar button", has_text="Import lyrics").click()


def _ping(page, live):
    """The `state` message the file watcher sends after the import's own write.
    Sent a few times: the page's socket may still be connecting."""
    for _ in range(3):
        server.hub.emit(live[1], {"type": "state"})
        page.wait_for_timeout(200)


def test_an_import_keeps_the_place_the_box_and_the_focus(live, page):
    page.set_viewport_size(DESKTOP)
    song = Song(page, live[1])
    _open(page, live)
    panel = page.locator(".panel")
    box = _box(page, 5, "T")
    box.scroll_into_view_if_needed()
    box.click()
    box.fill("la-la lu lu-la")
    page.evaluate("() => { window._typing = document.activeElement; }")
    before = panel.evaluate("p => p.scrollTop")
    assert before > 100, "the list should be scrolled well down for this to mean anything"

    _import(page)
    page.locator(".lyerr", has_text="m21–24").wait_for()
    _ping(page, live)

    assert song.imports and song.imports[0]["cells"]["5"]["T"] == "la-la lu lu-la"
    assert panel.evaluate("p => p.scrollTop") == before, "the panel moved"
    assert page.evaluate("() => window._typing.isConnected"), "the box was replaced"
    assert page.evaluate("() => document.activeElement === window._typing"), "focus was lost"
    # The warning sits under the tenor's box of the system it starts in, and only there.
    row = box.locator("xpath=..")
    assert row.locator(".lyerr").count() == 1
    assert page.locator(".lyerr").count() == 1
    _shot(page, "lyrics-import-keeps-place.png")


def test_only_the_changed_systems_are_fetched_again(live, page):
    page.set_viewport_size(DESKTOP)
    song = Song(page, live[1])
    _open(page, live)
    page.locator(".viewtabs .vtab", has_text=re.compile(r"^Compare$")).first.click()
    _wait_for(page, lambda: page.locator(".compare .cmpimg[alt^='cleaned system']").count()
              == SYSTEMS, what="the cleaned systems never all arrived")
    seen = len(song.cleaned)

    song.hold = True
    _box(page, 5, "T").fill("la-la lu lu-la")
    _import(page)
    _wait_for(page, lambda: len(song.held) == 2, what="the changed systems were not fetched")
    page.wait_for_timeout(400)
    # System 6 was typed in; system 7 has the tenor box blank, so the line spills
    # into it. Nothing else moves.
    assert song.after(seen) == [6, 7]
    assert all(v == "fp2" for _, v in song.cleaned[seen:])

    # While MuseScore works the old picture stays, under a note.
    row6 = page.locator(".cmprow").nth(5)
    assert row6.locator(".cmpcleaned img").count() == 1
    assert row6.locator(".liveupd", has_text="Updating").count() == 1
    _shot(page, "compare-updating-one-system.png")
    for route in song.held:
        route.fulfill(body=NEW, content_type="image/png")
    _wait_for(page, lambda: page.locator(".liveupd").count() == 0)

    # A refresh after the import does not reload the rest for the same change.
    _ping(page, live)
    page.wait_for_timeout(300)
    assert song.after(seen) == [6, 7]


def test_one_system_shows_the_words_on_the_notes(live, page):
    page.set_viewport_size(DESKTOP)
    song = Song(page, live[1])
    _open(page, live)
    _box(page, 2, "B").click()
    one = page.locator(".onesystem")
    one.locator(".muted", has_text="Cleaned system 3, with lyrics").wait_for()
    one.locator(".cmpcleaned img").wait_for()
    seen = len(song.cleaned)

    _box(page, 2, "B").fill("do re mi")
    _import(page)
    _wait_for(page, lambda: len(song.cleaned) > seen, what="system 3 was not fetched again")
    page.wait_for_timeout(300)
    assert song.cleaned[seen:] == [(3, "fp2")]
    _wait_for(page, lambda: one.locator(".liveupd").count() == 0)
    _shot(page, "one-system-with-lyrics.png")


def test_a_phone_keeps_its_place_too(live, page):
    page.set_viewport_size(PHONE)
    song = Song(page, live[1])
    _open(page, live)
    panel = page.locator(".panel")
    box = _box(page, 6, "B")
    box.scroll_into_view_if_needed()
    box.click()
    box.fill("do do re")
    before = panel.evaluate("p => p.scrollTop")
    assert before > 100
    _import(page)
    _wait_for(page, lambda: song.imports, what="the import never left")
    page.locator(".lyerr", has_text="m21–24").wait_for()
    _ping(page, live)
    assert panel.evaluate("p => p.scrollTop") == before, "the panel moved"

    page.locator(".mobilebar .mtab").nth(1).click()
    page.locator(".onesystem .muted", has_text="Cleaned system 7, with lyrics").wait_for()
    page.locator(".onesystem .cmpcleaned img").wait_for()
    _shot(page, "phone-one-system-after-import.png")
