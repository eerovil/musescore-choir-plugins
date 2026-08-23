"""Scrolling practice videos rendered from a score — without driving MuseScore's GUI.

Public interface:

    build_videos(mscx_path, out_dir, parts=..., ...) -> [video paths]

Everything else (verovio engraving, SVG geometry, the MIDI tempo map, the frame
compositing) is an implementation detail of that call.
"""

from .build import build_videos, unsupported_repeats

__all__ = ["build_videos", "unsupported_repeats"]
