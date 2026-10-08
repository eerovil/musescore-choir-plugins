"""Does a repeat have 1. and 2. brackets, asked in a real browser (#319).

`test_volta_question.py` pins the question and the writes. This is the Fix card a
person meets: an end repeat with no brackets, each bracket length as words, one tap
writing both brackets, and all of it on a phone.
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

from src.song_app import pdf_systems, server, state
from src.clean_score.utils import score_fixes
from src.song_app.tests.test_volta_question import _score

pytestmark = pytest.mark.browser

PDF = os.path.join(os.path.dirname(__file__), "..", "..", "..", "fixtures",
                   "virta-venhetta-vie", "00-registered", "Virta venhettä vie.pdf")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _volta_song():
    song = state.create("Volta Song", per_system=False)
    with open(song.path("song_cleaned.mscx"), "w", encoding="utf-8") as fh:
        fh.write(_score(bars=8, ends=(6,), starts=(3,)))
    song.data["cleaned"] = "song_cleaned.mscx"
    song.data["stage"] = "fix"
    shutil.copy(PDF, song.path("scan.pdf"))
    song.data.setdefault("sources", {})["pdf"] = "scan.pdf"
    song.save()
    pdf_systems.save_bounds(song.dir, [
        pdf_systems.SystemBounds(index=i, page=1, top=0.08 + 0.21 * (i - 1),
                                 bottom=0.08 + 0.21 * i, measure_start=2 * i - 1,
                                 measure_end=2 * i)
        for i in range(1, 5)])
    return song


@pytest.fixture
def live(tmp_path):
    songs = tmp_path / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp_path / "no-musescore-here")
    song = _volta_song()

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


def _spans(song):
    root = etree.parse(song.cleaned_path()).getroot()
    return score_fixes.volta_spans(root.find(".//Score/Staff"))


def _shot(page, card, name):
    out = os.environ.get("EVIDENCE_DIR")
    if out:
        os.makedirs(out, exist_ok=True)
        card.screenshot(path=os.path.join(out, name))


def test_a_repeat_with_no_brackets_is_answered_with_one_tap_on_a_phone(live, page):
    base, song = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".problem")
    card = page.locator(".problem")
    assert card.count() == 1
    assert "m6" in card.locator(".top").inner_text()
    assert "All parts" in card.locator(".top").inner_text()
    pick = card.locator(".readpick")
    assert "A repeat ends at bar 6. Does the page print 1. and 2. brackets" in pick.inner_text()
    labels = [o.inner_text() for o in pick.locator(".readopt").all()]
    assert labels[0].startswith("a") and "No brackets on the page" in labels[0]
    assert "as read now" in labels[0]
    assert [label[:1] for label in labels] == list("abcd")
    assert "1. over bars 5–6, 2. over bar 7" in labels[2]
    assert card.bounding_box()["width"] <= 390
    for option in pick.locator(".readopt").all():
        box = option.bounding_box()
        assert box["x"] + box["width"] <= 390
    page.wait_for_function("() => { const i = document.querySelector('.readcrop');"
                           " return i && i.complete && i.naturalWidth > 0; }")
    # The question only: no crop of the page goes into evidence (the songs are not ours).
    _shot(page, pick, "volta-question-phone.png")

    pick.locator(".readopt").nth(2).click()
    page.wait_for_selector("text=No issues")
    assert _spans(song) == [(5, 6), (7, 7)]
    [entry] = json.load(open(os.path.join(song.dir, "fixes.json"), encoding="utf-8"))
    assert (entry["kind"], entry["measure"], entry["bars"]) == ("volta", 6, 2)
    _shot(page, page.locator(".problems"), "volta-written.png")
    assert errors == []


def test_no_brackets_is_listed_as_decided(live, page):
    base, song = live
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".problem")
    page.locator(".problem .readopt").first.click()
    page.wait_for_selector(".readdone")
    assert "Bar 6, All parts: bracket answer a" in page.locator(".readdone").inner_text()
    _shot(page, page.locator(".problems"), "volta-kept.png")
    assert _spans(song) == []
