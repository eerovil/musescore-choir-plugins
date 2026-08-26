"""The same render, as pictures a browser can scroll instead of a file ffmpeg must write.

Looking at a scrolling video is how anyone finds out whether the picture is right:
the margins, the staff size, where the beat marker sits, whether a repeat lands
back on the right bar. Finding that out cost a full render — minutes of engraving,
rasterising, encoding and audio — for a question about layout.

So this stops one step short of the encoder. `build.prepare` decides the render and
`build.raster` draws it, both exactly as `build_videos` does, only at a height a web
page can carry; what comes back is that drawing as PNG tiles plus the numbers the
player needs. **The pixels are the renderer's own.** An earlier version sent the
engraving as SVG and let the browser draw it, which was cheaper and looked wrong in
a way nobody could act on: verovio writes lyrics and measure numbers as
`font-family="Times, serif"`, cairosvg resolves that against this host's fonts and a
browser against its own, so the words in the preview were never the words in the
video. Handing over pixels ends that whole class of difference — a preview is now
the video's own frames, at a smaller size and without the sound.

What the browser is left to do is interpolate the scroll curve, copy the window out
of the strip, and copy each sounding symbol's box out of a second, pre-lit strip. It
works out nothing about music, layout, time or colour.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Callable, Dict, List, Optional

import numpy as np
from PIL import Image

from . import spacing as spacing_mod
from .build import Raster, prepare, raster
from .timing import JUMP_FRACTION, SMOOTH_SECONDS
from .video import (BACKGROUND_ALPHA, BAND_ALPHA, FOCUS_ALPHA, HIGHLIGHT, Placed,
                    lit_pixels)

Logger = Callable[[str], None]

# Where the sung note sits across the frame — `video.render`'s own default. The
# browser needs it to turn the scroll curve into a window on the strip.
PLAYHEAD = 0.35

# How tall the preview is drawn. Not the video's height: 2160 rows of a whole score
# is hundreds of megabytes to send to a phone, and the panel showing it is a few
# hundred pixels wide. This is the same picture at a size a page can hold, sharp
# enough on a desktop panel that the browser is scaling it down rather than up.
PREVIEW_HEIGHT = 480

# The exact score `build.prepare` gives MuseScore for the final video's audio:
# silent parts removed and an opening BPM inserted when the app supplied one.
# Keeping a copy beside the preview lets mixes be rendered lazily after the
# expensive preparation has finished, without inventing a second preparation path.
AUDIO_SOURCE = "audio-source.mscx"

# Cairo caps surface dimensions and so do browsers: Chrome refuses images past
# 16384px on a side, and a long score is wider than that even at this height. The
# strip is therefore sent as tiles, which is what `geometry.rasterise` already does
# internally to draw it.
TILE_WIDTH = 4096

# Event times and total duration only choose musical state, so milliseconds are
# plenty there. Scroll anchors are different: their interpolation is truncated to
# an integer source pixel by both the renderer and browser. Rounding an anchor before
# interpolation can therefore move the preview by one pixel even when the numeric
# error is tiny, so the curve itself is sent at full float precision.
TIME_DP = 3


def _noop(_message: str) -> None:
    pass


def _tiles(array: np.ndarray, out_dir: str, prefix: str, mode: str) -> List[Dict]:
    """Write a strip out as PNG tiles and describe where each one starts."""
    tiles = []
    total = array.shape[1]
    for index, x in enumerate(range(0, total, TILE_WIDTH)):
        width = min(TILE_WIDTH, total - x)
        name = f"{prefix}-{index}.png"
        Image.fromarray(array[:, x:x + width], mode).save(os.path.join(out_dir, name))
        tiles.append({"name": name, "x": x, "width": width})
    return tiles


def lit_strip(drawn: Raster, strength: float = FOCUS_ALPHA) -> np.ndarray:
    """The whole strip with every playable symbol already repainted blue.

    Which symbol is sounding changes forty times a second; what a sounding symbol
    looks like does not. So it is drawn once, here, with `video.render`'s own
    arithmetic, and the player copies a box out of it. Everywhere else is
    transparent, which costs almost nothing in a PNG and leaves the engraving
    showing through.

    This is why the preview cannot invent a colour: it is not given one. It is given
    the pixels the encoder would have written.
    """
    covered = drawn.coverage > 0
    alpha = np.where(covered, 255, 0).astype(np.uint8)
    return np.dstack([lit_pixels(drawn.coverage, strength), alpha])


def marker_band(marker: Placed) -> List[int]:
    """The beat marker's horizontal band — `video.blend_beat_marker`'s own width."""
    note_width = max(1, marker.x1 - marker.x0)
    band = 2 * note_width
    centre = (marker.x0 + marker.x1) / 2
    x0 = int(round(centre - band / 2))
    return [x0, x0 + band]


def _events(drawn: Raster) -> List[Dict]:
    """Every symbol that lights up: when, and which box to copy from the lit strip."""
    return [{
        "id": item.note_id,
        "on": round(item.on, TIME_DP),
        "off": round(item.off, TIME_DP),
        "staff": item.staff,
        "x0": item.x0, "x1": item.x1, "y0": item.y0, "y1": item.y1,
        "marker": marker_band(item) if item.snap_marker else None,
    } for item in drawn.placed]


def preview(mscx_path: str, out_dir: str, *, width: int = 3840, height: int = 2160,
            fps: int = 60, keep_silent: bool = False, initial_bpm: Optional[int] = None,
            spacing_ratio: float = spacing_mod.DEFAULT_MAX_RATIO,
            smooth_seconds: float = SMOOTH_SECONDS,
            top_margin_percent: float = 0.0, bottom_margin_percent: float = 0.0,
            preview_height: int = PREVIEW_HEIGHT, log: Logger = _noop) -> Dict:
    """Draw this render as tiles in `out_dir` and return what a player needs with them.

    `width`/`height` are the video's, not the preview's: they set the shape of the
    frame, and the preview is that shape drawn `preview_height` tall.

    Raises what a render would raise, and for the same reasons: a D.C./D.S. jump the
    engraving cannot follow, margins that leave no picture, a timeline too far out of
    step with the audio. Failing here is the point — it costs seconds instead of the
    minutes it takes to discover the same thing from a finished file.
    """
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        ready = prepare(mscx_path, tmp, keep_silent=keep_silent,
                        initial_bpm=initial_bpm, spacing_ratio=spacing_ratio,
                        smooth_seconds=smooth_seconds, fps=fps,
                        top_margin_percent=top_margin_percent,
                        bottom_margin_percent=bottom_margin_percent, log=log)
        drawn = raster(ready, preview_height, log)
        frame_width = max(1, int(round(preview_height * width / height)))

        log("Writing the preview tiles")
        strip_tiles = _tiles(drawn.strip, out_dir, "strip", "RGB")
        lit_tiles = _tiles(lit_strip(drawn), out_dir, "lit", "RGBA")
        background_tiles = _tiles(lit_strip(drawn, BACKGROUND_ALPHA), out_dir,
                                  "background", "RGBA")
        shutil.copyfile(ready.source, os.path.join(out_dir, AUDIO_SOURCE))

        times, xs = ready.anchors
        return {
            # The frame the player draws, and the strip it cuts it out of.
            "frame": {"width": frame_width, "height": preview_height},
            "strip": {"width": drawn.width, "tiles": strip_tiles},
            "lit": {"tiles": lit_tiles},
            "background": {"tiles": background_tiles},
            "playhead": PLAYHEAD,
            "duration": round(ready.duration, TIME_DP),
            "fps": fps,
            # In strip pixels, so the player interpolates the exact curve and
            # subtracts the playhead exactly as `video.render` does. Cast NumPy
            # scalars to ordinary floats for JSON without quantising them.
            "scroll": {"times": [float(t) for t in times],
                       "xs": [float(x * drawn.px_per_unit) for x in xs],
                       # How far back the page has to go before it counts as a
                       # repeat rather than spacing noise — the same line
                       # `smooth_scroll` draws. A step past it is a jump, and the
                       # player must land on the far side of it rather than slide
                       # across the music in between.
                       "jump": float(JUMP_FRACTION * ready.layout.width
                                     * drawn.px_per_unit)},
            "events": _events(drawn),
            "highlight": {"colour": "#%02x%02x%02x" % HIGHLIGHT,
                          "marker_alpha": BAND_ALPHA,
                          "focus_alpha": FOCUS_ALPHA,
                          "background_alpha": BACKGROUND_ALPHA},
            "parts": list(ready.names),
            "dropped": list(ready.dropped),
            # A part maps to one visible staff only in this shape. Otherwise the
            # renderer highlights every staff equally rather than guessing.
            "focus_staves": ready.singing_staves == len(ready.names),
        }
