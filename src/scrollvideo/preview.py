"""The same render, as data a browser can play instead of a file ffmpeg must write.

Looking at a scrolling video is how anyone finds out whether the picture is right:
the margins, the staff size, where the beat marker sits, whether a repeat lands
back on the right bar. Finding that out cost a full render — minutes of engraving,
rasterising, encoding and audio — for a question about layout.

This turns `build.prepare` into a payload the browser can animate: the engraving as
SVG, the viewport the frame would show, the scroll curve the renderer would follow,
and when each symbol lights up. Nothing here decides anything; every number comes
out of the same preparation the video is made from, so a preview that looks right
is a render that will look right.

What the browser is left to do is interpolate the curve at the current time and move
the SVG window. It does no scrolling, timing or layout arithmetic of its own.
"""

from __future__ import annotations

import tempfile
from typing import Callable, Dict, List, Optional

from lxml import etree

from . import spacing as spacing_mod
from .build import prepare
from .geometry import SVG_NS, Layout
from .timing import JUMP_FRACTION, SMOOTH_SECONDS
from .video import BAND_ALPHA, HIGHLIGHT, Placed, place

Logger = Callable[[str], None]

# Where the sung note sits across the frame — `video.render`'s own default. The
# browser needs it to turn the scroll curve into a window on the page.
PLAYHEAD = 0.35

# Seconds are rounded to a millisecond and page positions to a hundredth of a
# verovio unit (~a thousandth of a staff line). Both are far finer than anything
# visible, and rounding keeps the payload a fraction of the size on a phone.
TIME_DP = 3
UNIT_DP = 2


def _noop(_message: str) -> None:
    pass


def preview_svg(svg: str, layout: Layout) -> str:
    """The engraving with its root put into score units, ready to scroll in a page.

    Verovio sizes the root in pixels a twenty-fifth of the coordinates everything
    inside it uses, so a browser given the file as-is cannot address a position on
    the page in the units the scroll curve is written in. Giving the root the unit
    viewBox makes one user unit one unit everywhere, and then showing a moment of
    the music is just setting the viewBox to that window — no transforms to keep in
    step with the scroll, and the nested engraving is untouched.
    """
    root = etree.fromstring(svg.encode())
    root.set("viewBox", f"0 0 {layout.width:g} {layout.height:g}")
    root.set("width", "100%")
    root.set("height", "100%")
    root.set("preserveAspectRatio", "none")

    # The engraving is a nested `<svg>` that sizes itself as a percentage of
    # whatever viewport encloses it. That viewport is the root's viewBox, which the
    # player rewrites every frame to move the window — so left as a percentage the
    # engraving would shrink and stretch along with the window instead of standing
    # still on the page. Stating its size in units pins it.
    for nested in root.iter(f"{{{SVG_NS}}}svg"):
        if nested is not root:
            nested.set("width", f"{layout.width:g}")
            nested.set("height", f"{layout.height:g}")
    return etree.tostring(root, encoding="unicode")


def _marker_band(marker: Placed) -> List[float]:
    """The beat marker's horizontal band, the width `video.blend_beat_marker` uses."""
    note_width = max(marker.x1 - marker.x0, 1e-9)
    centre = (marker.x0 + marker.x1) / 2
    return [round(centre - note_width, UNIT_DP), round(centre + note_width, UNIT_DP)]


def _events(ready, layout: Layout) -> List[Dict]:
    """Every symbol that lights up, when, and where it is on the page.

    Built through `video.place` at one pixel per unit, so the preview highlights
    exactly the symbols the renderer does — including the rule that a whole-measure
    rest does not drag the beat marker onto itself.
    """
    return [{
        "id": item.note_id,
        "on": round(item.on, TIME_DP),
        "off": round(item.off, TIME_DP),
        "staff": item.staff,
        "marker": _marker_band(item) if item.snap_marker else None,
    } for item in place(ready.events, layout, 1.0, staff_limit=ready.singing_staves)]


def preview(mscx_path: str, *, width: int = 3840, height: int = 2160, fps: int = 60,
            keep_silent: bool = False, initial_bpm: Optional[int] = None,
            spacer_per_quarter: int = spacing_mod.DEFAULT_PER_QUARTER,
            smooth_seconds: float = SMOOTH_SECONDS,
            top_margin_percent: float = 0.0, bottom_margin_percent: float = 0.0,
            log: Logger = _noop) -> Dict:
    """Everything a browser needs to play this render, without rendering it.

    Raises what a render would raise, and for the same reasons: a D.C./D.S. jump the
    engraving cannot follow, margins that leave no picture, a timeline too far out of
    step with the audio. Failing here is the point — it costs seconds instead of the
    minutes it takes to discover the same thing from a finished file.
    """
    with tempfile.TemporaryDirectory() as tmp:
        ready = prepare(mscx_path, tmp, keep_silent=keep_silent,
                        initial_bpm=initial_bpm, spacer_per_quarter=spacer_per_quarter,
                        smooth_seconds=smooth_seconds, fps=fps,
                        top_margin_percent=top_margin_percent,
                        bottom_margin_percent=bottom_margin_percent, log=log)
        layout = ready.layout
        times, xs = ready.anchors
        return {
            "svg": preview_svg(ready.engraving.svg, layout),
            "page": {"width": round(layout.width, UNIT_DP),
                     "height": round(layout.height, UNIT_DP)},
            # The slice of the page a frame shows, in the same units, already
            # including the spacer-staff crop and the margin adjustments.
            "view": {"start": round(ready.view_start, UNIT_DP) + 0.0,
                     "end": round(ready.view_end, UNIT_DP),
                     "height": round(ready.view_height, UNIT_DP),
                     "aspect": width / height},
            "playhead": PLAYHEAD,
            "duration": round(ready.duration, TIME_DP),
            "fps": fps,
            "scroll": {"times": [round(t, TIME_DP) for t in times],
                       "xs": [round(x, UNIT_DP) for x in xs],
                       # How far back the page has to go before it counts as a
                       # repeat rather than spacing noise — the same line
                       # `smooth_scroll` draws. A step past it is a jump, and the
                       # player must land on the far side of it rather than slide
                       # across the music in between.
                       "jump": round(JUMP_FRACTION * layout.width, UNIT_DP)},
            "events": _events(ready, layout),
            "highlight": {"colour": "#%02x%02x%02x" % HIGHLIGHT,
                          "marker_alpha": BAND_ALPHA},
            "parts": list(ready.names),
            "dropped": list(ready.dropped),
        }
