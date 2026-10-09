"""Picking between homr's readings of an unsure bar, in a real browser (#269).

`test_bar_readings.py` pins the matching and the write. What only exists in the
browser is the half a person meets: the bar's options drawn under the page, one tap
to pick, and the bar showing as decided afterwards.
"""
import json
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

from src.song_app import scan, server, state
from src.song_app.tests.test_bar_readings import READINGS, _fragment, _score

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
    # No score render: the system crop is a nicety, and MuseScore is not under test.
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")

    song = state.create("Reading Panel Song", per_system=False)
    os.makedirs(song.path("scan"))
    first, second = song.path("scan/system-01.musicxml"), song.path("scan/system-02.musicxml")
    with open(first, "w") as fh:
        fh.write(_fragment(None))
    with open(second, "w") as fh:
        fh.write(_fragment(READINGS))
    song.data["scan"] = {"systems": {
        "1": {"index": 1, "musicxml": "scan/system-01.musicxml",
              "content": scan.content_stamp(first), "bars": 1, "error": None},
        "2": {"index": 2, "musicxml": "scan/system-02.musicxml",
              "content": scan.content_stamp(second), "bars": 2, "error": None}}}
    with open(song.path("song_cleaned.mscx"), "w", encoding="utf-8") as fh:
        fh.write(_score(((1, "B1", 0),)))
    song.data["cleaned"] = "song_cleaned.mscx"
    song.data["stage"] = "fix"
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


def _open_fix(page, base, slug):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # The song sits at Fix, so that is the panel it opens on, at any width.
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".readpick")
    return errors


def _fixes(song):
    path = os.path.join(song.dir, "fixes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def test_the_options_are_drawn_and_one_tap_picks(live, page):
    base, song = live
    errors = _open_fix(page, base, song.slug)
    row = page.locator(".problem")
    assert "m3" in row.inner_text() and "B1" in row.inner_text()
    card = page.locator(".readpick")
    assert card.locator(".readopt").count() == 3
    assert "as read now" in card.locator(".readopt").first.inner_text()
    # Each option really is drawn, not a broken image.
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readsvg')].every(i => i.complete && i.naturalWidth > 0)")

    card.locator(".readopt").nth(1).click()
    page.wait_for_selector(".readdone")
    assert "Bar 3, B1: reading b" in page.locator(".readdone").inner_text()
    assert page.locator(".readpick").count() == 0
    [entry] = _fixes(song)
    assert entry["kind"] == "bar" and [m["value"] for m in entry["to"]] == ["note_4.", "note_8"]
    assert errors == []


def test_none_of_these_is_said_as_decided(live, page):
    base, song = live
    _open_fix(page, base, song.slug)
    page.locator(".readnone").click()
    page.wait_for_selector(".readdone")
    assert "none of these" in page.locator(".readdone").inner_text()
    assert _fixes(song) == []


def test_it_fits_a_phone(live, page):
    base, song = live
    page.set_viewport_size({"width": 390, "height": 844})
    _open_fix(page, base, song.slug)
    card = page.locator(".readpick")
    card.scroll_into_view_if_needed()
    box = card.bounding_box()
    assert box["width"] <= 390
    for option in card.locator(".readopt").all():
        assert option.bounding_box()["x"] + option.bounding_box()["width"] <= 390


def _drawn_pdf(path):
    """A page with two drawn systems of one staff: one bar, then two.

    Drawn rather than taken from a song, so a screenshot of the card shows no
    printed music anybody owns.
    """
    from PIL import Image, ImageDraw

    page = Image.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(page)
    space = 16
    for top, bars in ((300, 1), (700, 2)):
        left, right = 120, 1140
        for line in range(5):
            draw.line([(left, top + line * space), (right, top + line * space)], fill=0, width=2)
        draw.line([(left, top), (left, top + 4 * space)], fill=0, width=4)
        for k in range(1, bars + 1):
            x = left + (right - left) * k // bars
            draw.line([(x, top), (x, top + 4 * space)], fill=0, width=3)
            for n in range(4):  # noteheads with stems, away from the barlines
                cx = left + (right - left) * (k - 1) // bars + 80 + n * 100
                cy = top + space * (1 + n % 3)
                draw.ellipse([cx - 10, cy - 7, cx + 10, cy + 7], fill=0)
                draw.line([(cx + 9, cy), (cx + 9, cy - 3 * space)], fill=0, width=2)
    page.save(path, resolution=150)


def test_the_choice_says_which_part_it_would_give_the_line_to(live, page):
    """#368: the card boxes the bar on the page, says whose line an option is, and
    warns about what fixes.json already does to the bar."""
    from lxml import etree

    from src.song_app import pdf_systems
    from src.song_app.tests.test_bar_readings import _score

    base, song = live
    # B1 and B2 printed on one staff; B2 holds reading b's notes (C3 dotted quarter, D3
    # eighth) and has a slur recorded in this bar.
    with open(song.path("song_cleaned.mscx"), "w", encoding="utf-8") as fh:
        fh.write(_score(((1, "B1", 0), (2, "B2", 0))))
    root = etree.parse(song.cleaned_path()).getroot()
    voice = root.find(".//Score/Staff[@id='2']").findall("Measure")[2].find("voice")
    chords = voice.findall("Chord")
    etree.SubElement(chords[0], "dots").text = "1"
    chords[1].find("durationType").text = "eighth"
    etree.SubElement(root.find("Score"), "metaTag", name="lyricsSystemMap").text = json.dumps(
        [{"start": 1, "end": 1, "map": {"1": [1, 2]}, "source": {"1": [1, 2]}, "staves": 1},
         {"start": 2, "end": 3, "map": {"1": [1, 2]}, "source": {"1": [1, 2]}, "staves": 1}])
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    with open(song.path("fixes.json"), "w", encoding="utf-8") as fh:
        json.dump([{"kind": "slur", "staff": 2, "measure": 3, "index": 0, "span": 1,
                    "why": "the page prints a slur over B2's two notes"}], fh)
    _drawn_pdf(song.path("drawn.pdf"))
    song.data["sources"]["pdf"] = "drawn.pdf"
    song.save()
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=1, page=1, top=0.10, bottom=0.30,
                                 measure_start=1, measure_end=1),
        pdf_systems.SystemBounds(index=2, page=1, top=0.33, bottom=0.53,
                                 measure_start=2, measure_end=3)])

    errors = _open_fix(page, base, song.slug)
    card = page.locator(".problem")
    assert card.locator(".barpos").inner_text() == "B1: Bar 2 of 2 · staff 1 of 1, upper voice"
    pick = card.locator(".readpick")
    assert "Bar 3, B1: which notes does the page print for B1?" in pick.inner_text()
    warn = pick.locator(".readwarn")
    assert "fixes.json already changes bar 3" in warn.inner_text()
    assert "B2 (slur): the page prints a slur over B2's two notes" in warn.inner_text()
    assert "the line B2 has now — gives it to B1" in pick.locator(".readopt").nth(1).inner_text()
    assert pick.locator(".readswap").count() == 1
    # The box sits on the second bar of the drawn system, which is its right half.
    mark = card.locator(".cropmark")
    mark.wait_for()
    assert "staffonly" not in (mark.get_attribute("class") or "")
    crop, box = card.locator(".readcrop").bounding_box(), mark.bounding_box()
    middle = crop["x"] + crop["width"] * (120 + 1020 / 2) / 1240
    assert abs(box["x"] - middle) < crop["width"] * 0.02
    assert box["x"] + box["width"] <= crop["x"] + crop["width"]
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readsvg, .readcrop')]"
        ".every(i => i.complete && i.naturalWidth > 0)")
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        card.screenshot(path=os.path.join(out, "reading-choice-in-context.png"))
    assert errors == []


def test_a_bar_fixes_json_already_wrote_is_not_offered(live, page):
    base, song = live
    with open(song.path("fixes.json"), "w", encoding="utf-8") as fh:
        json.dump([{"kind": "bar", "staff": 1, "measure": 3, "from": [], "to": [],
                    "why": "m3: voices swapped, read against the page"}], fh)
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".readdone")
    assert page.locator(".readpick").count() == 0
    assert ("Bar 3, B1: answered by fixes.json (bar) — m3: voices swapped, read against "
            "the page") in page.locator(".readdone").inner_text()
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.locator(".problems").screenshot(path=os.path.join(out, "answered-by-fixes.png"))
