"""Verovio SVG -> note geometry, and SVG -> pixel strip.

The only module that knows how a verovio SVG is laid out. Two facts drive it:

* Coordinates live in the nested ``<svg class="definition-scale" viewBox=...>``;
  that viewBox is the score's unit space (the root's px size is 1/25 of it).
* A note's position is the ``translate(x, y)`` on its notehead ``<use>``, plus
  every ancestor ``<g transform="translate(...)">`` (verovio emits a page margin).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import cairosvg
import numpy as np
from PIL import Image
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
_TRANSLATE = re.compile(r"translate\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")
_DEF_SCALE = re.compile(r'<svg class="definition-scale"[^>]*viewBox="([\d.\- ]+)"[^>]*>')
_ROOT_SIZE = re.compile(r'^<svg width="[\d.]+px" height="[\d.]+px"')

# Cairo refuses surfaces wider than 32767 px, so long scores rasterise in tiles.
MAX_TILE_PX = 8000


@dataclass(frozen=True)
class NoteGeom:
    """Where one note sits, in verovio units."""

    x: float
    y: float
    staff_top: float      # top staff line of the staff it belongs to (its band id)
    staff_spacing: float  # distance between two staff lines


@dataclass(frozen=True)
class Layout:
    """The engraved page's unit space and everything placed in it."""

    width: float
    height: float
    notes: Dict[str, NoteGeom]
    staff_tops: List[float]   # top staff line of each staff, top to bottom

    def staff_index(self, geom: NoteGeom) -> int:
        return self.staff_tops.index(geom.staff_top)


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def parse_layout(svg_text: str) -> Layout:
    """Note positions and staff bands, in the units of the definition-scale viewBox."""
    m = _DEF_SCALE.search(svg_text)
    if not m:
        raise ValueError("Not a verovio SVG: no definition-scale viewBox.")
    vb = [float(v) for v in m.group(1).split()]

    root = etree.fromstring(svg_text.encode())

    # Cumulative ancestor translates (page margin, systems).
    offsets: Dict[etree._Element, Tuple[float, float]] = {}
    for g in root.iter(_tag("g")):
        t = _TRANSLATE.match(g.get("transform", "") or "")
        dx, dy = (float(t.group(1)), float(t.group(2))) if t else (0.0, 0.0)
        px, py = offsets.get(g.getparent(), (0.0, 0.0))
        offsets[g] = (px + dx, py + dy)

    notes: Dict[str, NoteGeom] = {}
    staff_tops: set = set()
    for staff in root.iter(_tag("g")):
        if staff.get("class") != "staff":
            continue
        _, ody = offsets[staff]
        odx, _ = offsets[staff]
        lines = []
        for path in staff.findall(_tag("path")):
            d = re.match(r"M[\d.]+ ([\d.]+) L", path.get("d", "") or "")
            if d:
                lines.append(float(d.group(1)) + ody)
        if len(lines) < 5:
            continue
        lines = sorted(set(lines))[:5]
        top, spacing = lines[0], (lines[-1] - lines[0]) / 4.0
        staff_tops.add(top)

        for note in staff.iter(_tag("g")):
            if note.get("class") != "note" or not note.get("id"):
                continue
            use = note.find(f".//{_tag('g')}[@class='notehead']/{_tag('use')}")
            if use is None:
                continue
            t = _TRANSLATE.match(use.get("transform", "") or "")
            if not t:
                continue
            notes[note.get("id")] = NoteGeom(float(t.group(1)) + odx,
                                             float(t.group(2)) + ody, top, spacing)

    return Layout(vb[2], vb[3], notes, sorted(staff_tops))


def rasterise(svg_text: str, layout: Layout, height_px: int) -> np.ndarray:
    """The whole engraving as one RGB strip `height_px` tall.

    Rendered in tiles: cairo caps surface dimensions, and a 3-minute score is
    wider than the cap. Tile edges are cut on the output pixel grid so the tiles
    abut exactly instead of accumulating rounding drift.
    """
    m = _DEF_SCALE.search(svg_text)
    scale = height_px / layout.height
    total_px = int(round(layout.width * scale))

    # Filled tile by tile into one buffer: at 4K a strip is hundreds of MB, and
    # collecting tiles before joining them would hold two copies at once.
    strip = np.empty((height_px, total_px, 3), dtype=np.uint8)
    x_px = 0
    while x_px < total_px:
        w_px = min(MAX_TILE_PX, total_px - x_px)
        # Derive the unit window from the pixel window, not the other way round.
        x0_units, w_units = x_px / scale, w_px / scale
        doc = svg_text.replace(
            m.group(0),
            m.group(0).replace(f'viewBox="{m.group(1)}"',
                               f'viewBox="{x0_units} 0 {w_units} {layout.height}"'), 1)
        doc = _ROOT_SIZE.sub(f'<svg width="{w_px}px" height="{height_px}px"', doc, count=1)
        png = cairosvg.svg2png(bytestring=doc.encode(), background_color="white")
        tile = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.uint8)
        strip[:, x_px:x_px + w_px] = tile[:, :w_px]
        x_px += w_px

    return strip
