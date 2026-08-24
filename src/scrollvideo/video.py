"""Frames: a pre-rendered strip, scrolled, with active notes and rests lit up.

The engraving is rasterised once; a frame is a crop of that strip plus an alpha
blend over each sounding note's rectangle. Nothing is re-engraved per frame, so
a minute of music costs a few seconds of CPU.
"""

from __future__ import annotations

import os
import subprocess
from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .geometry import Layout
from .timing import NoteEvent

# MuseScore's playback blue, sampled from the score window.
HIGHLIGHT = (42, 95, 171)
FOCUS_ALPHA = 1.0        # how blue the voice this track is for goes
BACKGROUND_ALPHA = 0.45  # ... and the other voices, when emphasising one
BAND_ALPHA = 0.18        # translucent beat marker; engraving remains readable
TAIL_SECONDS = 1.5       # keep rolling past the last note
# Engraving is flat white with sharp black marks — x264 finds it very compressible,
# so a fast preset costs little quality here and saves a lot of time at 4K.
DEFAULT_PRESET = "medium"
_BAND_COLOUR = np.rint(np.array(HIGHLIGHT) * BAND_ALPHA).astype(np.uint8)


def mux(video_path: str, audio_path: str, out_path: str) -> str:
    """Put `audio_path` onto an already-encoded video without re-encoding it.

    The picture is identical for every voice, so it is encoded once and each
    practice track is this: a stream copy plus an audio encode, about a second,
    instead of another twenty seconds of x264.
    """
    result = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-i", audio_path,
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
         "-b:a", "192k", out_path], capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"ffmpeg failed muxing {os.path.basename(out_path)}\n"
                           + (result.stderr or result.stdout or ""))
    return out_path


@dataclass(frozen=True)
class Placed:
    """A timed note or rest with its pixel rectangle and staff."""

    on: float
    off: float
    x0: int
    x1: int
    y0: int
    y1: int
    staff: int


def place(events: Sequence[NoteEvent], layout: Layout, px_per_unit: float,
          staff_limit: Optional[int] = None) -> List[Placed]:
    """Timed notes/rests -> pixel boxes, sorted by onset."""
    placed = []
    for e in events:
        geom = layout.playing(e.note_id)
        if geom is None:
            continue
        staff = layout.staff_index(geom)
        if staff_limit is not None and staff >= staff_limit:
            continue
        x0, y0, x1, y1 = geom.box()
        placed.append(Placed(
            e.on, e.off,
            int(x0 * px_per_unit), int(x1 * px_per_unit),
            int(y0 * px_per_unit), int(y1 * px_per_unit),
            staff))
    placed.sort(key=lambda p: p.on)
    return placed


def latest_onset_index(onsets: Sequence[float], t: float) -> Optional[int]:
    """Index of the last onset at or before ``t``, or none before music starts."""
    index = bisect_right(onsets, t) - 1
    return index if index >= 0 else None


def blend_beat_marker(frame: np.ndarray, marker: Placed, left: int) -> None:
    """Blend a two-notehead-wide, full-height marker into ``frame`` in place."""
    width = frame.shape[1]
    note_width = max(1, marker.x1 - marker.x0)
    band_width = 2 * note_width
    centre = (marker.x0 + marker.x1) / 2
    x0 = int(round(centre - band_width / 2)) - left
    x1 = x0 + band_width
    x0, x1 = max(x0, 0), min(x1, width)
    if x1 <= x0:
        return

    # Both operations use the reusable frame slice as their output, so no
    # frame-sized temporary is allocated for the marker.
    band = frame[:, x0:x1]
    np.multiply(band, 1 - BAND_ALPHA, out=band, casting="unsafe")
    np.add(band, _BAND_COLOUR, out=band, casting="unsafe")


def render(strip: np.ndarray, placed: Sequence[Placed], anchors: Tuple[Sequence[float], Sequence[float]],
           out_path: str, *, px_per_unit: float, width: int = 3840, fps: int = 60,
           playhead: float = 0.35, focus_staff: Optional[int] = None,
           audio_path: Optional[str] = None, duration: Optional[float] = None,
           crf: int = 20, preset: str = DEFAULT_PRESET,
           coverage: Optional[np.ndarray] = None) -> str:
    """Write the scrolling video. `focus_staff` lights one staff and dims the rest.

    `coverage` is `geometry.playing_coverage`: how much of each pixel a notehead or
    rest glyph covers. An active symbol is repainted blue through it, including
    antialiased edges, rather than sitting under a coloured box. Without coverage,
    the box is drawn instead.
    """
    height = strip.shape[0]
    strip_w = strip.shape[1]
    times, xs = anchors
    at = np.asarray(times, dtype=np.float64)
    ax = np.asarray(xs, dtype=np.float64) * px_per_unit

    if duration is None:
        duration = (max((p.off for p in placed), default=0.0)) + TAIL_SECONDS
    n_frames = max(1, int(round(duration * fps)))

    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-"]
    if audio_path:
        cmd += ["-i", audio_path, "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p",
            "-crf", str(crf), out_path]

    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    # One reused buffer. At 4K a frame is 25MB, so allocating and white-filling a
    # fresh one per frame costs more than the encoder does; the strip copy below
    # overwrites the window, and only the margins past either end need whiting out.
    frame = np.empty((height, width, 3), dtype=np.uint8)
    colour = np.array(HIGHLIGHT, dtype=np.float32)
    onsets = tuple(p.on for p in placed)

    try:
        for frame_no in range(n_frames):
            t = frame_no / fps
            left = int(np.interp(t, at, ax) - playhead * width)
            src0, src1 = max(0, left), min(strip_w, left + width)
            dst0, dst1 = src0 - left, src1 - left
            if dst0 > 0:
                frame[:, :dst0] = 255
            if dst1 < width:
                frame[:, dst1:] = 255
            if src1 > src0:
                np.copyto(frame[:, dst0:dst1], strip[:, src0:src1])

            marker_index = latest_onset_index(onsets, t)
            if marker_index is not None:
                blend_beat_marker(frame, placed[marker_index], left)

            for p in placed:
                if p.on > t:
                    break
                if p.off <= t:
                    continue
                x0, x1 = max(p.x0 - left, 0), min(p.x1 - left, width)
                if x1 <= x0:
                    continue
                y0, y1 = max(p.y0, 0), min(p.y1, height)
                if y1 <= y0:
                    continue
                strength = FOCUS_ALPHA if focus_staff in (None, p.staff) else BACKGROUND_ALPHA

                if coverage is None:
                    patch = frame[y0:y1, x0:x1].astype(np.float32)
                    frame[y0:y1, x0:x1] = (patch * (1 - strength)
                                           + colour * strength).astype(np.uint8)
                    continue

                # Repaint the glyph: where the note covers the pixel, draw it in
                # blue over white instead of black, weighted by that coverage.
                cover = coverage[y0:y1, x0 + left:x1 + left]
                if not cover.any():
                    continue
                a = (cover.astype(np.float32) / 255.0 * strength)[:, :, None]
                patch = frame[y0:y1, x0:x1].astype(np.float32)
                frame[y0:y1, x0:x1] = np.where(
                    a > 0, 255.0 - a * (255.0 - colour), patch).astype(np.uint8)

            ff.stdin.write(frame.tobytes())
        ff.stdin.close()
    except BrokenPipeError as exc:
        raise RuntimeError("ffmpeg exited while frames were being written.") from exc
    if ff.wait() != 0:
        raise RuntimeError(f"ffmpeg failed writing {out_path}")
    return out_path
