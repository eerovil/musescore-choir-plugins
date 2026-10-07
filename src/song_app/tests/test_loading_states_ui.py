"""The viewer says when something slow is happening (#303), in a real browser.

A score preview is a MuseScore run on the server, and so is every engraved system
in Compare and Scan vs page. Before this the score tab stayed blank while it was
built, and the systems popped in at random as the runs finished. What is pinned:
the building note and its running count, the old score kept under an "Updating…"
badge while a new one is built, a refused build saying why, and the engraved
systems waiting in placeholders and asked for top to bottom, two at a time, with
the one somebody jumped to going next.

The slow routes are held back with `page.route`, so nothing here needs MuseScore;
the pictures are blank stand-ins, never score music. Screenshots go to
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
SYSTEMS = 5
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _blank_pdf() -> bytes:
    """One empty A4 page: enough for pdf.js to draw a canvas."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (n, body))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objects) + 1, xref))
    return out.getvalue()


def _blank_png(shade=235) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 120), (shade, shade, shade)).save(buf, "PNG")
    return buf.getvalue()


PDF = _blank_pdf()
PNG = _blank_png()


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("loading-states")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Loading Song", per_system=False)
    cleaned = song.path("loading_cleaned.mscx")
    with open(cleaned, "w") as handle:
        handle.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = os.path.basename(cleaned)
    song.data["stage"] = "upload"
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


def _dress_song(page, slug, fingerprint=None):
    """Make the song look scanned and split into systems, as far as the page sees.

    Only the song's own JSON is touched; everything else is the real server or a
    route the test holds. `fingerprint` (a list, so a test can change it) stands in
    for a re-clean.
    """
    systems = [{"index": i, "page": 1, "top": (i - 1) / SYSTEMS, "bottom": i / SYSTEMS,
                "measure_start": 4 * i - 3, "measure_end": 4 * i}
               for i in range(1, SYSTEMS + 1)]

    def handle(route):
        res = route.fetch()
        data = res.json()
        data.update(has_pdf=True, systems=systems,
                    scan_status={**(data.get("scan_status") or {}),
                                 "read": SYSTEMS, "systems": SYSTEMS, "errors": {}})
        if fingerprint:
            data["cleaned_fingerprint"] = fingerprint[0]
        route.fulfill(response=res, body=json.dumps(data))

    page.route(re.compile(rf"/api/songs/{re.escape(slug)}$"), handle)
    page.route(re.compile(r"/api/songs/[^/]+/(system|page)/\d+"),
               lambda route: route.fulfill(body=PNG, content_type="image/png"))
    page.route(re.compile(r"/api/songs/[^/]+/pdf$"),
               lambda route: route.fulfill(body=PDF, content_type="application/pdf"))


def _open(page, live, tab):
    base, slug = live
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".viewtabs", state="attached")
    if page.locator(".mobilebar").is_visible():
        page.locator(".mobilebar .mtab").nth(1).click()
    page.locator(".viewtabs .vtab", has_text=re.compile(rf"^{tab}$")).first.click()


def _held(page, pattern):
    """Hold every request matching `pattern` until the test lets it go."""
    held = []
    page.route(pattern, lambda route: held.append(route))
    return held


def _wait_for(page, predicate, timeout=10):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, "timed out"
        page.wait_for_timeout(50)


def test_a_score_being_built_says_so_and_counts(live, page):
    page.set_viewport_size(DESKTOP)
    _dress_song(page, live[1])
    held = _held(page, re.compile(r"/render\?doc=cleaned_nolyrics"))
    _open(page, live, "Cleaned MSCX")

    note = page.locator(".pdfview .busynote", has_text="Building this score with MuseScore")
    note.wait_for()
    page.wait_for_function(
        "() => /\\d+ s$/.test(document.querySelector('.pdfview .busynote')?.textContent || '')",
        timeout=4000)
    _shot(page, "building-desktop.png")

    _wait_for(page, lambda: held)
    for route in held:
        route.fulfill(body=PDF, content_type="application/pdf")
    page.locator(".pdfview canvas.pdfpage").first.wait_for()
    assert note.count() == 0


def test_a_rebuild_keeps_the_old_score_under_an_updating_badge(live, page):
    page.set_viewport_size(DESKTOP)
    fingerprint = ["first"]
    _dress_song(page, live[1], fingerprint)
    held = _held(page, re.compile(r"/render\?doc=cleaned_nolyrics&v=second"))
    page.route(re.compile(r"/render\?doc=cleaned_nolyrics&v=first"),
               lambda route: route.fulfill(body=PDF, content_type="application/pdf"))
    _open(page, live, "Cleaned MSCX")
    page.locator(".pdfview canvas.pdfpage").first.wait_for()

    # A re-clean, as the page hears of it: a new fingerprint and a state ping.
    fingerprint[0] = "second"
    server.hub.emit(live[1], {"type": "state"})
    page.locator(".vupdating", has_text="Updating…").wait_for()
    assert page.locator(".pdfview canvas.pdfpage").count() >= 1, "the old score went blank"
    _shot(page, "updating-desktop.png")

    _wait_for(page, lambda: held)
    for route in held:
        route.fulfill(body=PDF, content_type="application/pdf")
    page.locator(".vupdating").wait_for(state="detached")
    assert page.locator(".pdfview canvas.pdfpage").count() >= 1


def test_a_refused_build_says_why_instead_of_staying_blank(live, page):
    # The real server, with no MuseScore behind it: /render answers 500.
    page.set_viewport_size(DESKTOP)
    _dress_song(page, live[1])
    _open(page, live, "Cleaned MSCX")
    error = page.locator(".pdfview .pdferr", has_text="MuseScore could not build this score:")
    error.wait_for()
    assert len(error.first.text_content()) > len("MuseScore could not build this score: ")
    assert page.locator(".pdfview iframe").count() == 0
    _shot(page, "refused-desktop.png")


@pytest.mark.parametrize("size,label", [(DESKTOP, "desktop"), (PHONE, "phone")])
def test_scan_systems_wait_in_place_and_arrive_top_to_bottom(live, page, size, label):
    page.set_viewport_size(size)
    _dress_song(page, live[1])
    held = _held(page, re.compile(r"/scan-system/\d+"))
    _open(page, live, "Scan vs page")

    slots = page.locator(".cmpslot", has_text="Engraving system")
    _wait_for(page, lambda: slots.count() == SYSTEMS)
    _wait_for(page, lambda: len(held) == 2)
    page.wait_for_timeout(300)
    assert len(held) == 2, "more than two engravings were asked for at once"
    order = [int(re.search(r"/scan-system/(\d+)", r.request.url).group(1)) for r in held]
    assert order == [1, 2]

    # System 5 is the one somebody jumps to: it goes next, ahead of 3 and 4.
    page.evaluate("""() => window.dispatchEvent(
        new CustomEvent('song-system', { detail: { index: 5 } }))""")
    held[0].fulfill(body=PNG, content_type="image/png")
    _wait_for(page, lambda: len(held) == 3)
    page.locator(".cmprow").first.scroll_into_view_if_needed()
    _shot(page, f"scan-vs-page-loading-{label}.png")

    # Finishing out of order still asks in order, two at a time.
    held[2].fulfill(body=PNG, content_type="image/png")
    _wait_for(page, lambda: len(held) == 4)
    held[1].fulfill(body=PNG, content_type="image/png")
    _wait_for(page, lambda: len(held) == 5)
    order = [int(re.search(r"/scan-system/(\d+)", r.request.url).group(1)) for r in held]
    assert order == [1, 2, 5, 3, 4]
    for route in held[3:]:
        route.fulfill(body=PNG, content_type="image/png")
    _wait_for(page, lambda: slots.count() == 0)
    assert page.locator(".cmpimg[alt^='scanned system']").count() == SYSTEMS


def test_a_system_that_cannot_be_engraved_says_so_and_the_rest_still_load(live, page):
    page.set_viewport_size(DESKTOP)
    _dress_song(page, live[1])

    def engrave(route):
        if route.request.url.split("?")[0].endswith("/scan-system/3"):
            route.fulfill(status=500, content_type="application/json",
                          body=json.dumps({"detail": "MuseScore CLI could not engrave it"}))
        else:
            route.fulfill(body=PNG, content_type="image/png")

    page.route(re.compile(r"/scan-system/\d+"), engrave)
    _open(page, live, "Scan vs page")
    failed = page.locator(".cmpslot.err",
                          has_text="Could not engrave system 3: MuseScore CLI could not engrave it")
    failed.wait_for()
    _wait_for(page, lambda: page.locator(".cmpimg[alt^='scanned system']").count() == SYSTEMS - 1)
    failed.scroll_into_view_if_needed()
    _shot(page, "engrave-failed-desktop.png")


def test_compare_says_the_cleaned_score_is_being_built(live, page):
    page.set_viewport_size(DESKTOP)
    _dress_song(page, live[1])
    held = _held(page, re.compile(r"/compare$"))
    cleaned = _held(page, re.compile(r"/cleaned-system/\d+"))
    _open(page, live, "Compare")
    page.locator(".compare .busynote",
                 has_text="Building the cleaned score with MuseScore").wait_for()

    _wait_for(page, lambda: held)
    held[0].fulfill(content_type="application/json", body=json.dumps({"systems": [
        {"index": i, "measure_start": 4 * i - 3, "measure_end": 4 * i}
        for i in range(1, SYSTEMS + 1)]}))
    _wait_for(page, lambda: page.locator(".cmpslot").count() == SYSTEMS)
    _wait_for(page, lambda: len(cleaned) == 2)
    page.wait_for_timeout(300)
    assert len(cleaned) == 2
    for route in list(cleaned):
        route.fulfill(body=PNG, content_type="image/png")
    _wait_for(page, lambda: len(cleaned) == 4)
