"""Recording a missing slur in a real browser.

The Python tests pin what the route does. What only exists in the browser is the
half the card is actually about: that the two notes can be *picked* off the bar
rather than counted out into JSON, and that the person is told the syllable count
is about to change before anything is written.
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

from src.song_app import server, state

pytestmark = pytest.mark.browser

# Herää Suomi!, bar 8, Tenor 1 as the scan left it: the page slurs E flat to D.
SCORE = """<museScore><Score>
<Part><trackName>T1</trackName><Staff id="1"/></Part>
<Part><trackName>T2</trackName><Staff id="2"/></Part>
<Staff id="1">
  <Measure><voice>
    <Chord><durationType>whole</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
  <Measure><voice>
    <Rest><durationType>half</durationType></Rest>
    <Chord><durationType>quarter</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
    <Chord><durationType>eighth</durationType><dots>1</dots>
           <Note><pitch>63</pitch><tpc>11</tpc></Note></Chord>
    <Chord><durationType>16th</durationType><Note><pitch>62</pitch><tpc>16</tpc></Note></Chord>
  </voice></Measure>
</Staff>
<Staff id="2">
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
  <Measure><voice><Rest><durationType>whole</durationType></Rest></voice></Measure>
</Staff>
</Score></museScore>"""

WHY = "Page 1 system 2, bar 8: the tenor slurs E flat to D over one syllable."


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

    song = state.create("Slur Panel Song", per_system=False)
    with open(song.path("slur_cleaned.mscx"), "w", encoding="utf-8") as fh:
        fh.write(SCORE)
    song.data["cleaned"] = "slur_cleaned.mscx"
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
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Fix").first.click()
    page.wait_for_selector(".slurfix .slurnote")
    return errors


def _pick_bar(page, measure):
    page.locator(".slurbar").fill(str(measure))
    page.locator(".slurbar").dispatch_event("change")
    page.wait_for_function(
        "(m) => document.querySelectorAll('.slurfix .slurnote').length === m",
        arg=3 if measure == 2 else 1)


def _fixes(song):
    path = os.path.join(song.dir, "fixes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []


def test_the_bar_is_shown_as_its_own_notes_to_pick_from(live, page):
    base, song = live
    errors = _open_fix(page, base, song.slug)
    _pick_bar(page, 2)

    assert [n.inner_text() for n in page.locator(".slurfix .slurnote").all()] == [
        "1. D4", "2. Eb4", "3. D4"]
    # The last note cannot open a slur and the first cannot close one.
    assert page.locator(".slurfrom option").count() == 2
    assert page.locator(".slurto option").count() == 2
    assert not errors, f"the panel raised: {errors}"


def test_it_says_what_the_slur_costs_the_bar_before_anything_is_written(live, page):
    base, song = live
    _open_fix(page, base, song.slug)
    _pick_bar(page, 2)
    page.locator(".slurfrom").select_option("1")
    page.locator(".slurto").select_option("2")

    assert "Eb4 → D4" in page.locator(".slureffect").inner_text()
    assert "3 syllable(s) to 2" in page.locator(".slureffect").inner_text()
    assert _fixes(song) == []


def test_picking_two_notes_records_the_slur(live, page):
    base, song = live
    errors = _open_fix(page, base, song.slug)
    _pick_bar(page, 2)
    page.locator(".slurfrom").select_option("1")
    page.locator(".slurto").select_option("2")
    page.locator(".slurwhy").fill(WHY)
    page.locator(".slursave").click()
    page.wait_for_selector("text=1 slur(s) recorded")

    assert _fixes(song) == [
        {"kind": "slur", "staff": 1, "measure": 2, "index": 1, "span": 1, "why": WHY}]
    # And the note it reaches stops taking a syllable, which is the point of recording it.
    _pick_bar(page, 2)
    assert page.locator(".slurfix .slurnote.nosyl").inner_text() == "3. D4"
    assert not errors, f"the panel raised: {errors}"

    if evidence := os.getenv("ISSUE_88_EVIDENCE_DIR"):
        os.makedirs(evidence, exist_ok=True)
        page.locator(".panel").screenshot(
            path=os.path.join(evidence, "issue-88-slur-recorded.png"))


def test_it_fits_a_phone(live, page):
    """The card's complaint: nobody hand-writes fixes.json from a phone."""
    base, song = live
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{song.slug}")
    # Below the breakpoint one pane shows at a time, so the stage rail is a tab.
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Fix").first.click()
    page.wait_for_selector(".slurfix .slurnote")
    _pick_bar(page, 2)
    page.locator(".slurfrom").select_option("1")
    page.locator(".slurto").select_option("2")
    page.locator(".slurwhy").fill(WHY)

    assert page.locator(".slursave").is_visible()
    assert not page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 1")

    if evidence := os.getenv("ISSUE_88_EVIDENCE_DIR"):
        os.makedirs(evidence, exist_ok=True)
        page.locator(".slurfix").screenshot(
            path=os.path.join(evidence, "issue-88-slur-phone.png"))


def test_a_reason_is_required_and_the_score_is_left_alone_without_one(live, page):
    base, song = live
    _open_fix(page, base, song.slug)
    _pick_bar(page, 2)
    page.locator(".slursave").click()
    page.wait_for_function(
        "() => (document.querySelector('.slurerr')?.textContent || '').length > 0")

    assert "say why" in page.locator(".slurerr").inner_text()
    assert _fixes(song) == []


def test_imported_lyrics_are_warned_about_before_the_write(live, page):
    """Re-slurring shortens the bar, so that line comes back one syllable too long."""
    base, song = live
    song.data["lyrics"] = {"json": "lyrics.json"}
    song.save()
    _open_fix(page, base, song.slug)
    _pick_bar(page, 2)
    page.locator(".slurwhy").fill(WHY)

    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    page.locator(".slursave").click()
    page.wait_for_timeout(300)

    assert seen and "one syllable too long" in seen[0]
    assert _fixes(song) == [], "dismissing the warning must not write anything"
