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
from playwright.sync_api import expect

from src.song_app import job_state, server, state

pytestmark = pytest.mark.browser

SCORE = """<museScore><Score>
<Part><trackName>S</trackName><Staff id="1"/></Part>
<Part><trackName>A</trackName><Staff id="2"/></Part>
<Part><trackName>T</trackName><Staff id="3"/></Part>
<Part><trackName>B</trackName><Staff id="4"/></Part>
<Staff id="1"><Measure><voice><Chord><Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>
<Staff id="2"><Measure><voice><Chord><Note><pitch>55</pitch></Note></Chord></voice></Measure></Staff>
<Staff id="3"><Measure><voice><Chord><Note><pitch>50</pitch></Note></Chord></voice></Measure></Staff>
<Staff id="4"><Measure><voice><Chord><Note><pitch>45</pitch></Note></Chord></voice></Measure></Staff>
</Score></museScore>"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _page_overflows(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth + 1"
        " || document.documentElement.scrollHeight > window.innerHeight + 1"
    )


def _screenshot(page, name):
    evidence = os.getenv("ISSUE_66_EVIDENCE_DIR")
    if not evidence:
        return
    os.makedirs(evidence, exist_ok=True)
    page.screenshot(path=os.path.join(evidence, name), full_page=True)


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
        fh.write(SCORE)
    song.data["cleaned"] = "panel_cleaned.mscx"
    fingerprint = state.file_fingerprint(song.cleaned_path())
    song.data["cleaned_fingerprint"] = fingerprint
    song.data["health"] = {"checked_against": fingerprint, "issues": []}
    song.data["verification"] = {"notes": {
        "checked_against": fingerprint, "status": "passed",
        "detail": "All source notes are preserved.",
    }}
    song.data["lyrics"] = {"imported_against": fingerprint, "warnings": []}
    song.data["review"] = {"approved_against": fingerprint}
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
def record_panel(live, page):
    base, slug = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Record").click()
    page.wait_for_selector("text=Video style")
    yield page, slug, errors
    assert not errors, f"the panel raised: {errors}"


def test_the_panel_offers_both_renderers_and_starts_on_the_scrolling_one(record_panel):
    view, _, _ = record_panel
    assert view.get_by_text("Scrolling score", exact=True).is_visible()
    assert view.get_by_text("Screen recording", exact=True).is_visible()
    radios = view.locator("input[type=radio][name=renderer]")
    assert radios.nth(0).is_checked(), "the scrolling renderer should be the default"
    assert not radios.nth(1).is_checked()
    assert view.locator('select option[value="720p"]').text_content() == "720p, 30fps (test)"
    advanced = view.locator(".record-advanced")
    assert not advanced.get_attribute("open"), "advanced settings start collapsed"
    assert not view.get_by_text("Use NVIDIA hardware encoding when available").is_visible()
    assert view.get_by_text("Tempo (BPM)", exact=True).is_visible()
    assert view.locator('input[type="number"][min="20"][max="300"]').input_value() == "80"
    top = view.locator('input[data-video-margin="top"]')
    bottom = view.locator('input[data-video-margin="bottom"]')
    assert top.input_value() == "0"
    assert bottom.input_value() == "5"
    advanced.locator("summary").click()
    assert view.get_by_text("Top margin", exact=True).is_visible()
    assert view.get_by_text("Bottom margin", exact=True).is_visible()
    hardware = view.get_by_text("Use NVIDIA hardware encoding when available").locator("..").locator("input")
    assert hardware.is_checked()
    assert view.get_by_role("button", name="Render all 4 parts").is_visible()
    if evidence := os.getenv("ISSUE_26_EVIDENCE_DIR"):
        view.locator("select").select_option("720p")
        view.locator("text=Output").scroll_into_view_if_needed()
        view.screenshot(path=os.path.join(evidence, "issue-26-render-options.png"))
    if evidence := os.getenv("ISSUE_28_EVIDENCE_DIR"):
        view.get_by_text("Tempo (BPM)", exact=True).scroll_into_view_if_needed()
        view.screenshot(path=os.path.join(evidence, "issue-28-bpm.png"))
    if evidence := os.getenv("ISSUE_74_EVIDENCE_DIR"):
        view.get_by_text("Bottom margin", exact=True).scroll_into_view_if_needed()
        assert bottom.input_value() == "5"
        view.screenshot(path=os.path.join(evidence, "issue-74-bottom-margin.png"))


def test_choosing_the_screen_recorder_swaps_the_controls(record_panel):
    view, _, _ = record_panel
    size = view.locator("select")           # the size choice, scrolling renderer only
    top = view.locator('input[data-video-margin="top"]')
    bottom = view.locator('input[data-video-margin="bottom"]')
    assert size.is_visible() and size.input_value() == "4k"
    assert not top.is_visible() and not bottom.is_visible()
    assert not view.get_by_text("Audio sync offset (ms)").is_visible()

    view.get_by_text("Screen recording", exact=True).click()
    view.locator(".record-advanced").locator("summary").click()
    assert view.get_by_role("button", name="Record all 4 parts").is_visible()
    assert view.get_by_text("Audio sync offset (ms)").is_visible()
    assert not size.is_visible(), "the size choice does not apply to screen recording"
    assert not top.is_visible() and not bottom.is_visible(), "video margins only apply to scrolling"
    assert not view.get_by_text("Tempo (BPM)", exact=True).is_visible()


def test_the_run_button_posts_the_chosen_renderer(record_panel):
    """What the server branches on has to actually leave the browser."""
    view, slug, _ = record_panel
    sent = []
    view.route(f"**/api/songs/{slug}/record", lambda route: (
        sent.append(json.loads(route.request.post_data or "{}")),
        route.fulfill(status=200, content_type="application/json",
                      body='{"started": true}')))

    view.get_by_role("button", name="Render all 4 parts").click()
    view.wait_for_timeout(300)
    assert sent and sent[0]["renderer"] == "scroll"
    assert sent[0]["quality"] == "4k"
    assert sent[0]["hardware_encoding"] is True
    assert sent[0]["bpm"] == 80
    assert sent[0]["top_margin"] == 0
    # A song nobody has framed by hand renders with a little space under the
    # bottom staff, so its lowest lyrics are not against the frame edge.
    assert sent[0]["bottom_margin"] == 5


def test_margin_adjustments_are_posted_independently(record_panel):
    view, slug, _ = record_panel
    sent = []
    view.route(f"**/api/songs/{slug}/record", lambda route: (
        sent.append(json.loads(route.request.post_data or "{}")),
        route.fulfill(status=200, content_type="application/json",
                      body='{"started": true}')))

    view.locator(".record-advanced").locator("summary").click()
    view.locator('input[data-video-margin="top"]').fill("12")
    view.locator('input[data-video-margin="bottom"]').fill("-8")
    view.get_by_role("button", name="Render all 4 parts").click()
    view.wait_for_timeout(300)

    assert sent
    assert sent[0]["top_margin"] == 12
    assert sent[0]["bottom_margin"] == -8


def test_render_progress_survives_a_page_reload(record_panel):
    view, slug, _ = record_panel
    song = state.load(slug)
    lock = server._lock_path(song)
    try:
        job_state.start(song.dir, "render")
        job_state.append(song.dir, "render", "Rendering video: 42% (2520/6000 frames)",
                         "progress")
        with open(lock, "w") as handle:
            handle.write(str(os.getpid()))

        view.reload()
        view.wait_for_selector(".progress")
        assert "42%" in view.locator(".progress").inner_text()
        assert view.get_by_text("Rendering… leave this running.", exact=False).is_visible()
        if evidence := os.getenv("ISSUE_26_EVIDENCE_DIR"):
            view.locator(".progress").scroll_into_view_if_needed()
            view.screenshot(path=os.path.join(evidence, "issue-26-render-progress.png"))
    finally:
        if os.path.exists(lock):
            os.remove(lock)
        job_state.finish(song.dir, "render")


def test_mobile_review_and_record_are_task_focused(live, page):
    base, slug = live
    jobs_path = state.load(slug).path(job_state.JOBS_FILE)
    if os.path.exists(jobs_path):
        os.remove(jobs_path)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 320, "height": 700})
    page.goto(f"{base}/#/song/{slug}")
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Review").click()

    expect(page.locator(".compact-review")).to_be_visible()
    expect(page.locator(".review-full")).to_be_hidden()
    expect(page.get_by_text("Ready to approve", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="✓ Approve → Record")).to_be_in_viewport()
    expect(page.locator(".mobilebar").get_by_role("button", name="Score")).to_be_visible()
    assert not _page_overflows(page)
    _screenshot(page, "issue-66-review-ready-320.png")

    song = state.load(slug)
    current = state.file_fingerprint(song.cleaned_path())
    song.data["health"]["checked_against"] = "older-score"
    song.save()
    page.reload()
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Review").click()
    expect(page.get_by_text("Needs attention", exact=True)).to_be_visible()
    expect(page.locator(".compact-check.stale")).to_be_visible()
    assert not _page_overflows(page)
    _screenshot(page, "issue-66-review-stale-320.png")

    song = state.load(slug)
    song.data["health"]["checked_against"] = current
    song.save()
    page.reload()
    page.set_viewport_size({"width": 430, "height": 820})
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Review").click()
    page.get_by_role("button", name="✓ Approve → Record").click()
    page.wait_for_timeout(300)
    assert not errors, f"the mobile workflow raised: {errors}"

    expect(page.get_by_text("✓ Score approved", exact=True)).to_be_visible()
    expect(page.locator(".mobilebar").get_by_role("button", name="Preview")).to_be_visible()
    expect(page.locator(".record-advanced")).not_to_have_attribute("open", "")
    expect(page.get_by_role("button", name="Render all 4 parts")).to_be_in_viewport()
    assert not _page_overflows(page)
    _screenshot(page, "issue-66-record-scrolling-430.png")

    page.get_by_text("Screen recording", exact=True).click()
    expect(page.get_by_role("button", name="Record all 4 parts")).to_be_in_viewport()
    expect(page.locator(".preview-action")).to_be_hidden()
    assert not _page_overflows(page)
    _screenshot(page, "issue-66-record-screen-430.png")

    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    expect(page.locator(".stagebar")).to_be_visible()
    assert not _page_overflows(page)
    _screenshot(page, "issue-66-stages-430.png")

    song = state.load(slug)
    with open(song.cleaned_path(), "a") as fh:
        fh.write("\n")
    page.reload()
    page.locator(".mobilebar").get_by_role("button", name="Record").click()
    expect(page.get_by_text("⚠ Score changed after approval", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Back to Review")).to_be_visible()
    expect(page.get_by_role("button", name="Render all 4 parts")).to_be_disabled()
