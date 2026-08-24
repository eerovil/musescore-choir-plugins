"""Reading a verovio SVG: where the notes are, and turning the page into pixels."""

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from lxml import etree

from src.scrollvideo import geometry
from src.scrollvideo.engrave import engrave
from src.scrollvideo.geometry import RestGeom, parse_layout, rasterise

MINI_SVG = """<svg width="100px" height="20px" xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink">
  <svg class="definition-scale" viewBox="0 0 2500 500">
    <g class="page-margin" transform="translate(500, 300)">
      <g class="measure">
        <g class="staff">
          <path d="M0 0 L900 0" /><path d="M0 100 L900 100" /><path d="M0 200 L900 200" />
          <path d="M0 300 L900 300" /><path d="M0 400 L900 400" />
          <g id="n1" class="note">
            <g class="notehead"><use xlink:href="#x" transform="translate(200, 100)" /></g>
            <g class="stem"><path d="M310 90 L310 -320" stroke-width="18" /></g>
            <g class="verse"><text x="200" y="600">la</text></g>
          </g>
          <g id="r1" class="mRest">
            <use xlink:href="#rest" transform="translate(500, 200)" />
          </g>
        </g>
      </g>
    </g>
  </svg>
</svg>"""


def test_ancestor_translates_are_added_to_note_positions():
    """The page-margin translate is part of a note's position; dropping it put
    every highlight a staff too high."""
    layout = parse_layout(MINI_SVG)
    note = layout.notes["n1"]
    assert (note.x, note.y) == (700.0, 400.0)      # 200+500, 100+300
    assert note.staff_top == 300.0                  # 0 + margin
    assert note.staff_spacing == 100.0


def test_layout_uses_the_definition_scale_viewbox_not_the_root_size():
    """Coordinates live in the nested viewBox; the root's px size is 1/25 of it."""
    layout = parse_layout(MINI_SVG)
    assert (layout.width, layout.height) == (2500.0, 500.0)


def test_ancestor_translates_are_added_to_rest_positions():
    layout = parse_layout(MINI_SVG)
    rest = layout.rests["r1"]
    assert (rest.x, rest.y) == (1000.0, 500.0)
    assert layout.staff_index(rest) == 0


def test_rest_box_is_taller_than_a_notehead_but_keeps_its_width():
    layout = parse_layout(MINI_SVG)
    rest = layout.rests["r1"]
    note = layout.notes["n1"]
    assert isinstance(rest, RestGeom)
    assert rest.box()[2] - rest.box()[0] == note.box()[2] - note.box()[0]
    assert rest.box()[3] - rest.box()[1] > note.box()[3] - note.box()[1]


def test_parse_layout_rejects_a_non_verovio_svg():
    with pytest.raises(ValueError):
        parse_layout('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>')


def test_engrave_loads_fonts_in_a_worker_thread(fermata_musicxml):
    """The song app engraves in an executor, outside Verovio's importing thread."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        rendered = executor.submit(engrave, fermata_musicxml).result()
    assert rendered.notes


def test_every_engraved_note_that_sounds_has_a_position(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    sounding = {n for entry in eng.timemap for n in entry.get("on", [])}
    assert sounding, "fixture should contain notes"
    assert sounding <= set(eng.layout.notes), "a sounding note had no geometry"


def test_every_timed_rest_has_a_position(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    rests = {r for entry in eng.timemap for r in entry.get("restsOn", [])}
    assert rests, "fixture should contain rests"
    assert rests <= set(eng.layout.rests), "a timed rest had no geometry"


def test_whole_measure_rest_has_timing_and_geometry(tmp_path):
    score = tmp_path / "measure-rest.musicxml"
    score.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>S</part-name></score-part></part-list>
  <part id="P1"><measure number="1">
    <attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time>
      <clef><sign>G</sign><line>2</line></clef></attributes>
    <note><rest measure="yes"/><duration>4</duration><voice>1</voice></note>
  </measure></part>
</score-partwise>""")
    eng = engrave(str(score))
    timed = {r for entry in eng.timemap for r in entry.get("restsOn", [])}
    root = etree.fromstring(eng.svg.encode())
    drawn = {node.get("id") for node in root.iter()
             if node.get("class") == "mRest"}
    assert timed
    assert timed <= drawn <= set(eng.layout.rests)
    _assert_rest_boxes_cover_glyphs(eng)


def test_notes_sit_inside_their_own_staff_band(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    for geom in eng.layout.notes.values():
        staff_bottom = geom.staff_top + 4 * geom.staff_spacing
        assert geom.staff_top - 6 * geom.staff_spacing <= geom.y
        assert geom.y <= staff_bottom + 6 * geom.staff_spacing


def test_rasterise_draws_ink_across_the_whole_strip(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    strip = rasterise(eng.svg, eng.layout, 240)
    assert strip.shape[0] == 240
    assert strip.shape[1] == int(round(eng.layout.width * 240 / eng.layout.height))
    ink = (strip < 128).any(axis=2)
    assert ink.any(), "strip rendered blank"
    # ink must reach the far end: a windowing bug once rendered only the first tile
    assert ink[:, -strip.shape[1] // 4:].any()


def test_tiled_and_single_shot_rasterisation_agree(fermata_musicxml, monkeypatch):
    """Cairo caps surface width, so long scores render in tiles; the seams must
    fall on the pixel grid rather than accumulating rounding drift."""
    eng = engrave(fermata_musicxml)
    whole = rasterise(eng.svg, eng.layout, 120)
    monkeypatch.setattr(geometry, "MAX_TILE_PX", 37)      # forces many odd-width tiles
    tiled = rasterise(eng.svg, eng.layout, 120)
    assert tiled.shape == whole.shape

    # Antialiasing along a seam can differ by a pixel; alignment may not.
    ink_whole = (whole < 128).any(axis=2)
    ink_tiled = (tiled < 128).any(axis=2)
    cols_whole = np.where(ink_whole.any(axis=0))[0]
    cols_tiled = np.where(ink_tiled.any(axis=0))[0]
    assert (cols_whole.min(), cols_whole.max()) == (cols_tiled.min(), cols_tiled.max())
    assert np.abs(ink_whole.sum(axis=0) - ink_tiled.sum(axis=0)).max() <= 1
    differing = np.abs(tiled.astype(int) - whole.astype(int)).any(axis=2).sum()
    assert differing < 0.001 * tiled.shape[0] * tiled.shape[1]


def test_the_note_box_is_the_head_and_stops_short_of_the_stem():
    """Only the head is recoloured, so the box need not chase the stem."""
    note = parse_layout(MINI_SVG).notes["n1"]
    x0, y0, x1, y1 = note.box()
    assert y0 > -320 + 300, "box should not reach up the stem"
    assert y0 < 100 + 300 < y1, "box should contain the notehead"


def test_marking_paints_the_notehead_only():
    """Heads turn blue; stems, and the lyric that lives in the same group, do not."""
    from src.scrollvideo.geometry import MARKER, _mark_notes
    root = etree.fromstring(_mark_notes(MINI_SVG).encode())
    ns = {"s": "http://www.w3.org/2000/svg"}
    head = root.find(".//s:g[@class='notehead']/s:use", ns)
    stem = root.find(".//s:g[@class='stem']/s:path", ns)
    lyric = root.find(".//s:g[@class='verse']/s:text", ns)
    # an inline style, because verovio's stylesheet outranks fill/stroke attributes
    assert MARKER.lower() in (head.get("style") or "").lower()
    assert MARKER.lower() not in (stem.get("style") or "").lower()
    assert MARKER.lower() not in (lyric.get("style") or "").lower()


def test_playing_marking_paints_rests_too():
    from src.scrollvideo.geometry import MARKER, _mark_playing
    root = etree.fromstring(_mark_playing(MINI_SVG).encode())
    ns = {"s": "http://www.w3.org/2000/svg"}
    rest = root.find(".//s:g[@class='mRest']/s:use", ns)
    assert MARKER.lower() in (rest.get("style") or "").lower()


def test_coverage_marks_the_note_and_leaves_the_staff_lines_alone(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    from src.scrollvideo.geometry import note_coverage
    coverage = note_coverage(eng.svg, eng.layout, 240)
    strip = rasterise(eng.svg, eng.layout, 240)
    ink = (strip < 128).any(axis=2)
    assert coverage.shape == ink.shape
    assert (coverage > 0).any(), "no note glyphs marked"
    assert (coverage > 0).sum() < ink.sum(), "everything got marked, not just notes"
    # nothing may be marked where there is no ink at all
    assert not ((coverage > 200) & ~ink).any()


def test_playing_coverage_adds_rest_glyphs(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    note_only = geometry.note_coverage(eng.svg, eng.layout, 240)
    with_rests = geometry.playing_coverage(eng.svg, eng.layout, 240)
    assert (with_rests > 0).sum() > (note_only > 0).sum()


def test_rest_boxes_cover_the_entire_real_verovio_glyph(fermata_musicxml):
    _assert_rest_boxes_cover_glyphs(engrave(fermata_musicxml))


def _assert_rest_boxes_cover_glyphs(eng):
    baseline = geometry._coverage(eng.svg, eng.layout, 240)
    rest_coverage = geometry._coverage(geometry._mark_rests(eng.svg), eng.layout, 240)
    inside = np.zeros_like(rest_coverage, dtype=bool)
    scale = 240 / eng.layout.height
    for rest in eng.layout.rests.values():
        x0, y0, x1, y1 = rest.box()
        inside[max(0, int(y0 * scale)):int(np.ceil(y1 * scale)),
               max(0, int(x0 * scale)):int(np.ceil(x1 * scale))] = True
    assert not ((rest_coverage > baseline) & ~inside).any()
