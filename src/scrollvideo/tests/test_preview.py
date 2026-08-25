"""The browser preview is the render, minus the encoder.

Every test here is a way of asking the same question: could what a singer sees in
the browser differ from what comes out of the encoder? The preview is only worth
having if the answer stays no, so these pin it to `build.prepare` and `build.raster`
— the clock, the scroll curve, the viewport, the symbols that light up, the pixels
themselves, and the refusals — rather than to what the payload happens to contain.

The pixel tests are the point of the payload's present shape. While the browser was
given the engraving as SVG and left to draw it, no test could have caught the thing
that was actually wrong: verovio writes lyrics and measure numbers as
`font-family="Times, serif"`, and a browser and cairosvg do not choose the same
serif. `test_the_strip_is_the_renderers_own_pixels` is what closes that door.
"""

import os
import tempfile

import numpy as np
import pytest
from lxml import etree
from PIL import Image

from src.scrollvideo import build, preview as preview_mod, video as video_mod
from src.scrollvideo.preview import PREVIEW_HEIGHT, preview
from src.scrollvideo.video import BAND_ALPHA, HIGHLIGHT

from .conftest import FILES, needs_ffmpeg, needs_musescore

pytestmark = needs_musescore

SIZE = dict(width=1920, height=1080, fps=30)


def _prepared(path, **kwargs):
    """What the renderer would work from, for the same score and settings."""
    with tempfile.TemporaryDirectory() as tmp:
        return build.prepare(path, tmp, fps=SIZE["fps"], **kwargs)


def _joined(payload, out_dir, part="strip"):
    """The tiles put back together, as the strip the renderer drew."""
    tiles = payload[part]["tiles"]
    images = [np.asarray(Image.open(os.path.join(out_dir, tile["name"])))
              for tile in tiles]
    assert [tile["x"] for tile in tiles] == list(np.cumsum([0] + [i.shape[1]
                                                                  for i in images])[:-1])
    return np.concatenate(images, axis=1)


@pytest.fixture(scope="module")
def prepared_preview(tmp_path_factory):
    """Prepared once: it costs a MuseScore conversion, an engraving and a raster."""
    out = str(tmp_path_factory.mktemp("preview"))
    return preview(os.path.join(FILES, "fermata.mscx"), out, **SIZE), out


@pytest.fixture
def payload(prepared_preview):
    return prepared_preview[0]


@pytest.fixture
def repeat_mscx():
    """The fermata score with its two bars written as a repeated section."""
    return os.path.join(FILES, "repeat.mscx")


def _px(ready):
    """Pixels per verovio unit at the preview's height — `build.raster`'s own scale."""
    strip_height = int(round(PREVIEW_HEIGHT * ready.layout.height / ready.view_height))
    return strip_height / ready.layout.height


def test_the_strip_is_the_renderers_own_pixels(fermata_mscx, prepared_preview):
    """Not a second drawing of the same score — the identical pixels.

    This is the whole reason the preview sends pictures instead of the engraving:
    the words are drawn by whatever draws them, and only one drawing can be the
    one the video will contain.
    """
    payload, out_dir = prepared_preview
    ready = _prepared(fermata_mscx)
    drawn = build.raster(ready, PREVIEW_HEIGHT)

    strip = _joined(payload, out_dir)
    assert strip.shape == drawn.strip.shape
    assert np.array_equal(strip, drawn.strip)
    assert payload["strip"]["width"] == drawn.width
    assert payload["frame"] == {"width": round(PREVIEW_HEIGHT * SIZE["width"]
                                               / SIZE["height"]),
                                "height": PREVIEW_HEIGHT}


def test_a_lit_symbol_is_the_blue_the_renderer_paints(fermata_mscx, prepared_preview):
    """The colour is not the browser's to choose, so it is not the browser's to get wrong.

    Every playable glyph arrives already repainted, by the same call `video.render`
    makes; the player copies a box out of it and adds nothing.
    """
    payload, out_dir = prepared_preview
    drawn = build.raster(_prepared(fermata_mscx), PREVIEW_HEIGHT)
    lit = _joined(payload, out_dir, "lit")

    assert lit.shape[2] == 4                                  # transparent elsewhere
    covered = drawn.coverage > 0
    assert covered.any()
    assert np.array_equal(lit[:, :, 3] > 0, covered)
    assert np.array_equal(lit[:, :, :3][covered],
                          video_mod.lit_pixels(drawn.coverage)[covered])

    # Fully covered pixels are the highlight itself, not a blend with the engraving.
    solid = drawn.coverage == 255
    if solid.any():
        assert np.array_equal(np.unique(lit[:, :, :3][solid].reshape(-1, 3), axis=0),
                              np.array([HIGHLIGHT], dtype=np.uint8))


@needs_ffmpeg
def test_a_previewed_frame_is_the_frame_the_renderer_would_write(fermata_mscx,
                                                                 prepared_preview,
                                                                 monkeypatch, tmp_path):
    """The payload, composed the way the player composes it, against a real frame.

    `video.render` is run for real at the preview's size, writing raw pixels rather
    than an encoded file so the comparison is not arguing with a codec. What the
    test then builds out of the payload is the three steps the player takes — the
    window, the marker band, the lit boxes — and they have to agree.
    """
    payload, out_dir = prepared_preview
    monkeypatch.setattr(video_mod, "_video_encoder_args",
                        lambda *_a, **_k: ["-c:v", "rawvideo", "-f", "rawvideo"])

    ready = _prepared(fermata_mscx)
    drawn = build.raster(ready, PREVIEW_HEIGHT)
    frame_width, fps = payload["frame"]["width"], 4
    raw = tmp_path / "frames.raw"
    video_mod.render(drawn.strip, drawn.placed, ready.anchors, str(raw),
                     px_per_unit=drawn.px_per_unit, width=frame_width, fps=fps,
                     coverage=drawn.coverage, duration=3.0)

    size = frame_width * PREVIEW_HEIGHT * 3
    frames = np.fromfile(raw, dtype=np.uint8).reshape(-1, PREVIEW_HEIGHT, frame_width, 3)
    assert frames.shape[0] >= 8 and frames[0].size == size

    strip = _joined(payload, out_dir)
    lit = _joined(payload, out_dir, "lit")
    for index in range(0, 8):
        composed = _compose(payload, strip, lit, index / fps)
        # The band is the one place the browser does its own arithmetic (canvas
        # alpha rather than the renderer's rounded integer), so a unit of rounding
        # is allowed and nothing else is.
        assert np.allclose(composed, frames[index], atol=1)


def _compose(payload, strip, lit, t):
    """One frame, exactly as `scroll_preview.js` builds it. Kept in step by the test above."""
    frame_width, height = payload["frame"]["width"], payload["frame"]["height"]
    scroll = payload["scroll"]
    xs, times, jump = scroll["xs"], scroll["times"], scroll["jump"]
    index = int(np.searchsorted(times, t, side="right")) - 1
    if index >= len(times) - 1:
        position = xs[-1]
    else:
        step = xs[index + 1] - xs[index]
        span = times[index + 1] - times[index]
        position = xs[index] if step < -jump else (
            xs[index] + step * (t - times[index]) / span if span > 0 else xs[index])
    left = int(position - payload["playhead"] * frame_width)

    frame = np.full((height, frame_width, 3), 255, dtype=np.uint8)
    src0, src1 = max(0, left), min(strip.shape[1], left + frame_width)
    if src1 > src0:
        frame[:, src0 - left:src1 - left] = strip[:, src0:src1]

    active = [e for e in payload["events"] if e["on"] <= t < e["off"]]
    band = next((e["marker"] for e in reversed(payload["events"])
                 if e["on"] <= t and e["marker"]), None)
    if band:
        x0, x1 = max(band[0] - left, 0), min(band[1] - left, frame_width)
        if x1 > x0:
            frame[:, x0:x1] = (frame[:, x0:x1] * (1 - BAND_ALPHA)
                               + np.array(HIGHLIGHT) * BAND_ALPHA).astype(np.uint8)

    for e in active:
        x0, x1 = max(e["x0"] - left, 0), min(e["x1"] - left, frame_width)
        if x1 <= x0:
            continue
        box = lit[e["y0"]:e["y1"], e["x0"] + (x0 - (e["x0"] - left)):e["x0"] + (x1 - (e["x0"] - left))]
        opaque = box[:, :, 3] > 0
        frame[e["y0"]:e["y1"], x0:x1][opaque] = box[:, :, :3][opaque]
    return frame


def test_the_scroll_curve_is_the_renderers_own(fermata_mscx, payload):
    """Not a similar curve computed the same way — the identical numbers.

    Anything else is a second implementation of the scrolling, which is exactly
    what the preview exists to avoid. The payload states them in strip pixels,
    which is what `video.render` interpolates in too.
    """
    ready = _prepared(fermata_mscx)
    times, xs = ready.anchors
    px = _px(ready)
    assert payload["scroll"]["times"] == [round(t, 3) for t in times]
    assert payload["scroll"]["xs"] == [round(x * px, 2) for x in xs]

    # And so the position at a moment agrees, which is what is actually looked at.
    for moment in np.linspace(0, ready.duration, 25):
        rendered = float(np.interp(moment, times, xs)) * px
        previewed = float(np.interp(moment, payload["scroll"]["times"],
                                    payload["scroll"]["xs"]))
        assert abs(rendered - previewed) < 1.0


def test_a_fermata_is_timed_on_musescores_clock(fermata_mscx, payload):
    """The whole reason the video keeps time: verovio's own timestamps are wrong.

    Measure 1 ends on a chord stretched three times over, which MuseScore plays
    and verovio does not know about. A preview on verovio's clock would run a
    second short and drift away from the audio it is previewing.
    """
    ready = _prepared(fermata_mscx)
    verovio_seconds = max(float(entry.get("tstamp", 0.0))
                          for entry in ready.engraving.timemap) / 1000.0
    assert payload["duration"] == round(ready.duration, 3)
    assert payload["duration"] - build.TAIL_SECONDS > verovio_seconds + 0.5


def test_margins_move_the_preview_exactly_as_they_move_the_video(fermata_mscx, tmp_path):
    """The frame is cut out of the strip, so the margins are already in the pixels."""
    margins = dict(top_margin_percent=10.0, bottom_margin_percent=-20.0)
    out = str(tmp_path / "margins")
    payload = preview(fermata_mscx, out, **SIZE, **margins)
    drawn = build.raster(_prepared(fermata_mscx, **margins), PREVIEW_HEIGHT)
    assert np.array_equal(_joined(payload, out), drawn.strip)


def test_a_bottom_margin_shows_white_and_not_the_hidden_spacer_staff(fermata_mscx,
                                                                     tmp_path):
    """The spacer staff is engraved to space the bars and then cropped away.

    Adding bottom margin must reveal white — the renderer is explicit about it —
    and this is where the SVG preview and the video really did disagree: the
    browser was given the whole page and simply widened its window onto it, so a
    margin someone nudged put a staff of rests on screen that no video would ever
    contain.
    """
    out = str(tmp_path / "bottom")
    payload = preview(fermata_mscx, out, **SIZE, bottom_margin_percent=25.0)
    strip = _joined(payload, out)

    ready = _prepared(fermata_mscx, bottom_margin_percent=25.0)
    music_rows = int(round(ready.visible_height * _px(ready)))
    below = strip[music_rows + 2:]
    assert below.size and (below == 255).all()


def test_the_symbols_that_light_up_are_the_ones_the_renderer_lights(fermata_mscx,
                                                                    payload):
    ready = _prepared(fermata_mscx)
    drawn = build.raster(ready, PREVIEW_HEIGHT)
    assert [e["id"] for e in payload["events"]] == [p.note_id for p in drawn.placed]
    assert [e["on"] for e in payload["events"]] == [round(p.on, 3) for p in drawn.placed]
    assert [(e["x0"], e["x1"], e["y0"], e["y1"]) for e in payload["events"]] == \
        [(p.x0, p.x1, p.y0, p.y1) for p in drawn.placed]

    # A whole-measure rest is not a beat, so it does not drag the marker onto
    # itself — the same rule `video.place` applies.
    assert [bool(e["marker"]) for e in payload["events"]] == \
        [p.snap_marker for p in drawn.placed]


def test_a_repeated_section_jumps_back_instead_of_sliding(repeat_mscx, tmp_path):
    """A repeat is real backward motion, and it survives into the payload.

    The player is told how big a step counts as one (`scroll.jump`) so it can land
    on the far side rather than interpolate across the music in between — which
    would scroll the whole section backwards in front of a singer.
    """
    payload = preview(repeat_mscx, str(tmp_path / "repeat"), **SIZE)
    xs = payload["scroll"]["xs"]
    threshold = payload["scroll"]["jump"]
    jumps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1) if xs[i + 1] - xs[i] < -threshold]
    assert len(jumps) == 1

    # The repeated bars are played twice and drawn once, so the same symbols light
    # up on both passes.
    ids = [e["id"] for e in payload["events"]]
    assert len(ids) > len(set(ids))


def test_it_refuses_what_a_render_would_refuse(tmp_path, fermata_mscx):
    """Failing here is the point: seconds, rather than minutes into an encode."""
    score = etree.parse(fermata_mscx)
    measure = score.getroot().find(".//Staff/Measure")
    etree.SubElement(etree.SubElement(measure, "Jump"), "jumpTo").text = "start"
    path = tmp_path / "jump.mscx"
    score.write(str(path))
    with pytest.raises(NotImplementedError, match="Jump"):
        preview(str(path), str(tmp_path / "out"), **SIZE)

    with pytest.raises(ValueError, match="margin"):
        preview(fermata_mscx, str(tmp_path / "out"), **SIZE, top_margin_percent=500.0)


def test_nothing_is_encoded_and_no_voice_is_mixed(fermata_mscx, tmp_path, monkeypatch):
    """Preview draws the picture; it is the minutes after that it stands in for.

    Rasterising is the cheap half and the preview does it — that is what makes the
    pixels the renderer's. Encoding a frame or rendering a voice's audio is not.
    """
    def refuse(*_args, **_kwargs):
        raise AssertionError("preview must not encode or render audio")

    monkeypatch.setattr("src.scrollvideo.video.render", refuse)
    monkeypatch.setattr("src.scrollvideo.video.mux", refuse)
    monkeypatch.setattr("src.scrollvideo.audio.render_mix", refuse)
    out = str(tmp_path / "out")
    preview(fermata_mscx, out, **SIZE)
    # And nothing is left behind but the picture.
    assert all(name.endswith(".png") for name in os.listdir(out))


def test_a_long_score_is_sent_as_tiles_a_browser_can_decode(fermata_mscx, tmp_path,
                                                            monkeypatch):
    """Chrome refuses an image past 16384px on a side, and a score gets that wide."""
    monkeypatch.setattr(preview_mod, "TILE_WIDTH", 200)
    out = str(tmp_path / "tiled")
    payload = preview(fermata_mscx, out, **SIZE)
    tiles = payload["strip"]["tiles"]
    assert len(tiles) > 1
    assert all(tile["width"] <= 200 for tile in tiles)
    assert np.array_equal(_joined(payload, out),
                          build.raster(_prepared(fermata_mscx), PREVIEW_HEIGHT).strip)


def test_the_playhead_is_the_renderers(payload):
    """Where the sung note sits across the frame is a render decision, not a UI one."""
    assert payload["playhead"] == preview_mod.PLAYHEAD
