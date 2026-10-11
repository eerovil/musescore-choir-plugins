"""The by-system lyric editor in a real browser (#393, #397).

One printed system per screen: the page picture cut just above each staff, the boxes
for the parts printed on that staff under it, verse tabs, a voice-2 box where a part
has second-voice notes, and Previous / Next importing first. It runs the real server on
the Virta fixture (a real scan, its bands and its cleaned score), with one second voice
written into the copy so there is a voice-2 box to find. The import is real; only "Show
what landed" is answered with a stand-in picture, since that is a MuseScore render.
Screenshots go to `EVIDENCE_DIR` when the run names one.
"""

import io
import json
import os
import shutil
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
if not shutil.which("pdftoppm"):
    pytest.skip("needs poppler for the page pictures", allow_module_level=True)

import uvicorn
from lxml import etree
from PIL import Image
from playwright.sync_api import expect

from src.song_app import server, state

pytestmark = pytest.mark.browser

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "fixtures", "virta-venhetta-vie")
CLEANED = "Virta-venhetta-vie_cleaned.mscx"
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 390, "height": 844}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("lyric-editor")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")
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
        yield f"http://127.0.0.1:{port}"
    finally:
        running.should_exit = True
        thread.join(timeout=10)
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _add_voice2(path, staff_id, measures):
    """Give one staff a second voice of two quarters in each named bar."""
    tree = etree.parse(path)
    staff = [s for s in tree.getroot().find("Score").findall("Staff")
             if s.get("id") == str(staff_id)][0]
    for m in measures:
        bar = staff.findall("Measure")[m - 1]
        voice = etree.Element("voice")
        for _ in range(2):
            voice.append(etree.fromstring(
                "<Chord><durationType>quarter</durationType>"
                "<Note><pitch>43</pitch><tpc>15</tpc></Note></Chord>"))
        bar.findall("voice")[-1].addnext(voice)
    tree.write(path, encoding="utf-8", xml_declaration=True)


@pytest.fixture
def song(live):
    """A fresh copy of the Virta fixture on the Lyrics stage, B2 singing a second
    voice in bars 4-5 (printed system 2)."""
    slug = "virta-venhetta-vie"
    dest = os.path.join(state.SONGS_DIR, slug)
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    for stage in ("00-registered", "10-cleaned"):
        src = os.path.join(FIXTURE, stage)
        for name in os.listdir(src):
            shutil.copyfile(os.path.join(src, name), os.path.join(dest, name))
    _add_voice2(os.path.join(dest, CLEANED), 4, (4, 5))
    with open(os.path.join(dest, ".song.json"), encoding="utf-8") as f:
        data = json.load(f)
    data.update(cleaned=CLEANED, stage="lyrics")
    with open(os.path.join(dest, ".song.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    yield slug, dest
    shutil.rmtree(dest, ignore_errors=True)


def _shot(page, name):
    where = os.environ.get("EVIDENCE_DIR")
    if where:
        os.makedirs(where, exist_ok=True)
        page.screenshot(path=os.path.join(where, name))


def _open(page, live, slug, size=DESKTOP):
    page.set_viewport_size(size)
    page.add_init_script("localStorage.setItem('lyricMode', 'manual')")
    page.goto(f"{live}/#/song/{slug}")
    expect(page.locator(".lyedwhere")).to_contain_text("System 1 of 15", timeout=30_000)


def _box(page, key):
    return page.locator(f'textarea[data-box="{key}"]')


def _grid(page, live, slug):
    return page.request.get(f"{live}/api/songs/{slug}/lyric-grid").json()


def _text(grid, system, key, verse=1):
    return [b for b in grid["boxes"][str(system)] if b["key"] == key][0]["text"].get(str(verse), "")


def _pictures_loaded(page):
    page.wait_for_function(
        "() => { const i = [...document.querySelectorAll('.lyedslice img, .lyedpic img')];"
        "        return i.length && i.every(x => x.complete && x.naturalWidth > 100); }",
        timeout=60_000)


def test_one_printed_system_with_each_staffs_boxes_under_it(live, song, page):
    slug, _ = song
    _open(page, live, slug)
    expect(page.locator(".lyedwhere")).to_have_text("System 1 of 15 · bars 1–3")
    expect(page.locator(".lyedslice")).to_have_count(2, timeout=30_000)
    _pictures_loaded(page)
    # Top staff, its two tenors, bottom staff, its two basses: in that order.
    order = page.locator(".lyedbody > *").evaluate_all(
        "els => els.map(e => e.classList.contains('lyedslice') ? 'staff'"
        "  : e.querySelector('textarea')?.dataset.box)")
    assert order == ["staff", "1", "2", "staff", "3", "4"]
    expect(page.locator(".viewer")).to_be_hidden()
    expect(page.locator(".lyedtabs .vtab", has_text="Verse 1")).to_have_class("vtab active")
    # The second staff's slice starts with the second staff, not in the middle of it.
    tops = page.locator(".lyedslice").evaluate_all("els => els.map(e => e.getBoundingClientRect().height)")
    assert all(h > 60 for h in tops)
    _shot(page, "editor-desktop.png")


def test_next_imports_first_and_a_warning_keeps_you_on_the_system(live, song, page):
    slug, _ = song
    _open(page, live, slug)
    _box(page, "1").fill("Vir-ta")                    # 2 syllables for 11 notes
    expect(page.locator(".lyedstatus")).to_contain_text("Not imported — T1")
    page.get_by_role("button", name="Next →").click()
    expect(page.locator(".lyedstatus")).to_contain_text("warning", timeout=30_000)
    expect(page.locator(".lyedwhere")).to_contain_text("System 1 of 15")
    warning = page.locator(".lyrow", has=_box(page, "1")).locator(".lyerr")
    expect(warning).to_contain_text("too few")
    expect(page.locator(".lyrow", has=_box(page, "2")).locator(".lyerr")).to_have_count(0)
    # The box now shows what landed: the two syllables and a `_` for each note left.
    assert _box(page, "1").input_value().split() == ["Vir-ta"] + ["_"] * 9
    page.get_by_role("button", name="Next →").click()
    expect(page.locator(".lyedwhere")).to_contain_text("System 2 of 15")
    assert _text(_grid(page, live, slug), 0, "1").startswith("Vir-ta")


def test_a_clean_import_moves_straight_on_and_previous_comes_back(live, song, page):
    slug, _ = song
    _open(page, live, slug)
    _box(page, "4").fill(" ".join(["la"] * 5))      # B2 has five notes in bars 1-3
    page.get_by_role("button", name="Next →").click()
    expect(page.locator(".lyedwhere")).to_contain_text("System 2 of 15", timeout=30_000)
    page.get_by_role("button", name="← Previous").click()
    expect(page.locator(".lyedwhere")).to_contain_text("System 1 of 15")
    expect(_box(page, "4")).to_have_value("la la la la la")


def test_verse_tabs_keep_the_system_and_import_only_their_verse(live, song, page):
    slug, _ = song
    _open(page, live, slug)
    _box(page, "1").fill(" ".join(["yy"] * 11))
    page.get_by_role("button", name="Next →").click()
    expect(page.locator(".lyedwhere")).to_contain_text("System 2 of 15", timeout=30_000)
    page.get_by_role("button", name="+ verse").click()
    expect(page.locator(".lyedtabs .vtab.active")).to_have_text("Verse 2")
    expect(page.locator(".lyedwhere")).to_contain_text("System 2 of 15")
    expect(_box(page, "3")).to_have_value("")
    slots = int(page.locator(".lyrow", has=_box(page, "3")).locator("label").inner_text()
                .split("·")[1].split()[0])
    _box(page, "3").fill(" ".join(["kaa"] * slots))
    page.get_by_role("button", name="Import").click()
    expect(page.locator(".lyedstatus")).to_have_text("Imported.", timeout=30_000)
    grid = _grid(page, live, slug)
    assert grid["verses"] == [1, 2]
    assert _text(grid, 1, "3", verse=2) == " ".join(["kaa"] * slots)
    assert _text(grid, 1, "3", verse=1) == ""
    assert _text(grid, 0, "1") == " ".join(["yy"] * 11)
    page.locator(".lyedtabs .vtab", has_text="Verse 1").click()
    expect(page.locator(".lyedwhere")).to_contain_text("System 2 of 15")
    _shot(page, "editor-verse-2.png")


def test_a_voice_2_box_only_where_that_part_has_second_voice_notes(live, song, page):
    slug, dest = song
    _open(page, live, slug)
    expect(_box(page, "4@2")).to_have_count(0)
    page.get_by_role("button", name="Next →").click()
    expect(page.locator(".lyedwhere")).to_contain_text("System 2 of 15")
    row = page.locator(".lyrow.v2")
    expect(row.locator("label")).to_have_text("B2 · voice 2 · 4 slots")
    # Right under the B2 box, on the bottom staff.
    order = page.locator(".lyedbody > *").evaluate_all(
        "els => els.map(e => e.classList.contains('lyedslice') ? 'staff'"
        "  : e.querySelector('textarea')?.dataset.box)")
    assert order[-3:] == ["3", "4", "4@2"]
    _box(page, "4@2").fill("Ke-vät ke-vät")
    page.get_by_role("button", name="Import").click()
    expect(page.locator(".lyedstatus")).to_have_text("Imported.", timeout=30_000)
    assert _text(_grid(page, live, slug), 1, "4@2") == "Ke-vät ke-vät"
    row.scroll_into_view_if_needed()
    _shot(page, "editor-voice-2.png")


def test_emptying_a_box_and_importing_takes_its_words_out(live, song, page):
    slug, _ = song
    _open(page, live, slug)
    _box(page, "2").fill(" ".join(["oo"] * 13))
    page.get_by_role("button", name="Import").click()
    expect(page.locator(".lyedstatus")).to_have_text("Imported.", timeout=30_000)
    assert _text(_grid(page, live, slug), 0, "2")
    _box(page, "2").fill("")
    page.get_by_role("button", name="Import").click()
    expect(page.locator(".lyedstatus")).to_have_text("Imported.", timeout=30_000)
    assert _text(_grid(page, live, slug), 0, "2") == ""


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 200), (200, 230, 200)).save(buf, "PNG")
    return buf.getvalue()


def test_show_what_landed_swaps_the_page_for_the_bars_as_they_are(live, song, page):
    slug, _ = song
    asked = []

    def landed(route):
        asked.append(route.request.url)
        route.fulfill(status=200, content_type="image/png", body=_png())

    page.route("**/landed/**", landed)
    _open(page, live, slug)
    page.get_by_role("button", name="Show what landed").click()
    expect(page.locator(".lyedpic.landed img")).to_be_visible(timeout=30_000)
    expect(page.locator(".lyedslice")).to_have_count(0)
    assert "/landed/0?" in asked[-1]
    page.get_by_role("button", name="Show the page").click()
    expect(page.locator(".lyedslice")).to_have_count(2, timeout=30_000)


def test_a_system_whose_staves_do_not_match_lists_its_boxes(live, song, page):
    slug, _ = song
    page.route("**/lyric-staves/**", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"crop": 1, "aspect": 4, "rows": [{"top": 0, "bottom": 1}],
                         "printed": None})))
    _open(page, live, slug)
    expect(page.locator(".lyedbody .hint")).to_contain_text("could not be matched", timeout=30_000)
    expect(page.locator(".lyedslice")).to_have_count(0)
    expect(page.locator(".lyedpic img")).to_have_count(1)
    expect(page.locator(".lyedbody textarea")).to_have_count(4)


def test_it_works_on_a_phone(live, song, page):
    slug, _ = song
    _open(page, live, slug, size=PHONE)
    expect(page.locator(".lyedslice")).to_have_count(2, timeout=30_000)
    _pictures_loaded(page)
    wide = page.evaluate(
        "() => [...document.querySelectorAll('.panel *')]"
        "  .filter(e => e.getBoundingClientRect().right > window.innerWidth + 1)"
        "  .map(e => e.className || e.tagName)")
    assert wide == [], wide
    expect(page.get_by_role("button", name="Next →")).to_be_visible()
    expect(page.locator(".mobilebar")).to_be_visible()
    _shot(page, "editor-phone.png")


def test_a_line_running_on_refreshes_the_next_systems_picture_too(live, song, page):
    """Thirteen syllables for T1's eleven notes run into system 2, whose box is empty:
    both systems' cleaned pictures are marked as changed, not only the one typed in."""
    slug, _ = song
    _open(page, live, slug)
    _box(page, "1").fill(" ".join(["la"] * 13))
    page.get_by_role("button", name="Import").click()
    expect(page.locator(".lyedstatus")).to_contain_text("Imported", timeout=30_000)
    changed = page.evaluate("() => Object.keys(sysVer.by).map(Number).sort((a, b) => a - b)")
    assert changed == [1, 2], changed
