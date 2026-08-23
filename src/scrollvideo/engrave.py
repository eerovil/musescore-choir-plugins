"""Engrave a score to one continuous system (verovio) and read its geometry.

Verovio gives two things that line up by id: an SVG in which every note is a
``<g id=... class="note">``, and a timemap whose ``on``/``off`` lists name those
same ids. This module owns that pairing; nothing else touches verovio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import verovio

from .geometry import Layout, parse_layout

# One system, no page breaks: the score becomes a single horizontal strip.
#
# `mnumInterval` prints bar numbers, which a single continuous system otherwise
# shows only once (they are normally drawn per system). Every bar, not every
# fifth: only ~4 bars fit on screen, so a wider interval leaves stretches of the
# video with no number visible at all. `xmlIdSeed` fixes
# verovio's element ids, which are otherwise random per run — the ids do not
# affect what is drawn, but pinning them makes a render byte-for-byte
# reproducible and failures easier to compare.
MEASURE_NUMBER_INTERVAL = 1
XML_ID_SEED = 1

OPTIONS = {
    "breaks": "none",
    "adjustPageHeight": True,
    "adjustPageWidth": True,
    "header": "none",
    "footer": "none",
    "scale": 40,
    "mnumInterval": MEASURE_NUMBER_INTERVAL,
    "xmlIdSeed": XML_ID_SEED,
}


@dataclass(frozen=True)
class Engraving:
    svg: str
    layout: Layout
    timemap: List[dict]      # verovio events: qstamp, tstamp, on/off note ids
    drawn_id: Dict[str, str]  # sounding id -> the id of the note actually engraved

    @property
    def notes(self) -> Dict[str, object]:
        return self.layout.notes


def engrave(musicxml_path: str, options: Dict | None = None) -> Engraving:
    """Render `musicxml_path` as one system and return SVG + geometry + timemap."""
    tk = verovio.toolkit()
    tk.setOptions({**OPTIONS, **(options or {})})
    if not tk.loadFile(musicxml_path):
        raise RuntimeError(f"Verovio could not load {musicxml_path}")
    pages = tk.getPageCount()
    if pages != 1:
        raise RuntimeError(f"Expected one continuous system, got {pages} pages.")
    svg = tk.renderToSVG(1)
    layout = parse_layout(svg)
    timemap = tk.renderToTimemap({"includeMeasures": True})
    return Engraving(svg, layout, timemap, _drawn_ids(tk, timemap, layout))


def _drawn_ids(tk, timemap: List[dict], layout: Layout) -> Dict[str, str]:
    """Map every sounding note id to the note that is actually on the page.

    Verovio expands repeats in the timemap: a note inside a repeated section
    sounds again under a suffixed id (``xyz-rend2``) that is not drawn, because
    the section is engraved once. ``getNotatedIdForElement`` maps those copies
    back, which is what makes a repeat highlight the notes it plays — and makes
    the scroll jump back to them.
    """
    drawn: Dict[str, str] = {}
    for entry in timemap:
        for note_id in entry.get("on", []):
            if note_id in drawn:
                continue
            if note_id in layout.notes:
                drawn[note_id] = note_id
                continue
            notated = tk.getNotatedIdForElement(note_id)
            if notated in layout.notes:
                drawn[note_id] = notated
    return drawn
