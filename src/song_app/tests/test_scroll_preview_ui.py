"""The scroll preview in a real browser: play, pause, seek, and what it shows.

The drawing is stubbed — this is about the player, not about verovio, and the
browser CI job has no MuseScore. What it is fed is a miniature of a real payload:
a strip whose pixels spell out their own x position, a second strip holding the
blue a lit symbol is painted, a scroll curve with a repeat in it, and two symbols
that light up.

That first trick is what makes these tests possible at all. Since the preview
became pixels there is no viewBox to read the window off, so the strip encodes
column *x* as `(x >> 8, x & 255, 0)` and a single pixel read back off the canvas
says exactly where the player has scrolled to. Everything here is checked that
way: by looking at what is on screen.
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
np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")


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

FRAME = {"width": 640, "height": 360}
STRIP_WIDTH = 4000
TILE = 2048
PLAYHEAD = 0.35
HIGHLIGHT = (42, 95, 171)
BAND_ALPHA = 0.18

# A curve that walks forward, jumps back to a repeated section a third of the way
# in, and walks forward again. `jump` is the step size past which the player must
# land rather than interpolate — the same line the renderer's smoothing draws.
SCROLL = {
    "times": [0.0, 4.0, 4.05, 8.0],
    "xs": [0.0, 400.0, 100.0, 600.0],
    "jump": 200.0,
}
EVENTS = [
    {"id": "n1", "on": 0.0, "off": 3.0, "staff": 0,
     "x0": 400, "x1": 440, "y0": 100, "y1": 140, "marker": [380, 460]},
    {"id": "n2", "on": 3.0, "off": 8.0, "staff": 0,
     "x0": 500, "x1": 540, "y0": 150, "y1": 190, "marker": [480, 560]},
]


def _strip_colour(x: int):
    """What the stub strip holds at column x — its own position, in two channels."""
    return (x >> 8, x & 255, 0)


def _write_tiles(out_dir: str) -> dict:
    """The two strips the renderer would hand over, as tiles."""
    columns = np.arange(STRIP_WIDTH)
    strip = np.zeros((FRAME["height"], STRIP_WIDTH, 3), dtype=np.uint8)
    strip[:, :, 0] = (columns >> 8)[None, :]
    strip[:, :, 1] = (columns & 255)[None, :]

    lit = np.zeros((FRAME["height"], STRIP_WIDTH, 4), dtype=np.uint8)
    for event in EVENTS:
        box = (slice(event["y0"], event["y1"]), slice(event["x0"], event["x1"]))
        lit[box] = (*HIGHLIGHT, 255)

    described = {}
    for part, array, mode in (("strip", strip, "RGB"), ("lit", lit, "RGBA")):
        tiles = []
        for index, x in enumerate(range(0, STRIP_WIDTH, TILE)):
            width = min(TILE, STRIP_WIDTH - x)
            name = f"{part}-{index}.png"
            Image.fromarray(array[:, x:x + width], mode).save(
                os.path.join(out_dir, name))
            tiles.append({"name": name, "x": x, "width": width})
        described[part] = {"tiles": tiles}
    described["strip"]["width"] = STRIP_WIDTH
    return described


def _payload(duration: float, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    return {
        "frame": FRAME, "playhead": PLAYHEAD, "duration": duration, "fps": 30,
        "scroll": SCROLL, "events": EVENTS,
        "highlight": {"colour": "#%02x%02x%02x" % HIGHLIGHT,
                      "marker_alpha": BAND_ALPHA},
        "parts": ["S1"], "dropped": [], **_write_tiles(out_dir),
    }


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """The real app, with the drawing replaced by a payload of known numbers.

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

    def fake_preview(mscx_path, out_dir, **_kwargs):
        # Two scores, so a rebuild is visible: the edited one previews shorter.
        edited = "<Staff/>" in open(mscx_path).read()
        return _payload(5.0 if edited else 8.0, out_dir)

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
    page.wait_for_selector(".pvviewport > canvas")
    yield page, cleaned
    assert not errors, f"the player raised: {errors}"


def _pixel(page, x, y):
    """One pixel of the frame as it stands on screen."""
    return tuple(page.evaluate(
        """([x, y]) => Array.from(document.querySelector('.pvviewport canvas')
             .getContext('2d').getImageData(x, y, 1, 1).data).slice(0, 3)""",
        [x, y]))


# A column with nothing drawn over it, read where no symbol box reaches.
PROBE = (300, 300)


def _left(page):
    """Which strip column the frame starts at — read out of the picture itself."""
    r, g, _b = _pixel(page, *PROBE)
    return (r << 8) + g - PROBE[0]


def _left_for(x: float) -> float:
    return x - PLAYHEAD * FRAME["width"]


def test_the_player_shows_the_start_of_the_score_in_the_video_s_own_frame(player):
    page, _ = player
    size = page.evaluate(
        """() => { const c = document.querySelector('.pvviewport canvas');
                   return [c.width, c.height]; }""")
    assert size == [FRAME["width"], FRAME["height"]]
    assert _left(page) == pytest.approx(_left_for(SCROLL["xs"][0]), abs=1)
    assert page.locator('[data-preview="time"]').inner_text() == "0:00 / 0:08"


def test_past_the_end_of_the_strip_is_white_as_the_renderer_leaves_it(player):
    """The frame starts before the music does, so its left edge is off the strip."""
    page, _ = player
    assert _pixel(page, 5, 300) == (255, 255, 255)


def test_playing_scrolls_and_pausing_holds(player):
    page, _ = player
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(600)
    moving = _left(page)
    assert moving > _left_for(SCROLL["xs"][0])

    page.locator('[data-preview="play"]').click()   # now reads Pause
    assert page.locator('[data-preview="play"]').inner_text() == "Play"
    held = _left(page)
    page.wait_for_timeout(400)
    assert _left(page) == held


def test_seeking_moves_the_score_at_once(player):
    page, _ = player
    page.locator('[data-preview="seek"]').fill("2")
    assert page.locator('[data-preview="time"]').inner_text() == "0:02 / 0:08"
    # Half way along the first stretch of the curve.
    assert _left(page) == pytest.approx(_left_for(200.0), abs=1)

    page.locator('[data-preview="restart"]').click()
    assert page.locator('[data-preview="time"]').inner_text() == "0:00 / 0:08"
    assert _left(page) == pytest.approx(_left_for(0.0), abs=1)


def test_a_repeat_jumps_back_instead_of_sliding_through_the_music(player):
    """The two sides of the jump, and the moment in between it must not smear."""
    page, _ = player
    seek = page.locator('[data-preview="seek"]')

    seek.fill("4")
    assert _left(page) == pytest.approx(_left_for(400.0), abs=1)

    # Inside the jump: the page holds where it was rather than travelling backwards
    # across everything between the two sections.
    seek.fill("4.02")
    assert _left(page) == pytest.approx(_left_for(400.0), abs=1)

    seek.fill("4.05")
    assert _left(page) == pytest.approx(_left_for(100.0), abs=1)


def _on_screen(page, event, dx=5, dy=5):
    """A pixel inside a symbol's box, wherever the frame currently has it."""
    left = _left(page)
    return _pixel(page, event["x0"] - left + dx, event["y0"] + dy)


def test_the_sounding_symbol_is_the_blue_the_renderer_painted(player):
    """And the blue arrives ready-made: the player copies it, it does not mix it."""
    page, _ = player
    seek = page.locator('[data-preview="seek"]')

    seek.fill("1")
    assert _on_screen(page, EVENTS[0]) == HIGHLIGHT
    silent = EVENTS[1]["x0"] + 5
    assert _on_screen(page, EVENTS[1]) == _strip_colour(silent)

    seek.fill("3.5")
    assert _on_screen(page, EVENTS[1]) == HIGHLIGHT
    assert _on_screen(page, EVENTS[0]) == _strip_colour(EVENTS[0]["x0"] + 5)


def test_the_beat_marker_follows_the_note_that_just_started(player):
    page, _ = player
    page.locator('[data-preview="seek"]').fill("1")
    left = _left(page)

    inside = EVENTS[0]["marker"][0] + 10
    banded = _pixel(page, inside - left, 300)
    plain = _strip_colour(inside)
    expected = tuple(round(c * (1 - BAND_ALPHA) + h * BAND_ALPHA)
                     for c, h in zip(plain, HIGHLIGHT))
    assert banded == pytest.approx(expected, abs=2)

    # Just outside it, the engraving is untouched.
    outside = EVENTS[0]["marker"][1] + 5
    assert _pixel(page, outside - left, 300) == _strip_colour(outside)

    page.locator('[data-preview="seek"]').fill("3.5")
    left = _left(page)
    second = EVENTS[1]["marker"][0] + 10
    assert _pixel(page, second - left, 300) != _strip_colour(second)
    assert _pixel(page, EVENTS[0]["marker"][0] + 10 - left, 300) == \
        _strip_colour(EVENTS[0]["marker"][0] + 10)


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
    page.wait_for_selector(".pvviewport > canvas")

    panel = page.locator(".pvviewport")
    assert panel.bounding_box()["width"] <= 390
    # The controls are on screen and usable, not off the side of the panel.
    for control in ("play", "restart", "seek"):
        assert page.locator(f'[data-preview="{control}"]').is_visible()
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(400)
    assert _left(page) > _left_for(SCROLL["xs"][0])


def test_a_refused_score_says_so_instead_of_playing(live, page):
    base, slug, _ = live
    _open_record(page, base, slug)
    page.route("**/scroll-preview*", lambda route: route.fulfill(
        status=400, content_type="application/json",
        body='{"detail": "This score uses Jump (a D.C./D.S. jump)"}'))
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvstatus.err")
    assert "D.C./D.S." in page.locator(".pvstatus").inner_text()
    assert page.locator(".pvviewport").count() == 0
