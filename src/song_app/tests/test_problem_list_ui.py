"""Every problem in one list, each with its choices, in a real browser (#290).

`test_problems.py` pins the rows and the writes. What only exists in the browser is
what a person meets on the Fix stage: one card per bar and part saying everything
wrong there, homr's readings of an unsure bar offered as whole bars — the likeliest
six first, the second reading always among them, the rest behind "More" (#295) — a
slur the scan ran between two singers offered back as words, one tap each, and all
of it on a phone.
"""
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
from lxml import etree
from playwright.sync_api import expect

from src.clean_score.tests.test_cross_voice_slurs import _score as _slur_score
from src.clean_score.utils.cross_voice_slurs import drop_cross_voice_slurs, store_removed
from src.song_app import pdf_systems, scan, server, state
from src.song_app.tests.test_bar_readings import _fragment, _score
from src.song_app.tests.test_problems import SECOND

pytestmark = pytest.mark.browser


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unsure_song():
    song = state.create("Unsure Song", per_system=False)
    os.makedirs(song.path("scan"))
    first, second = song.path("scan/system-01.musicxml"), song.path("scan/system-02.musicxml")
    with open(first, "w") as fh:
        fh.write(_fragment(None))
    with open(second, "w") as fh:
        fh.write(_fragment(SECOND))
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
    return song


def _slur_song():
    song = state.create("Slur Song", per_system=False)
    root = _slur_score()
    store_removed(root, drop_cross_voice_slurs(root))
    etree.ElementTree(root).write(song.path("song_cleaned.mscx"), encoding="UTF-8")
    song.data["cleaned"] = "song_cleaned.mscx"
    song.data["stage"] = "fix"
    song.save()
    return song


@pytest.fixture
def live(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")
    unsure, slurred = _unsure_song(), _slur_song()

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
        yield f"http://127.0.0.1:{port}", unsure, slurred
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
    page.wait_for_selector(".problem")
    return errors


def _fixes(song):
    path = os.path.join(song.dir, "fixes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def _evidence(page, name, what=".problems"):
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        page.set_viewport_size({"width": 1400, "height": 2000})
        page.locator(what).screenshot(path=os.path.join(out, name))


def test_an_unsure_bar_offers_whole_bars(live, page):
    base, song, _ = live
    errors = _open_fix(page, base, song.slug)
    assert page.locator(".problem").count() == 1
    card = page.locator(".problem")
    assert "m3" in card.inner_text() and "B1" in card.inner_text()
    [pick] = card.locator(".readpick").all()
    assert "Which bar does the page print?" in pick.inner_text()
    # Six shown: the bar as read, homr's second reading, then the likeliest others.
    shown = pick.locator(".readopt:visible")
    assert shown.count() == 6
    assert "as read now" in shown.nth(0).inner_text()
    assert "second reading" in shown.nth(1).inner_text()
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readopt:not([hidden]) .readsvg')]"
        ".every(i => i.complete && i.naturalWidth > 0)")
    _evidence(page, "whole-bars-card.png")

    pick.locator(".readmore").click()
    assert pick.locator(".readopt:visible").count() == 10
    assert pick.locator(".readmore").count() == 0

    pick.locator(".readopt").nth(1).click()
    page.wait_for_selector(".readdone")
    assert "Bar 3, B1: reading b (second reading)" in page.locator(".readdone").inner_text()
    assert page.locator(".problem .readpick").count() == 0
    [entry] = _fixes(song)
    assert entry["kind"] == "bar" and [m["value"] for m in entry["to"]] == ["note_2"]
    _evidence(page, "whole-bar-picked.png")
    assert errors == []


def test_the_card_says_which_bar_staff_and_voice_is_meant(live, page):
    """The crop is a whole printed system; the card names the bar, staff and voice (#310)."""
    base, song, _ = live
    # Where clean_score recorded the parts were printed: B1 alone on the second staff.
    root = etree.parse(song.cleaned_path()).getroot()
    etree.SubElement(root.find("Score"), "metaTag", name="lyricsSystemMap").text = json.dumps(
        [{"start": 1, "end": 5, "map": {"1": [2], "2": [1]}, "source": {"1": [2], "2": [1]},
          "staves": 2}])
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    pdf = os.path.join(os.path.dirname(__file__), "..", "..", "..", "fixtures",
                       "virta-venhetta-vie", "00-registered", "Virta venhettä vie.pdf")
    shutil.copy(pdf, song.path("scan.pdf"))
    song.data["sources"]["pdf"] = "scan.pdf"
    song.save()
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=1, page=1, top=0.10, bottom=0.30,
                                 measure_start=1, measure_end=1),
        pdf_systems.SystemBounds(index=2, page=1, top=0.30, bottom=0.52,
                                 measure_start=2, measure_end=5)])
    page.set_viewport_size({"width": 390, "height": 844})
    errors = _open_fix(page, base, song.slug)
    card = page.locator(".problem")
    assert "system 2 · bar 2/4" in card.locator(".top").inner_text()
    assert card.locator(".barpos").inner_text() == "Bar 2 of 4 · staff 2 of 2, only voice"
    # Said right above the crop it is about.
    # Waited for rather than counted once: the panel can redraw between reads (CI saw 0).
    expect(card.locator(".barpos + .readcrop")).to_have_count(1)
    page.wait_for_function("() => { const i = document.querySelector('.readcrop');"
                           " return i && i.complete && i.naturalWidth > 0; }")
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        card.screenshot(path=os.path.join(out, "bar-in-line-phone.png"))
    assert errors == []


def test_without_bar_ranges_the_card_says_only_the_system(live, page):
    base, song, _ = live
    _open_fix(page, base, song.slug)
    top = page.locator(".problem .top").inner_text()
    assert "system 2" in top and "bar" not in top
    assert page.locator(".barpos").count() == 0


def test_a_removed_slur_is_answered_in_words(live, page):
    base, _, song = live
    errors = _open_fix(page, base, song.slug)
    card = page.locator(".problem")
    assert card.count() == 1
    text = card.inner_text()
    assert "slur to T2 bar 2 removed" in text and "Bar 2, T2: slur from T1 bar 1" in text
    labels = [o.inner_text() for o in card.locator(".readopt").all()]
    assert labels[1].startswith("b") and "Slur in T2 (C4 → C4)" in labels[1]
    assert card.locator(".readnone").count() == 0
    _evidence(page, "removed-slur-card.png")

    card.locator(".readopt").nth(1).click()
    page.wait_for_selector("text=No issues")
    assert "slur answer b (Slur in T2" in page.locator(".readdone").inner_text()
    assert {f["kind"] for f in _fixes(song)} == {"slur", "unmark"}
    _evidence(page, "after-answering.png")
    assert errors == []


def test_it_fits_a_phone(live, page):
    base, song, _ = live
    page.set_viewport_size({"width": 390, "height": 844})
    _open_fix(page, base, song.slug)
    card = page.locator(".problem")
    card.scroll_into_view_if_needed()
    assert card.bounding_box()["width"] <= 390
    for option in card.locator(".readopt:visible").all():
        box = option.bounding_box()
        assert box["x"] + box["width"] <= 390
    page.wait_for_function(
        "() => [...document.querySelectorAll('.readopt:not([hidden]) .readsvg')]"
        ".every(i => i.complete && i.naturalWidth > 0)")
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        card.screenshot(path=os.path.join(out, "phone-card.png"))


def _many_rows(answered=()):
    """Twelve open rows, each with a choice in words; `answered` ones come back decided."""
    rows = []
    for n in range(1, 13):
        rid = f"row-{n}"
        choice = {"id": f"c-{n}", "kind": "slur", "title": f"Is there a slur in bar {n}?",
                  "options": [{"letter": "a", "label": "No slur"},
                              {"letter": "b", "label": "Slur in T1"}],
                  "decision": {"picked": "b"} if rid in answered else None}
        rows.append({"id": rid, "measure": n, "part": "T1", "system": None,
                     "notes": [], "choices": [choice]})
    return rows


@pytest.mark.parametrize("size", [{"width": 1280, "height": 800}, {"width": 390, "height": 844}],
                         ids=["desktop", "phone"])
def test_a_pick_keeps_the_place_in_the_list(live, page, size):
    """A tap redraws the panel; it must not leave the reader at its bottom (#329)."""
    base, song, _ = live
    answered = set()
    page.route("**/problems", lambda route: route.fulfill(
        json={"rows": _many_rows(answered)}))
    song_json = page.request.get(f"{base}/api/songs/{song.slug}").json()

    def pick(route):
        answered.add("row-" + json.loads(route.request.post_data)["choice"].split("-")[1])
        route.fulfill(json=song_json)
    page.route("**/problems/pick", pick)

    page.set_viewport_size(size)
    errors = _open_fix(page, base, song.slug)
    page.wait_for_selector('.problems[data-loaded="1"]')
    if size["width"] < 800:
        assert page.locator(".ws.m-panel").count() == 1
    panel = page.locator(".panel")
    assert panel.evaluate("p => p.scrollHeight > p.clientHeight + 400"), "the list must scroll"

    tapped = page.locator('[data-row="row-5"]')
    panel.evaluate("(p) => { const c = p.querySelector('[data-row=\"row-5\"]');"
                   " p.scrollTop += c.getBoundingClientRect().top - p.getBoundingClientRect().top - 120; }")
    stood = tapped.bounding_box()["y"]
    tapped.locator(".readopt").nth(1).click()
    page.wait_for_selector('[data-row="row-5"]', state="detached")
    page.wait_for_selector('.problems[data-loaded="1"] .readdone')

    at_bottom = panel.evaluate("p => p.scrollTop >= p.scrollHeight - p.clientHeight - 2")
    assert not at_bottom, "the pick threw the panel to its bottom"
    # The next card slid into the place of the one answered.
    assert abs(page.locator('[data-row="row-6"]').bounding_box()["y"] - stood) <= 4
    out = os.environ.get("EVIDENCE_DIR")
    if out and size["width"] < 800:
        page.screenshot(path=os.path.join(out, "after-pick-place-kept-phone.png"))
    assert errors == []
