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
import wave

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

from src.song_app import pipeline, server, state  # noqa: E402

pytestmark = pytest.mark.browser

FRAME = {"width": 640, "height": 360}
STRIP_WIDTH = 4000
TILE = 2048
PLAYHEAD = 0.35
HIGHLIGHT = (42, 95, 171)
BAND_ALPHA = 0.18
BACKGROUND_ALPHA = 0.45
BACKGROUND = tuple(int(255 - BACKGROUND_ALPHA * (255 - c)) for c in HIGHLIGHT)

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
    {"id": "n3", "on": 0.0, "off": 3.0, "staff": 1,
     "x0": 500, "x1": 540, "y0": 150, "y1": 190, "marker": None},
    {"id": "n2", "on": 3.0, "off": 8.0, "staff": 1,
     "x0": 500, "x1": 540, "y0": 200, "y1": 240, "marker": [480, 560]},
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
    background = np.zeros((FRAME["height"], STRIP_WIDTH, 4), dtype=np.uint8)
    for event in EVENTS:
        box = (slice(event["y0"], event["y1"]), slice(event["x0"], event["x1"]))
        lit[box] = (*HIGHLIGHT, 255)
        background[box] = (*BACKGROUND, 255)

    described = {}
    for part, array, mode in (("strip", strip, "RGB"), ("lit", lit, "RGBA"),
                              ("background", background, "RGBA")):
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
        "parts": ["S1", "B1"], "dropped": [], "focus_staves": True,
        **_write_tiles(out_dir),
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
    previous_audio = pipeline.scroll_preview_audio

    audio_paths = {}
    for name in ("ALL", "S1", "B1"):
        path = tmp / f"{name}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\0\0" * 8000 * 6)
        audio_paths[name] = str(path)

    def fake_preview(mscx_path, out_dir, **_kwargs):
        # Two scores, so a rebuild is visible: the edited one previews shorter.
        edited = "<Staff/>" in open(mscx_path).read()
        payload = _payload(5.0 if edited else 8.0, out_dir)
        with open(os.path.join(out_dir, preview_mod.AUDIO_SOURCE), "wb") as fh:
            fh.write(open(mscx_path, "rb").read())
        return payload

    def fake_audio(_song_dir, _cleaned, mix, _revision, **_settings):
        if mix == "S1":
            time.sleep(0.25)
        return audio_paths[mix], True

    preview_mod.preview = fake_preview
    pipeline.scroll_preview_audio = fake_audio

    song = state.create("Preview Song", per_system=False)
    cleaned = song.path("preview_cleaned.mscx")
    with open(cleaned, "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = "preview_cleaned.mscx"
    song.data["cleaned_fingerprint"] = state.file_fingerprint(cleaned)
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
        pipeline.scroll_preview_audio = previous_audio
        state.SONGS_DIR = previous_dir
        if previous_cli is None:
            os.environ.pop("MUSESCORE_CLI_PATH", None)
        else:
            os.environ["MUSESCORE_CLI_PATH"] = previous_cli


def _open_record(page, base, slug):
    page.goto(f"{base}/#/song/{slug}")
    page.wait_for_selector(".stagebar")
    page.locator(".stagebar .step", has_text="Record").click()
    page.wait_for_selector("text=Video style")


@pytest.fixture
def player(live, page):
    base, slug, cleaned = live
    errors = []
    audio_requests = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda request: audio_requests.append(request.url)
            if "/scroll-preview-audio?" in request.url else None)
    _open_record(page, base, slug)
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("Audio off")')
    page._preview_audio_requests = audio_requests
    yield page, cleaned
    assert not errors, f"the player raised: {errors}"


def _enable_audio(page):
    page.locator('[data-preview="audio-enabled"]').check()
    page.wait_for_selector('[data-preview="audio-status"]:has-text("ALL audio ready")')


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


def test_audio_is_off_until_the_user_enables_it(player):
    page, _ = player
    assert page._preview_audio_requests == []
    assert page.locator('[data-preview="audio-enabled"]').is_checked() is False
    assert page.locator('[data-preview="mix"]').is_hidden()
    assert page.locator('[data-preview="audio"]').is_hidden()
    assert page.locator('[data-preview="play"]').is_enabled()

    _enable_audio(page)
    assert len(page._preview_audio_requests) == 1
    assert "mix=ALL" in page._preview_audio_requests[0]
    assert page.locator('[data-preview="mix"]').is_visible()
    assert page.locator('[data-preview="audio"]').is_visible()


def test_disabling_audio_returns_active_playback_to_the_silent_clock(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(250)
    before = float(page.locator('[data-preview="seek"]').input_value())
    requests = len(page._preview_audio_requests)
    page.locator('[data-preview="audio"]').evaluate(
        "a => { a.pause = () => setTimeout(() => "
        "a.dispatchEvent(new Event('pause')), 50); }")

    page.locator('[data-preview="audio-enabled"]').uncheck()
    page.wait_for_selector('[data-preview="audio-status"]:has-text("Audio off")')
    page.wait_for_timeout(250)

    assert page.locator('[data-preview="play"]').inner_text() == "Pause"
    assert float(page.locator('[data-preview="seek"]').input_value()) > before
    assert len(page._preview_audio_requests) == requests
    assert page.locator('[data-preview="mix"]').is_hidden()
    assert page.locator('[data-preview="audio"]').is_hidden()


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
    silent = EVENTS[2]["x0"] + 5
    assert _on_screen(page, EVENTS[2]) == _strip_colour(silent)

    seek.fill("3.5")
    assert _on_screen(page, EVENTS[2]) == HIGHLIGHT
    assert _on_screen(page, EVENTS[0]) == _strip_colour(EVENTS[0]["x0"] + 5)


def test_a_part_mix_changes_focus_without_rebuilding_the_picture(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="seek"]').fill("1")
    original_canvas = page.locator('[data-preview="canvas"]').evaluate(
        "c => (c.__issue64 = 'same-canvas')")

    page.locator('[data-preview="mix"]').select_option("S1")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("S1 audio ready")')
    assert _on_screen(page, EVENTS[0]) == HIGHLIGHT
    assert _on_screen(page, EVENTS[1]) == BACKGROUND
    assert page.locator('[data-preview="time"]').inner_text() == "0:01 / 0:08"

    page.locator('[data-preview="mix"]').select_option("ALL")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("ALL audio ready")')
    assert _on_screen(page, EVENTS[0]) == HIGHLIGHT
    assert _on_screen(page, EVENTS[1]) == HIGHLIGHT
    assert page.locator('[data-preview="canvas"]').evaluate("c => c.__issue64") == \
        original_canvas


def test_a_late_mix_request_cannot_replace_the_new_selection(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="mix"]').select_option("S1")
    page.locator('[data-preview="mix"]').select_option("B1")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("B1 audio ready")')
    page.wait_for_timeout(350)
    assert page.locator('[data-preview="mix"]').input_value() == "B1"
    assert page.locator('[data-preview="audio-status"]').inner_text() == "B1 audio ready"


def test_switching_mix_while_playing_preserves_position_and_playback(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="seek"]').fill("1")
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(250)
    before = page.locator('[data-preview="audio"]').evaluate("a => a.currentTime")

    page.locator('[data-preview="mix"]').select_option("B1")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("B1 audio ready")')
    page.wait_for_timeout(250)

    assert page.locator('[data-preview="play"]').inner_text() == "Pause"
    assert page.locator('[data-preview="audio"]').evaluate("a => a.currentTime") > before


def test_play_waits_until_the_selected_audio_is_ready(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="mix"]').select_option("S1")
    assert page.locator('[data-preview="play"]').is_disabled()
    assert "Preparing S1 audio" in page.locator('[data-preview="audio-status"]').inner_text()
    page.wait_for_selector('[data-preview="audio-status"]:has-text("S1 audio ready")')
    assert page.locator('[data-preview="play"]').is_enabled()


def test_audio_failure_keeps_the_picture_and_can_retry_the_same_mix(player):
    page, _ = player
    _enable_audio(page)
    pattern = "**/scroll-preview-audio?*mix=S1*"
    page.route(pattern, lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"audio failed"}'))
    page.locator('[data-preview="mix"]').select_option("S1")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("audio failed")')
    assert page.locator(".pvviewport > canvas").is_visible()
    assert page.locator('[data-preview="retry-audio"]').is_visible()

    page.unroute(pattern)
    page.locator('[data-preview="retry-audio"]').click()
    page.wait_for_selector('[data-preview="audio-status"]:has-text("S1 audio ready")')
    assert page.locator('[data-preview="retry-audio"]').is_hidden()


def test_audio_time_is_the_picture_clock(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="audio"]').evaluate(
        "a => { a.currentTime = 2.5; a.dispatchEvent(new Event('seeking')); }")
    assert _left(page) == pytest.approx(_left_for(250.0), abs=1)
    assert float(page.locator('[data-preview="seek"]').input_value()) == \
        pytest.approx(2.5)


def test_the_picture_finishes_its_tail_after_the_wav_ends(player):
    page, _ = player
    _enable_audio(page)
    page.locator('[data-preview="seek"]').fill("5.9")
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(900)
    assert float(page.locator('[data-preview="seek"]').input_value()) > 6.4

    page.locator('[data-preview="seek"]').fill("7.8")
    page.wait_for_timeout(350)
    assert float(page.locator('[data-preview="seek"]').input_value()) == \
        pytest.approx(8.0)
    assert page.locator('[data-preview="play"]').inner_text() == "Play"


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
    second = EVENTS[2]["marker"][0] + 10
    assert _pixel(page, second - left, 300) != _strip_colour(second)
    assert _pixel(page, EVENTS[0]["marker"][0] + 10 - left, 300) == \
        _strip_colour(EVENTS[0]["marker"][0] + 10)


def test_a_score_fingerprint_refresh_invalidates_the_prepared_preview(player):
    """A preview of a score that is no longer on disk is worse than none."""
    page, cleaned = player
    assert page.locator('[data-preview="time"]').inner_text() == "0:00 / 0:08"

    original = open(cleaned).read()
    try:
        with open(cleaned, "w") as fh:
            fh.write("<museScore><Score><Staff/></Score></museScore>")
        page.wait_for_selector(".pvviewport", state="detached", timeout=5000)
        assert "inputs changed" in page.locator(".pvstatus").inner_text()
        page.locator('[data-preview="open"]').click()
        page.wait_for_selector('[data-preview="time"]:has-text("0:05")')
    finally:
        with open(cleaned, "w") as fh:
            fh.write(original)
        page.wait_for_selector(".pvviewport", state="detached", timeout=5000)


def test_changing_preview_settings_invalidates_before_preparing_again(live, page):
    base, slug, _ = live
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if "/scroll-preview?" in request.url else None)
    _open_record(page, base, slug)
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    assert len(requests) == 1

    page.locator(".record-common select").select_option("720p")
    page.wait_for_selector(".pvviewport", state="detached")
    assert "inputs changed" in page.locator(".pvstatus").inner_text()
    assert len(requests) == 1

    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    assert len(requests) == 2
    assert "quality=720p" in requests[-1]


def test_changing_settings_during_preparation_waits_before_replacing_it(live, page):
    base, slug, _ = live
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if "/scroll-preview?" in request.url else None)
    _open_record(page, base, slug)
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (...args) => String(args[0]).includes('/scroll-preview?')
          ? new Promise((resolve, reject) => setTimeout(
              () => original(...args).then(resolve, reject), 500))
          : original(...args);
    }""")

    button = page.locator('[data-preview="open"]')
    button.click()
    page.locator(".record-common select").select_option("720p")
    assert button.is_disabled()
    assert len(requests) == 0
    page.wait_for_function("!document.querySelector('[data-preview=\"open\"]').disabled")
    assert page.locator(".pvviewport").count() == 0
    assert len(requests) == 1
    assert "quality=4k" in requests[0]

    button.click()
    page.wait_for_selector(".pvviewport > canvas")
    assert len(requests) == 2
    assert "quality=720p" in requests[-1]


def test_the_player_fits_a_phone(live, page):
    base, slug, _ = live
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if "/scroll-preview?" in request.url else None)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{slug}")
    # One pane at a time on a phone: the stage rail is behind the bottom bar.
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".stagebar .step", has_text="Record").click()
    page.wait_for_selector("text=Video style")
    page.locator(".mobilebar").get_by_role("button", name="Preview").click()
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    assert len(requests) == 1

    panel = page.locator(".pvviewport")
    assert panel.bounding_box()["width"] <= 390
    # The controls are on screen and usable, not off the side of the panel.
    for control in ("play", "restart", "seek", "audio-enabled"):
        assert page.locator(f'[data-preview="{control}"]').is_visible()
    assert page.locator('[data-preview="mix"]').is_hidden()
    assert page.locator('[data-preview="audio"]').is_hidden()
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(400)
    assert _left(page) > _left_for(SCROLL["xs"][0])
    if evidence := os.getenv("ISSUE_66_EVIDENCE_DIR"):
        # The evidence has to show the UI as it actually settles: the picture is
        # playable immediately and audio remains an explicit opt-in.
        assert page.locator('[data-preview="audio-enabled"]').is_visible()
        assert "Audio off" in \
            page.locator('[data-preview="audio-status"]').inner_text()
        os.makedirs(evidence, exist_ok=True)
        page.screenshot(path=os.path.join(evidence, "issue-66-preview-390.png"),
                        full_page=True)
    page.locator(".mobilebar").get_by_role("button", name="Record").click()
    assert page.locator(".pvviewport").count() == 1
    assert page.locator('[data-preview="play"]').inner_text() == "Play"
    page.locator(".mobilebar").get_by_role("button", name="Stages").click()
    page.locator(".mobilebar").get_by_role("button", name="Preview").click()
    assert page.locator(".pvviewport").is_visible()
    assert len(requests) == 1, "pane switching must reuse the prepared preview"


def test_hiding_preview_while_it_is_preparing_keeps_the_result(live, page):
    base, slug, _ = live
    requests = []
    page.on("request", lambda request: requests.append(request.url)
            if "/scroll-preview?" in request.url else None)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{slug}")
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (...args) => String(args[0]).includes('/scroll-preview?')
          ? new Promise((resolve, reject) => setTimeout(
              () => original(...args).then(resolve, reject), 500))
          : original(...args);
    }""")
    page.locator(".mobilebar").get_by_role("button", name="Preview").click()
    page.locator('[data-preview="open"]').click()
    page.locator(".mobilebar").get_by_role("button", name="Record").click()
    page.wait_for_timeout(800)

    assert page.locator(".pvviewport").count() == 1
    assert not page.locator(".pvviewport").is_visible()

    page.locator(".mobilebar").get_by_role("button", name="Preview").click()
    assert page.locator(".pvviewport").is_visible()
    assert len(requests) == 1


def _count(page, fragment):
    """How many requests for `fragment` the page has made since `_watch`."""
    return page.evaluate("f => window.__seen.filter(u => u.includes(f)).length",
                         fragment)


def _watch(page):
    """Record every request, and every object URL made and let go of.

    The object URLs are the honest way to ask whether the sound was really
    dropped: a preview that is torn down but still holds a blob URL is one that
    could be heard again, and nothing in the DOM would show it.
    """
    page.evaluate("""() => {
        window.__seen = [];
        window.__blobs = { made: 0, freed: 0 };
        const fetched = window.fetch;
        window.fetch = (...args) => { window.__seen.push(String(args[0])); return fetched(...args); };
        const make = URL.createObjectURL.bind(URL);
        const free = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = (b) => { window.__blobs.made++; return make(b); };
        URL.revokeObjectURL = (u) => { window.__blobs.freed++; return free(u); };
    }""")


def _blobs(page):
    return page.evaluate("() => window.__blobs")


def test_hiding_preview_pauses_the_sound_but_keeps_it_prepared(live, page):
    """Switching to Record to nudge a margin is the normal move, not a teardown."""
    base, slug, _ = live
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{base}/#/song/{slug}")
    _watch(page)
    page.locator(".mobilebar").get_by_role("button", name="Preview").click()
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    _enable_audio(page)
    page.locator('[data-preview="mix"]').select_option("S1")
    page.wait_for_selector('[data-preview="audio-status"]:has-text("S1 audio ready")')
    prepared = (_count(page, "/scroll-preview?"), _count(page, "/scroll-preview-audio?"))

    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(300)
    assert not page.locator('[data-preview="audio"]').evaluate("a => a.paused")

    page.locator(".mobilebar").get_by_role("button", name="Record").click()
    page.wait_for_timeout(300)
    audio = page.locator('[data-preview="audio"]')
    assert audio.evaluate("a => a.paused"), "hiding the pane must stop the sound"
    held = audio.evaluate("a => a.currentTime")
    assert held > 0
    assert page.locator('[data-preview="play"]').inner_text() == "Play"
    # Paused, not thrown away: the picture and the prepared WAV are both still here.
    page.wait_for_timeout(300)
    assert audio.evaluate("a => a.currentTime") == pytest.approx(held, abs=0.05)
    assert audio.evaluate("a => !!a.src")
    assert page.locator(".pvviewport").count() == 1

    page.locator(".mobilebar").get_by_role("button", name="Preview").click()
    page.locator('[data-preview="play"]').click()
    page.wait_for_timeout(300)
    assert not audio.evaluate("a => a.paused"), "coming back must resume, not re-prepare"
    assert audio.evaluate("a => a.currentTime") > held
    assert page.locator('[data-preview="mix"]').input_value() == "S1"
    assert (_count(page, "/scroll-preview?"), _count(page, "/scroll-preview-audio?")) \
        == prepared


def test_changing_a_setting_invalidates_the_sound_with_the_picture(live, page):
    """A stale mix is a wrong tempo or a wrong crop — it must not survive either."""
    base, slug, _ = live
    _open_record(page, base, slug)
    _watch(page)
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    _enable_audio(page)
    assert _blobs(page)["made"] == 1

    page.locator(".record-common select").select_option("720p")
    page.wait_for_selector(".pvviewport", state="detached")
    assert "inputs changed" in page.locator(".pvstatus").inner_text()
    assert page.locator('[data-preview="audio"]').count() == 0
    assert page.evaluate("() => document.querySelectorAll('audio').length") == 0
    assert _blobs(page) == {"made": 1, "freed": 1}, "the WAV must be let go of too"

    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    _enable_audio(page)
    assert "quality=720p" in page.evaluate(
        "() => window.__seen.filter(u => u.includes('/scroll-preview-audio?')).pop()")


def test_a_changed_score_invalidates_the_sound_with_the_picture(live, page):
    base, slug, cleaned = live
    _open_record(page, base, slug)
    _watch(page)
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    _enable_audio(page)

    original = open(cleaned).read()
    try:
        with open(cleaned, "w") as fh:
            fh.write("<museScore><Score><Staff/></Score></museScore>")
        page.wait_for_selector(".pvviewport", state="detached", timeout=5000)
        assert page.locator('[data-preview="audio"]').count() == 0
        assert _blobs(page) == {"made": 1, "freed": 1}

        page.locator('[data-preview="open"]').click()
        page.wait_for_selector('[data-preview="time"]:has-text("0:05")')
        _enable_audio(page)
    finally:
        with open(cleaned, "w") as fh:
            fh.write(original)
        page.wait_for_selector(".pvviewport", state="detached", timeout=5000)


def test_a_late_audio_response_cannot_reattach_to_an_invalidated_preview(live, page):
    """The WAV comes back after the preview it belongs to is already gone."""
    base, slug, _ = live
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    _open_record(page, base, slug)
    _watch(page)
    # Read the body straight away, then hold the response back. The sound is
    # therefore already fetched — and cannot be cancelled — when the preview it
    # was asked for is invalidated underneath it.
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = async (...args) => {
          const response = await original(...args);
          if (!String(args[0]).includes('/scroll-preview-audio?')) return response;
          const blob = await response.blob();
          await new Promise((r) => setTimeout(r, 700));
          return new Response(blob, { status: response.status });
        };
    }""")
    page.locator('[data-preview="open"]').click()
    page.wait_for_selector(".pvviewport > canvas")
    page.locator('[data-preview="audio-enabled"]').check()
    page.wait_for_selector('[data-preview="audio-status"]:has-text("Preparing ALL audio")')

    page.locator(".record-common select").select_option("720p")
    page.wait_for_selector(".pvviewport", state="detached")
    page.wait_for_timeout(1200)

    assert "inputs changed" in page.locator(".pvstatus").inner_text()
    assert page.evaluate("() => document.querySelectorAll('audio').length") == 0
    assert _blobs(page)["made"] == _blobs(page)["freed"], "a late WAV was kept"
    assert not errors, f"the late response raised: {errors}"


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
