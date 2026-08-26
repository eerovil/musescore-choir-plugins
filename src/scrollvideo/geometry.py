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


_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_COMMAND = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")
_OP = re.compile(r"(translate|scale)\(([^)]*)\)")
_HREF = "{http://www.w3.org/1999/xlink}href"

# How many numbers each path command takes per point group. `A` is the odd one
# out: its first five numbers are radii and flags, and only the last two are a
# point, so its x is the second from the end rather than the first.
_ARGS = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7}

# Something whose horizontal reach we could not work out. It counts as drawing
# across the whole page, so the measure holding it is never cropped away.
_UNBOUNDED = object()


def _numbers(text: str) -> List[float]:
    return [float(n) for n in _NUMBER.findall(text or "")]


def _path_x_range(d: str):
    """The smallest and largest x a path's own coordinates reach.

    Curves are bounded by their control points, so taking every control point
    gives a range that contains the drawn curve rather than approximating it —
    which is what cropping needs: too wide only costs a little speed, too narrow
    would rub ink off the page.
    """
    xs: List[float] = []
    x = 0.0
    tokens = [(m.group(0), m.start(), m.end()) for m in _COMMAND.finditer(d or "")]
    for index, (command, _start, end) in enumerate(tokens):
        stop = tokens[index + 1][1] if index + 1 < len(tokens) else len(d)
        upper = command.upper()
        relative = command.islower()
        if upper == "Z":
            continue
        if upper not in _ARGS:
            return _UNBOUNDED
        args = _numbers(d[end:stop])
        step = _ARGS[upper]
        if not args or len(args) % step:
            return _UNBOUNDED
        for offset in range(0, len(args), step):
            group = args[offset:offset + step]
            points = [group[-2]] if upper == "A" else (
                [group[0]] if upper == "H" else
                [] if upper == "V" else group[0::2])
            for value in points:
                xs.append(x + value if relative else value)
            if points:
                x = xs[-1]
    return (min(xs), max(xs)) if xs else _UNBOUNDED


def _transform(attribute: str) -> Tuple[float, float]:
    """An element's own transform as (x offset, x scale), applied in order."""
    offset, scale = 0.0, 1.0
    for name, args in _OP.findall(attribute or ""):
        values = _numbers(args)
        if name == "translate" and values:
            offset += scale * values[0]
        elif name == "scale" and values:
            scale *= values[0]
    return offset, scale


def _own_x_range(element, glyphs: Dict[str, Tuple[float, float]]):
    """Where one element draws, in its parent's coordinates — or None if nowhere."""
    name = etree.QName(element).localname
    pad = float(element.get("stroke-width") or 0) / 2.0
    if name == "path":
        span = _path_x_range(element.get("d") or "")
    elif name == "use":
        reference = (element.get(_HREF) or element.get("href") or "").lstrip("#")
        # A glyph we could not measure has to count as drawing everywhere, or
        # cropping would rub it off the page.
        span = glyphs.get(reference, _UNBOUNDED)
    elif name == "rect":
        x = _numbers(element.get("x") or "0")
        width = _numbers(element.get("width") or "0")
        span = (x[0], x[0] + width[0]) if x and width else None
    elif name in ("polygon", "polyline"):
        xs = _numbers(element.get("points") or "")[0::2]
        span = (min(xs), max(xs)) if xs else None
    elif name == "ellipse":
        cx = _numbers(element.get("cx") or "")
        rx = _numbers(element.get("rx") or "0")
        span = (cx[0] - rx[0], cx[0] + rx[0]) if cx else None
    elif name in ("text", "tspan"):
        # Writing, whose width needs the font to know. One em per character is
        # more than any glyph is wide, and the anchor may put the string either
        # side of x, so this covers every way it can be laid out.
        x = _numbers(element.get("x") or "")
        if not x:
            return None
        sizes = [_numbers(node.get("font-size") or "0")[0]
                 for node in element.iter() if node.get("font-size")]
        # Collapsed, because the SVG is indented and the layout whitespace
        # between tags is not writing anyone can see.
        letters = len(" ".join("".join(element.itertext()).split()))
        reach = letters * max(sizes or [0.0])
        span = (x[0] - reach, x[0] + reach)
    else:
        return None
    if span is None or span is _UNBOUNDED:
        return span
    offset, scale = _transform(element.get("transform"))
    low, high = sorted((offset + scale * span[0], offset + scale * span[1]))
    return low - pad, high + pad


def _glyph_extents(root) -> Dict[str, Tuple[float, float]]:
    """How wide each glyph in <defs> is, so a <use> of it can be placed."""
    extents: Dict[str, Tuple[float, float]] = {}
    for defs in root.iter(_tag("defs")):
        for glyph in defs:
            identifier = glyph.get("id")
            if not identifier:
                continue
            # `_subtree_x_range` already applies the glyph's own transform, which
            # is what a `<use>` of it draws.
            span = _subtree_x_range(glyph, {})
            if span and span is not _UNBOUNDED:
                extents[identifier] = span
    return extents


def _subtree_x_range(element, glyphs: Dict[str, Tuple[float, float]]):
    """Where an element and everything inside it draws, in its parent's coordinates."""
    own = _own_x_range(element, glyphs)
    if own is _UNBOUNDED:
        return _UNBOUNDED
    spans = [own] if own else []
    offset, scale = _transform(element.get("transform"))
    for child in element:
        inner = _subtree_x_range(child, glyphs)
        if inner is _UNBOUNDED:
            return _UNBOUNDED
        if inner:
            spans.append((offset + scale * inner[0], offset + scale * inner[1]))
    if not spans:
        return None
    return min(s[0] for s in spans), max(s[1] for s in spans)


def measure_spans(root) -> List[Tuple[object, float, float]]:
    """Every engraved measure and the horizontal band of page it draws in."""
    glyphs = _glyph_extents(root)
    offsets: Dict[object, Tuple[float, float]] = {}
    spans = []
    for group in root.iter(_tag("g")):
        offset, scale = _transform(group.get("transform"))
        parent_offset, parent_scale = offsets.get(group.getparent(), (0.0, 1.0))
        offsets[group] = (parent_offset + parent_scale * offset, parent_scale * scale)
        if group.get("class") != "measure":
            continue
        # `_subtree_x_range` already applies the measure's own transform, so what
        # it returns is in the parent's coordinates and only the ancestors are left.
        inner = _subtree_x_range(group, glyphs)
        if inner is None:
            continue
        if inner is _UNBOUNDED:
            spans.append((group, float("-inf"), float("inf")))
            continue
        spans.append((group, parent_offset + parent_scale * inner[0],
                      parent_offset + parent_scale * inner[1]))
    return spans


class _Croppable:
    """One parsed engraving, handing out the part of itself a tile can see.

    Rasterising in tiles used to give cairosvg the whole score every time and only
    move the viewBox. Cairo then clips, but cairosvg has already walked and drawn
    every node in the document — so an 8000px tile and a 1765px tile of the same
    score cost the same, and a strip cut into eight tiles costs eight full passes
    over the music. Handing each tile only the measures inside it makes the whole
    strip cost about one pass however many tiles it is cut into.

    Nothing about the picture changes: a measure is dropped only when the band of
    page it draws in — glyph widths, curve control points and the widest a piece
    of writing could be, all measured off the engraving itself — lies outside the
    window entirely.
    """

    def __init__(self, svg_text: str):
        self._text = svg_text
        self._root = etree.fromstring(svg_text.encode())
        self._spans = measure_spans(self._root)

    def window(self, x0: float, x1: float) -> str:
        """This engraving with the measures outside [x0, x1) taken out."""
        hidden = [(measure, measure.getparent())
                  for measure, low, high in self._spans if high < x0 or low > x1]
        if not hidden:
            return self._text
        places = [(parent.index(measure), measure, parent)
                  for measure, parent in hidden]
        for _index, measure, parent in places:
            parent.remove(measure)
        try:
            return etree.tostring(self._root, encoding="unicode")
        finally:
            for index, measure, parent in sorted(places, key=lambda p: p[0]):
                parent.insert(index, measure)


def _tiles(svg_text: str, layout: Layout, height_px: int) -> Iterator[Tuple[int, int, np.ndarray]]:
    """Render the strip in tiles: (x offset, width, RGB tile).

    Cairo caps surface dimensions and a 3-minute score is wider than the cap.
    Tile edges are cut on the output pixel grid, not by converting a fixed unit
    width, so the seams abut exactly instead of accumulating rounding drift.

    Each tile is given only the measures it shows (`_Croppable`), because the cost
    of drawing one is the number of nodes in the document handed over and not the
    size of the window onto it.
    """
    scale = height_px / layout.height
    total_px = int(round(layout.width * scale))
    single = total_px <= MAX_TILE_PX
    croppable = None if single else _Croppable(svg_text)

    x_px = 0
    while x_px < total_px:
        w_px = min(MAX_TILE_PX, total_px - x_px)
        text = svg_text if croppable is None else croppable.window(x_px / scale,
                                                                  (x_px + w_px) / scale)
        tag, _ = _definition_scale(text)
        doc = _window(text, tag, x_px / scale, w_px / scale, layout.height,
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
