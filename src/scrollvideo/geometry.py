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
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Tuple

import cairosvg
import numpy as np
from PIL import Image
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
_TRANSLATE = re.compile(r"translate\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")
_STEM = re.compile(r"M\s*([-\d.]+)\s+([-\d.]+)\s+L\s*([-\d.]+)\s+([-\d.]+)")
_DEF_SCALE = re.compile(r'<svg[^>]*class="definition-scale"[^>]*>')
_VIEWBOX = re.compile(r'viewBox="([\d.\- ]+)"')
_WIDTH = re.compile(r'width="[\d.]+px"')
_HEIGHT = re.compile(r'height="[\d.]+px"')

# Cairo refuses surfaces wider than 32767 px, so long scores rasterise in tiles.
MAX_TILE_PX = 8000

# Pure red: the engraving is black on white, so nothing else can produce it.
MARKER = "#FF0000"
REST_CLASSES = ("rest", "mRest")


@dataclass(frozen=True)
class NoteGeom:
    """Where one note sits, in verovio units."""

    x: float
    y: float
    staff_top: float      # top staff line of the staff it belongs to (its band id)
    staff_spacing: float  # distance between two staff lines

    def box(self) -> Tuple[float, float, float, float]:
        """The notehead, which is what highlighting recolours."""
        sp = self.staff_spacing
        return (self.x - 0.2 * sp, self.y - 0.75 * sp,
                self.x + 1.4 * sp, self.y + 0.75 * sp)


@dataclass(frozen=True)
class RestGeom(NoteGeom):
    """A rest uses the notehead width but needs more vertical room."""

    measure_rest: bool = False

    def box(self) -> Tuple[float, float, float, float]:
        sp = self.staff_spacing
        return (self.x - 0.2 * sp, self.y - 2.5 * sp,
                self.x + 1.4 * sp, self.y + 2.5 * sp)


@dataclass(frozen=True)
class Layout:
    """The engraved page's unit space and everything placed in it."""

    width: float
    height: float
    notes: Dict[str, NoteGeom]
    staff_tops: List[float]   # top staff line of each staff, top to bottom
    rests: Dict[str, RestGeom] = field(default_factory=dict)

    def staff_index(self, geom: NoteGeom) -> int:
        return self.staff_tops.index(geom.staff_top)

    def playing(self, element_id: str) -> NoteGeom | None:
        """Geometry for a note or rest that can be active during playback."""
        return self.notes.get(element_id) or self.rests.get(element_id)


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def _definition_scale(svg_text: str) -> Tuple[str, List[float]]:
    """The nested <svg class="definition-scale"> tag and its viewBox.

    Coordinates live in that viewBox, not the root, whose px size is 1/25 of it.
    Matched loosely: once the SVG has been through lxml the attribute order and
    namespace declarations differ from what verovio emitted.
    """
    tag = _DEF_SCALE.search(svg_text)
    box = _VIEWBOX.search(tag.group(0)) if tag else None
    if not box:
        raise ValueError("Not a verovio SVG: no definition-scale viewBox.")
    return tag.group(0), [float(v) for v in box.group(1).split()]


def _window(svg_text: str, tag: str, x0: float, w_units: float, height_units: float,
            w_px: int, h_px: int) -> str:
    """The SVG showing only [x0, x0+w_units), sized to w_px by h_px."""
    windowed = _VIEWBOX.sub(f'viewBox="{x0} 0 {w_units} {height_units}"', tag, count=1)
    doc = svg_text.replace(tag, windowed, 1)
    head_end = doc.index(">") + 1
    head = _WIDTH.sub(f'width="{w_px}px"', doc[:head_end], count=1)
    head = _HEIGHT.sub(f'height="{h_px}px"', head, count=1)
    return head + doc[head_end:]


def parse_layout(svg_text: str) -> Layout:
    """Note positions and staff bands, in the units of the definition-scale viewBox."""
    _, vb = _definition_scale(svg_text)

    root = etree.fromstring(svg_text.encode())

    # Cumulative ancestor translates (page margin, systems).
    offsets: Dict[etree._Element, Tuple[float, float]] = {}
    for g in root.iter(_tag("g")):
        t = _TRANSLATE.match(g.get("transform", "") or "")
        dx, dy = (float(t.group(1)), float(t.group(2))) if t else (0.0, 0.0)
        px, py = offsets.get(g.getparent(), (0.0, 0.0))
        offsets[g] = (px + dx, py + dy)

    notes: Dict[str, NoteGeom] = {}
    rests: Dict[str, RestGeom] = {}
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

        for rest in staff.iter(_tag("g")):
            if rest.get("class") not in REST_CLASSES or not rest.get("id"):
                continue
            use = rest.find(_tag("use"))
            if use is None:
                continue
            t = _TRANSLATE.match(use.get("transform", "") or "")
            if not t:
                continue
            rests[rest.get("id")] = RestGeom(
                float(t.group(1)) + odx, float(t.group(2)) + ody, top, spacing,
                measure_rest=rest.get("class") == "mRest")

    return Layout(vb[2], vb[3], notes, sorted(staff_tops), rests)


def _tiles(svg_text: str, layout: Layout, height_px: int) -> Iterator[Tuple[int, int, np.ndarray]]:
    """Render the strip in tiles: (x offset, width, RGB tile).

    Cairo caps surface dimensions and a 3-minute score is wider than the cap.
    Tile edges are cut on the output pixel grid, not by converting a fixed unit
    width, so the seams abut exactly instead of accumulating rounding drift.
    """
    tag, _ = _definition_scale(svg_text)
    scale = height_px / layout.height
    total_px = int(round(layout.width * scale))

    x_px = 0
    while x_px < total_px:
        w_px = min(MAX_TILE_PX, total_px - x_px)
        doc = _window(svg_text, tag, x_px / scale, w_px / scale, layout.height,
                      w_px, height_px)
        png = cairosvg.svg2png(bytestring=doc.encode(), background_color="white")
        tile = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.uint8)
        yield x_px, w_px, tile
        x_px += w_px


def note_coverage(svg_text: str, layout: Layout, height_px: int) -> np.ndarray:
    """Per-pixel coverage of the note glyphs, 0-255, for recolouring them.

    The notes are re-rendered in a marker colour no engraving uses, and coverage
    read back as red-minus-green: pure red where a glyph covers the pixel, zero
    on the black staff lines and lyrics, and correctly partial on antialiased
    edges — so a highlighted note keeps its smooth outline instead of a fringe.
    """
    return _coverage(_mark_notes(svg_text), layout, height_px)


def _coverage(marked_svg: str, layout: Layout, height_px: int) -> np.ndarray:
    """Read marker-coloured glyph coverage from an SVG."""
    width_px = int(round(layout.width * height_px / layout.height))
    coverage = np.zeros((height_px, width_px), dtype=np.uint8)
    for x_px, w_px, tile in _tiles(marked_svg, layout, height_px):
        red = tile[:, :w_px, 0].astype(np.int16)
        green = tile[:, :w_px, 1].astype(np.int16)
        coverage[:, x_px:x_px + w_px] = np.clip(red - green, 0, 255).astype(np.uint8)
    return coverage


def playing_coverage(svg_text: str, layout: Layout, height_px: int) -> np.ndarray:
    """Per-pixel coverage of notes and rests that can be highlighted."""
    return _coverage(_mark_playing(svg_text), layout, height_px)


# An inline style, not fill/stroke attributes: verovio ships a stylesheet
# ("#id ellipse, #id path, ... {stroke:currentColor}") that outranks presentation
# attributes on the shapes it names.
#
# `color` is here for that same stylesheet, one level further down. A notehead is a
# `<use>` of a glyph kept in `<defs>`, so the shapes that actually get painted are
# paths the rule names by id — and a rule on the path itself beats a style inherited
# from the `<use>` above it. Marking the `<use>` therefore left the glyph outlined
# in black around a red fill, and since coverage is read back as red-minus-green,
# the whole antialiased edge came back at well under half its real value. Those edge
# pixels were then repainted almost white instead of part-blue, which is what gave a
# lit notehead a staircase for a border (#63). Setting `color` resolves that
# `currentColor` to the marker as well, so the outline is marked too and coverage
# matches the engraving's own ink pixel for pixel.
_MARK_STYLE = f"fill:{MARKER};stroke:{MARKER};color:{MARKER}"
_DRAWN = ("use", "ellipse", "polygon", "polyline", "rect", "path")


def _mark_notes(svg_text: str) -> str:
    """The same SVG with every **notehead** painted in the marker colour.

    Heads only. Stems, flags and beams stay black: the head alone reads as the
    note being sung, and colouring stems drags the eye up and down the staff as
    they flip direction. Lyrics live inside the note group and are likewise left
    alone.
    """
    root = etree.fromstring(svg_text.encode())
    for note in root.iter(_tag("g")):
        if note.get("class") != "note":
            continue
        for head in note.findall(f".//{_tag('g')}[@class='notehead']"):
            for part in head.iter():
                if etree.QName(part).localname in _DRAWN:
                    part.set("style", _MARK_STYLE)
    return etree.tostring(root, encoding="unicode")


def _mark_playing(svg_text: str) -> str:
    """The same SVG with noteheads and rests painted in the marker colour."""
    return _mark_rests(_mark_notes(svg_text))


def _mark_rests(svg_text: str) -> str:
    """The same SVG with ordinary and whole-measure rests marked."""
    root = etree.fromstring(svg_text.encode())
    for rest in root.iter(_tag("g")):
        if rest.get("class") not in REST_CLASSES:
            continue
        for part in rest.iter():
            if etree.QName(part).localname in _DRAWN:
                part.set("style", _MARK_STYLE)
    return etree.tostring(root, encoding="unicode")


def rasterise(svg_text: str, layout: Layout, height_px: int) -> np.ndarray:
    """The whole engraving as one RGB strip `height_px` tall.

    Rendered in tiles: cairo caps surface dimensions, and a 3-minute score is
    wider than the cap. Tile edges are cut on the output pixel grid so the tiles
    abut exactly instead of accumulating rounding drift.
    """
    # Filled tile by tile into one buffer: at 4K a strip is hundreds of MB, and
    # collecting tiles before joining them would hold two copies at once.
    total_px = int(round(layout.width * height_px / layout.height))
    strip = np.empty((height_px, total_px, 3), dtype=np.uint8)
    for x_px, w_px, tile in _tiles(svg_text, layout, height_px):
        strip[:, x_px:x_px + w_px] = tile[:, :w_px]
    return strip
