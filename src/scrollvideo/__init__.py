"""Scrolling practice videos rendered from a score — without driving MuseScore's GUI.

Public interface:

    build_videos(mscx_path, out_dir, parts=..., ...) -> [video paths]
    preview.preview(mscx_path, width=..., height=..., ...) -> a payload to play

Everything else (verovio engraving, SVG geometry, the MIDI tempo map, the frame
compositing) is an implementation detail of those calls. Both are the same render:
`build.prepare` decides the engraving, the clock, the scroll and the viewport, and
one of them turns that into pixels while the other hands it to the browser.

The preview is imported from its own module (`from src.scrollvideo.preview import
preview`) rather than re-exported here — binding the function on the package would
hide the module of the same name from anything that imports it.
"""

from .build import build_videos, prepare, unsupported_repeats

__all__ = ["build_videos", "prepare", "unsupported_repeats"]
