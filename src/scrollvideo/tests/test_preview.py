"""The browser preview is the render, minus the pixels.

Every test here is a way of asking the same question: could what a singer sees in
the browser differ from what comes out of the encoder? The preview is only worth
having if the answer stays no, so these pin it to `build.prepare` — the clock, the
scroll curve, the viewport, the symbols that light up, and the refusals — rather
than to what the payload happens to contain.
"""

import os
import tempfile

import numpy as np
import pytest
from lxml import etree

from src.scrollvideo import build, preview as preview_mod
from src.scrollvideo.geometry import SVG_NS
from src.scrollvideo.preview import preview, preview_svg
from src.scrollvideo.video import place

from .conftest import FILES, needs_musescore

pytestmark = needs_musescore

SIZE = dict(width=1920, height=1080, fps=30)


def _prepared(path, **kwargs):
    """What the renderer would work from, for the same score and settings."""
    with tempfile.TemporaryDirectory() as tmp:
        return build.prepare(path, tmp, fps=SIZE["fps"], **kwargs)


@pytest.fixture(scope="module")
def payload():
    """Prepared once: it costs a MuseScore conversion and an engraving."""
    return preview(os.path.join(FILES, "fermata.mscx"), **SIZE)


@pytest.fixture
def repeat_mscx():
    """The fermata score with its two bars written as a repeated section."""
    return os.path.join(FILES, "repeat.mscx")


def test_the_scroll_curve_is_the_renderers_own(fermata_mscx, payload):
    """Not a similar curve computed the same way — the identical numbers.

    Anything else is a second implementation of the scrolling, which is exactly
    what the preview exists to avoid.
    """
    ready = _prepared(fermata_mscx)
    times, xs = ready.anchors
    assert payload["scroll"]["times"] == [round(t, 3) for t in times]
    assert payload["scroll"]["xs"] == [round(x, 2) for x in xs]

    # And so the position at a moment agrees, which is what is actually looked at.
    # The payload rounds time to the millisecond, so allow the distance the page
    # travels in half of one — well under a pixel of the frame it is compared to.
    pixel = (ready.view_height * SIZE["width"] / SIZE["height"]) / SIZE["width"]
    for moment in np.linspace(0, ready.duration, 25):
        rendered = float(np.interp(moment, times, xs))
        previewed = float(np.interp(moment, payload["scroll"]["times"],
                                    payload["scroll"]["xs"]))
        assert abs(rendered - previewed) < pixel


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


def test_the_viewport_is_the_frame_the_renderer_would_crop(fermata_mscx, payload):
    """Margins move the preview's window exactly as they move the video's."""
    assert payload["view"]["start"] == 0.0
    assert payload["view"]["end"] == round(_prepared(fermata_mscx).visible_height, 2)
    # The spacer staff is engraved and cropped off, so the window is shorter than
    # the page it is cut from.
    assert payload["view"]["end"] < payload["page"]["height"]
    assert payload["view"]["aspect"] == SIZE["width"] / SIZE["height"]

    margins = dict(top_margin_percent=10.0, bottom_margin_percent=-20.0)
    with_margins = preview(fermata_mscx, **SIZE, **margins)
    ready = _prepared(fermata_mscx, **margins)
    assert with_margins["view"]["start"] == round(ready.view_start, 2)
    assert with_margins["view"]["end"] == round(ready.view_end, 2)
    assert with_margins["view"]["start"] < 0  # white space added above the music


def test_the_symbols_that_light_up_are_the_ones_the_renderer_lights(fermata_mscx,
                                                                    payload):
    ready = _prepared(fermata_mscx)
    placed = place(ready.events, ready.layout, 1.0, staff_limit=ready.singing_staves)
    assert [e["id"] for e in payload["events"]] == [p.note_id for p in placed]
    assert [e["on"] for e in payload["events"]] == [round(p.on, 3) for p in placed]

    # Every id names something the browser can actually find in the page.
    svg = etree.fromstring(payload["svg"].encode())
    drawn = {node.get("id") for node in svg.iter() if node.get("id")}
    assert {e["id"] for e in payload["events"]} <= drawn

    # A whole-measure rest is not a beat, so it does not drag the marker onto
    # itself — the same rule `video.place` applies.
    assert [bool(e["marker"]) for e in payload["events"]] == [p.snap_marker for p in placed]


def test_a_repeated_section_jumps_back_instead_of_sliding(repeat_mscx):
    """A repeat is real backward motion, and it survives into the payload.

    The player is told how big a step counts as one (`scroll.jump`) so it can land
    on the far side rather than interpolate across the music in between — which
    would scroll the whole section backwards in front of a singer.
    """
    payload = preview(repeat_mscx, **SIZE)
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
        preview(str(path), **SIZE)

    with pytest.raises(ValueError, match="margin"):
        preview(fermata_mscx, **SIZE, top_margin_percent=500.0)


def test_nothing_is_rasterised_or_encoded(fermata_mscx, monkeypatch):
    """Preview is the cheap half of the pipeline, and has to stay that way."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("preview must not rasterise or encode")

    monkeypatch.setattr("src.scrollvideo.geometry.rasterise", refuse)
    monkeypatch.setattr("src.scrollvideo.geometry.playing_coverage", refuse)
    monkeypatch.setattr("src.scrollvideo.video.render", refuse)
    preview(fermata_mscx, **SIZE)


def test_the_svg_is_addressable_in_the_units_the_curve_is_written_in(payload):
    """The page's own coordinates, so moving the window is setting a viewBox.

    Verovio sizes the root in pixels a twenty-fifth of the coordinates inside it,
    and the engraving sizes itself as a percentage of whatever encloses it — so
    left alone it would shrink and stretch every time the window moved.
    """
    root = etree.fromstring(payload["svg"].encode())
    page = payload["page"]
    assert root.get("viewBox") == f"0 0 {page['width']:g} {page['height']:g}"
    nested = root.find(f"{{{SVG_NS}}}svg")
    assert nested.get("width") == f"{page['width']:g}"
    assert nested.get("height") == f"{page['height']:g}"


def test_preview_svg_leaves_a_page_it_cannot_read_alone():
    """Not a verovio page: nothing to rewrite, and nothing pretended."""
    layout = type("L", (), {"width": 100.0, "height": 40.0})()
    svg = preview_svg('<svg xmlns="http://www.w3.org/2000/svg" width="4px"/>', layout)
    root = etree.fromstring(svg.encode())
    assert root.get("viewBox") == "0 0 100 40"
    assert root.find(f"{{{SVG_NS}}}svg") is None


def test_the_playhead_is_the_renderers(payload):
    """Where the sung note sits across the frame is a render decision, not a UI one."""
    assert payload["playhead"] == preview_mod.PLAYHEAD
