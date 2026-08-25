"""The scroll preview in a real browser: play, pause, seek, and what it shows.

The engraving is stubbed — this is about the player, not about verovio, and the
browser CI job has no MuseScore. The payload it is fed is a miniature of a real
one: a page in score units, a viewport, a scroll curve with a repeat in it, and two
symbols that light up. The picture it produces is checked the only way it can
honestly be checked, by reading the window the player put on the page.
"""
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

import uvicorn  # noqa: E402

from src.song_app import server, state  # noqa: E402

pytestmark = pytest.mark.browser

PAGE = {"width": 4000.0, "height": 1200.0}
VIEW = {"start": 0.0, "end": 900.0, "height": 900.0, "aspect": 16 / 9}
WINDOW = VIEW["height"] * VIEW["aspect"]
PLAYHEAD = 0.35

# A curve that walks forward, jumps back to a repeated section a third of the way
# in, and walks forward again. `jump` is the step size past which the player must
# land rather than interpolate — the same line the renderer's smoothing draws.
SCROLL = {
    "times": [0.0, 4.0, 4.05, 8.0],
    "xs": [0.0, 2400.0, 400.0, 2600.0],
    "jump": 1000.0,
}
EVENTS = [
    {"id": "n1", "on": 0.0, "off": 3.0, "staff": 0, "marker": [100.0, 220.0]},
    {"id": "n2", "on": 3.0, "off": 8.0, "staff": 0, "marker": [900.0, 1020.0]},
]

# Shaped like verovio's output: the coordinates live in a nested
# <svg class="definition-scale">, and a note is a group whose notehead is a <use>.
SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%"
   viewBox="0 0 {PAGE['width']:g} {PAGE['height']:g}" preserveAspectRatio="none">
  <svg class="definition-scale" viewBox="0 0 {PAGE['width']:g} {PAGE['height']:g}"
       width="{PAGE['width']:g}" height="{PAGE['height']:g}">
    <g id="n1" class="note"><g class="notehead">
      <ellipse cx="160" cy="400" rx="60" ry="40"/></g></g>
    <g id="n2" class="note"><g class="notehead">
      <ellipse cx="960" cy="500" rx="60" ry="40"/></g></g>
  </svg>
</svg>"""


def _payload(duration: float) -> dict:
    return {
        "svg": SVG, "page": PAGE, "view": VIEW, "playhead": PLAYHEAD,
        "duration": duration, "fps": 30, "scroll": SCROLL, "events": EVENTS,
        "highlight": {"colour": "#2a5fab", "marker_alpha": 0.18},
        "parts": ["S1"], "dropped": [],
    }


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """The real app, with the engraving replaced by a payload of known numbers.

    The stub reads the cleaned score, so editing it changes what a fresh
    preparation would return — which is how the staleness test can tell a reused
    preview from a rebuilt one.
    """
    import src.scrollvideo.preview as preview_mod

    tmp = tmp_path_factory.mktemp("scrollpreview")
    songs = tmp / "songs"
    songs.mkdir()
    previous_dir, state.SONGS_DIR = state.SONGS_DIR, str(songs)
    previous_cli = os.environ.get("MUSESCORE_CLI_PATH")
    os.environ["MUSESCORE_CLI_PATH"] = str(tmp / "no-musescore-here")
    previous_preview = preview_mod.preview

    def fake_preview(mscx_path, **_kwargs):
        # Two scores, so a rebuild is visible: the edited one previews shorter.
        edited = "<Staff/>" in open(mscx_path).read()
        return _payload(5.0 if edited else 8.0)

    preview_mod.preview = fake_preview

    song = state.create("Preview Song", per_system=False)
    cleaned = song.path("preview_cleaned.mscx")
    with open(cleaned, "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = "preview_cleaned.mscx"
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
        yield f"http://127.0.0.1:{port}", song.slug, cleaned
    finally:
        srv.should_exit = True
        thread.join(timeout=10)
        preview_mod.preview = previous_preview
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _open_record(page, base, slug):
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Record").click()
    page.wait_for_selector("text=How to make the videos")


@pytest.fixture
def player(live, page):
    base, slug, cleaned = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _open_record(page, base, slug)
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > svg")
    yield page, cleaned
    assert not errors, f"the player raised: {errors}"


def _window(page):
    """The slice of the page on screen: (left, top, width, height) in score units."""
    box = page.locator(".pvviewport > svg").get_attribute("viewBox")
    return [float(v) for v in box.split()]


def _left_for(x: float) -> float:
    return x - PLAYHEAD * WINDOW


def test_the_player_shows_the_start_of_the_score_in_the_video_s_own_frame(player):
    page, _ = player
    left, top, width, height = _window(page)
    assert (top, height) == (VIEW["start"], VIEW["height"])
    assert width == pytest.approx(WINDOW, rel=1e-6)
    assert left == pytest.approx(_left_for(SCROLL["xs"][0]), rel=1e-6)
    assert page.locator('[data-preview="time"]').inner_text() == "0:00 / 0:08"


def test_playing_scrolls_and_pausing_holds(player):
    page, _ = player
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(600)
    moving = _window(page)[0]
    assert moving > _left_for(SCROLL["xs"][0])

    page.locator('[data-preview="play"]').click()   # now reads Pause
    assert page.locator('[data-preview="play"]').inner_text() == "Play"
    held = _window(page)[0]
    page.wait_for_timeout(400)
    assert _window(page)[0] == held


def test_seeking_moves_the_score_at_once(player):
    page, _ = player
    page.locator('[data-preview="seek"]').fill("2")
    assert page.locator('[data-preview="time"]').inner_text() == "0:02 / 0:08"
    # Half way along the first stretch of the curve.
    assert _window(page)[0] == pytest.approx(_left_for(1200.0), rel=1e-6)

    page.locator('[data-preview="restart"]').click()
    assert page.locator('[data-preview="time"]').inner_text() == "0:00 / 0:08"
    assert _window(page)[0] == pytest.approx(_left_for(0.0), rel=1e-6)


def test_a_repeat_jumps_back_instead_of_sliding_through_the_music(player):
    """The two sides of the jump, and the moment in between it must not smear."""
    page, _ = player
    seek = page.locator('[data-preview="seek"]')

    seek.fill("4")
    assert _window(page)[0] == pytest.approx(_left_for(2400.0), rel=1e-6)

    # Inside the jump: the page holds where it was rather than travelling backwards
    # across everything between the two sections.
    seek.fill("4.02")
    assert _window(page)[0] == pytest.approx(_left_for(2400.0), rel=1e-6)

    seek.fill("4.05")
    assert _window(page)[0] == pytest.approx(_left_for(400.0), rel=1e-6)


def test_the_sounding_note_is_lit_and_the_others_are_not(player):
    page, _ = player
    seek = page.locator('[data-preview="seek"]')

    seek.fill("1")
    assert "pv-on" in (page.locator("#n1").get_attribute("class") or "")
    assert "pv-on" not in (page.locator("#n2").get_attribute("class") or "")

    seek.fill("5")
    assert "pv-on" not in (page.locator("#n1").get_attribute("class") or "")
    assert "pv-on" in (page.locator("#n2").get_attribute("class") or "")

    # It is the glyph itself that changes colour, not a box drawn over it.
    painted = page.evaluate(
        "getComputedStyle(document.querySelector('#n2 .notehead ellipse')).fill")
    assert painted == "rgb(42, 95, 171)"


def test_the_beat_marker_follows_the_note_that_just_started(player):
    page, _ = player
    marker = page.locator('[data-preview="marker"]')

    page.locator('[data-preview="seek"]').fill("1")
    assert float(marker.get_attribute("x")) == pytest.approx(EVENTS[0]["marker"][0])
    assert float(marker.get_attribute("width")) == pytest.approx(120.0)

    page.locator('[data-preview="seek"]').fill("5")
    assert float(marker.get_attribute("x")) == pytest.approx(EVENTS[1]["marker"][0])


def test_the_preview_is_prepared_again_once_the_score_changes(player):
    """A preview of a score that is no longer on disk is worse than none."""
    page, cleaned = player
    assert page.locator('[data-preview="time"]').inner_text() == "0:00 / 0:08"

    original = open(cleaned).read()
    try:
        with open(cleaned, "w") as fh:
            fh.write("<museScore><Score><Staff/></Score></museScore>")
        page.locator('[data-preview="open"]').click()
        page.wait_for_selector('[data-preview="time"]:has-text("0:05")')
    finally:
        with open(cleaned, "w") as fh:
            fh.write(original)


def test_the_player_fits_a_phone(live, page):
    base, slug, _ = live
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{slug}")
    # One pane at a time on a phone: the stage rail is behind the bottom bar.
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Record").click()
    page.wait_for_selector("text=How to make the videos")
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > svg")

    panel = page.locator(".pvviewport")
    assert panel.bounding_box()["width"] <= 390
    # The controls are on screen and usable, not off the side of the panel.
    for control in ("play", "restart", "seek"):
        assert page.locator(f'[data-preview="{control}"]').is_visible()
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(400)
    assert _window(page)[0] > _left_for(SCROLL["xs"][0])


def test_a_refused_score_says_so_instead_of_playing(live, page):
    base, slug, _ = live
    _open_record(page, base, slug)
    page.route(f"**/scroll-preview*", lambda route: route.fulfill(
        status=400, content_type="application/json",
        body='{"detail": "This score uses Jump (a D.C./D.S. jump)"}'))
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvstatus.err")
    assert "D.C./D.S." in page.locator(".pvstatus").inner_text()
    assert page.locator(".pvviewport").count() == 0
