"""Engrave a score to one continuous system (verovio) and read its geometry.

Verovio gives two things that line up by id: an SVG in which every note is a
``<g id=... class="note">``, and a timemap whose ``on``/``off`` lists name those
same ids. This module owns that pairing; nothing else touches verovio.
"""

from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Dict, List, Optional

import verovio
from lxml import etree

from .geometry import SVG_NS, Layout, parse_layout

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

# The native library does not carry the package's default resource path into the
# executor thread used by the song app. Give each toolkit its bundled fonts
# explicitly wherever engraving runs.
RESOURCE_PATH = str(files("verovio") / "data")


@dataclass(frozen=True)
class Engraving:
    svg: str
    layout: Layout
    timemap: List[dict]       # qstamp/tstamp plus timed note and rest ids
    drawn_id: Dict[str, str]  # timed id -> the symbol id actually engraved

    @property
    def notes(self) -> Dict[str, object]:
        return self.layout.notes


def engrave(musicxml_path: str, options: Dict | None = None) -> Engraving:
    """Render `musicxml_path` as one system and return SVG + geometry + timemap."""
    tk = verovio.toolkit(False)
    if not tk.setResourcePath(RESOURCE_PATH):
        raise RuntimeError(f"Verovio could not load its resources from {RESOURCE_PATH}")
    tk.setOptions({**OPTIONS, **(options or {})})
    if not tk.loadFile(musicxml_path):
        raise RuntimeError(f"Verovio could not load {musicxml_path}")
    pages = tk.getPageCount()
    if pages != 1:
        raise RuntimeError(f"Expected one continuous system, got {pages} pages.")
    svg = draw_symbol_text(tk.renderToSVG(1))
    layout = parse_layout(svg)
    timemap = tk.renderToTimemap({"includeMeasures": True, "includeRests": True})
    return Engraving(svg, layout, timemap, _drawn_ids(tk, timemap, layout))


# Music symbols that appear inside a piece of text — the quarter note in a
# tempo mark, "♩ = 80" — are the one thing verovio does not draw. It writes them
# as characters of its own music font and leaves the renderer to find that font.
# Ours cannot: cairo picks fonts through fontconfig, the font is only offered as
# a base64 @font-face in the SVG's stylesheet, and cairosvg ignores @font-face.
# So the note came out as the empty box a font draws for a character it has not
# got. Verovio ships the same glyphs as SVG outlines next to the font, and those
# are what every symbol it *does* draw is made of, so we draw the character from
# the outline ourselves and the mark reads as it does on the page.
MUSIC_FONT = "Leipzig"
UNITS_PER_EM = 1000.0
_SMUFL = re.compile("^[\\uE000-\\uF8FF]+$")
_PX = re.compile(r"^([\d.]+)")


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


@lru_cache(maxsize=None)
def _glyph(code: str) -> Optional[etree._Element]:
    """The outline verovio draws for one music character, or None if it has none.

    The outline files carry no namespace; the shapes are moved into the SVG one
    so that what goes into the page is the same kind of element as everything
    already there.
    """
    path = os.path.join(RESOURCE_PATH, MUSIC_FONT, f"{code}.xml")
    if not os.path.exists(path):
        return None
    outline = etree.parse(path).getroot()
    for node in outline.iter():
        if isinstance(node.tag, str):
            node.tag = _tag(etree.QName(node).localname)
    return outline


@lru_cache(maxsize=None)
def _advances() -> Dict[str, float]:
    """How far each music character moves the pen, in font units."""
    boxes = etree.parse(os.path.join(RESOURCE_PATH, f"{MUSIC_FONT}.xml")).getroot()
    return {g.get("c"): float(g.get("h-a-x", 0)) for g in boxes.iter("g") if g.get("c")}


def _font_size(element: etree._Element) -> Optional[float]:
    """The font size in force on `element`, following inheritance upwards."""
    while element is not None:
        size = _PX.match(element.get("font-size") or "")
        # A `<text>` verovio sizes per run declares `font-size="0px"`, which is
        # not a size to inherit but a statement that the runs carry their own.
        if size and float(size.group(1)) > 0:
            return float(size.group(1))
        element = element.getparent()
    return None


def _runs(text: etree._Element) -> List[etree._Element]:
    """The pieces of writing inside a `<text>`, in the order they are laid out."""
    return [span for span in text.iter(_tag("tspan")) if (span.text or "").strip()]


def draw_symbol_text(svg: str) -> str:
    """The same SVG with music characters inside text replaced by their outlines.

    Only a symbol that opens its text is drawn. Everything after it moves along
    by the width the font would have advanced, which is a number verovio ships;
    where a symbol *follows* writing we would have to measure that writing in
    whatever font the renderer chose, and a guess at the position is worse than
    the box, so those are left alone. Every tempo mark met so far — the printed
    ones and the fallback the app adds for a score with no tempo — opens with
    its note.
    """
    root = etree.fromstring(svg.encode())
    changed = False
    for text in root.iter(_tag("text")):
        runs = _runs(text)
        if not runs or not _SMUFL.match(runs[0].text.strip()):
            continue
        if text.get("text-anchor") or text.get("x") is None:
            continue  # the pen does not start at the text's own x
        size = _font_size(runs[0])
        if size is None:
            continue
        drawn = _draw_run(runs[0], float(text.get("x")), float(text.get("y")), size)
        if drawn is None:
            continue
        group, pen = drawn
        text.addprevious(group)
        if len(runs) > 1:
            runs[1].set("x", f"{pen:.4g}")
        changed = True
    return etree.tostring(root, encoding="unicode") if changed else svg


def _draw_run(run: etree._Element, x: float, y: float,
              size: float) -> Optional[tuple]:
    """Replace one run of music characters with outlines; return them and the pen."""
    scale = size / UNITS_PER_EM
    advances = _advances()
    group = etree.Element(_tag("g"), {"class": "symbol-text"})
    for character in run.text.strip():
        code = f"{ord(character):04X}"
        glyph = _glyph(code)
        if glyph is None:
            return None
        placed = etree.SubElement(
            group, _tag("g"),
            {"transform": f"translate({x:.4g}, {y:.4g}) scale({scale:.4g})"})
        placed.extend(deepcopy(child) for child in glyph)
        x += advances.get(code, 0.0) * scale
    run.getparent().remove(run)
    return group, x


def _drawn_ids(tk, timemap: List[dict], layout: Layout) -> Dict[str, str]:
    """Map every timed note/rest id to the symbol that is actually on the page.

    Verovio expands repeats in the timemap: a note inside a repeated section
    sounds again under a suffixed id (``xyz-rend2``) that is not drawn, because
    the section is engraved once. ``getNotatedIdForElement`` maps those copies
    back, which is what makes a repeat highlight the notes it plays — and makes
    the scroll jump back to them.
    """
    drawn: Dict[str, str] = {}
    for entry in timemap:
        for element_id in (*entry.get("on", []), *entry.get("restsOn", [])):
            if element_id in drawn:
                continue
            if layout.playing(element_id) is not None:
                drawn[element_id] = element_id
                continue
            notated = tk.getNotatedIdForElement(element_id)
            if layout.playing(notated) is not None:
                drawn[element_id] = notated
    return drawn
