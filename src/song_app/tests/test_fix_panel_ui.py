"""The Fix panel in a real browser: an outstanding free-text fix has to be visible.

The Python tests pin that cleaning reports the sentence. The whole point of writing
one down is that it does not get forgotten, and the log scrolls past — so this
covers the half that only exists in the browser.
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

SAID = "B1 bar 40, last eighth: drop the D, keep the C — the basses cross here."


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fixpanel")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")

    song = state.create("Fix Panel Song", per_system=False)
    with open(song.path("fixpanel_cleaned.mscx"), "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = "fixpanel_cleaned.mscx"
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
    page.wait_for_selector("text=Open in MuseScore")
    return errors


def test_an_outstanding_sentence_is_shown_on_the_fix_stage(live, page):
    base, song = live
    with open(os.path.join(song.dir, "fixes.json"), "w", encoding="utf-8") as fh:
        json.dump([{"kind": "text", "what": SAID}], fh)
    errors = _open_fix(page, base, song.slug)

    assert page.get_by_text(SAID).is_visible()
    assert page.get_by_text("not applied automatically").is_visible()
    assert not errors, f"the panel raised: {errors}"


def test_nothing_is_shown_when_there_is_nothing_outstanding(live, page):
    base, song = live
    path = os.path.join(song.dir, "fixes.json")
    if os.path.exists(path):
        os.remove(path)
    errors = _open_fix(page, base, song.slug)

    assert not page.get_by_text("not applied automatically").is_visible()
    assert page.get_by_text("No issues").is_visible()
    assert not errors, f"the panel raised: {errors}"


# The same panel is where a collapsed meter summary has to land, because a summary
# nobody can see is the silence this replaces (#124). It is an ordinary finding —
# it lists, it says how many bars and where to start, and it dismisses — so the
# only thing worth pinning in a browser is that it really is one.
COLLAPSED = {
    "id": "meter-collapsed-66",
    "kind": "meter-collapsed",
    "measure": 2,
    "staff": "whole score",
    "status": "open",
    "collapsed": 18,
    "collapsed_bars": 18,
    "detail": ("18 bar(s) sit at a length the engraving never prints (66 staff-bars, "
               "first at m2), listed as one line because 80% of bars carry their own "
               "length — free or mixed meter looks like this, and so does a badly "
               "parsed score"),
}


def test_a_collapsed_meter_summary_reads_as_a_finding_not_as_silence(live, page):
    base, song = live
    path = os.path.join(song.dir, "fixes.json")
    if os.path.exists(path):
        os.remove(path)
    song.data["health"] = {
        "checked_against": song.data.get("cleaned_fingerprint"),
        "issues": [COLLAPSED],
    }
    song.save()
    errors = _open_fix(page, base, song.slug)

    assert page.get_by_text("meter-collapsed").is_visible()
    assert page.get_by_text("18 bar(s) sit at a length").is_visible()
    assert not page.get_by_text("No issues").is_visible()

    evidence = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "evidence")
    os.makedirs(evidence, exist_ok=True)
    page.screenshot(path=os.path.join(evidence, "issue-124-meter-collapsed.png"),
                    full_page=True)

    page.get_by_role("button", name="Dismiss").click()
    page.wait_for_selector("text=No issues")
    assert not errors, f"the panel raised: {errors}"
    song.data["health"] = {"checked_against": None, "issues": []}
    song.save()


def test_the_review_stage_shows_the_findings_behind_the_collapsed_line(live, page):
    """The number two scans get compared by. Collapsing rows must not collapse it.

    B6's whole-page parse would otherwise read "4 open issue(s)" beside its
    per-system parse's 28 — the comparison the whole card is about.
    """
    base, song = live
    fingerprint = state.file_fingerprint(song.cleaned_path())
    song.data["health"] = {
        "checked_against": fingerprint,
        "issues": [COLLAPSED] + [
            {"id": f"malformed-m{m}-s1-v0", "kind": "malformed-measure", "measure": m,
             "staff": "T1", "status": "open", "detail": "voice 1 fills 7/8 of 1"}
            for m in (4, 9, 12)
        ],
    }
    song.data["stage"] = "review"
    song.save()

    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{song.slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Review").first.click()
    page.wait_for_selector(".verify")

    health_row = page.locator(".verify .check", has_text="Health").first
    health_row.wait_for()
    # 18 collapsed meter findings + 3 malformed = 21, from 4 rows in the Fix panel.
    assert "21 open issue(s)" in health_row.inner_text()
    assert "18 of them are meter findings" in health_row.inner_text()

    evidence = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "evidence")
    os.makedirs(evidence, exist_ok=True)
    page.screenshot(path=os.path.join(evidence, "issue-124-review-count.png"),
                    full_page=True)
    assert not errors, f"the panel raised: {errors}"

    song.data["health"] = {"checked_against": None, "issues": []}
    song.data["stage"] = "fix"
    song.save()
