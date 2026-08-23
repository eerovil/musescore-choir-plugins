"""Reading a verovio SVG: where the notes are, and turning the page into pixels."""

import numpy as np
import pytest

from src.scrollvideo import geometry
from src.scrollvideo.engrave import engrave
from src.scrollvideo.geometry import parse_layout, rasterise

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


def test_parse_layout_rejects_a_non_verovio_svg():
    with pytest.raises(ValueError):
        parse_layout('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>')


def test_every_engraved_note_that_sounds_has_a_position(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    sounding = {n for entry in eng.timemap for n in entry.get("on", [])}
    assert sounding, "fixture should contain notes"
    assert sounding <= set(eng.layout.notes), "a sounding note had no geometry"


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
