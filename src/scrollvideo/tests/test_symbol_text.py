"""Music symbols written inside text — the note in a tempo mark.

Verovio leaves those to the renderer's fonts, and ours has no music font, so
they used to come out as the empty box a font draws for a character it lacks.
"""

import numpy as np
from lxml import etree

from src.scrollvideo.engrave import draw_symbol_text, engrave
from src.scrollvideo.geometry import SVG_NS, parse_layout, rasterise

TEMPO_MARK = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1"><part-name>Soprano</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <direction placement="above">
        <direction-type>
          <metronome><beat-unit>quarter</beat-unit><per-minute>80</per-minute></metronome>
        </direction-type>
        <sound tempo="80"/>
      </direction>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>4</duration><voice>1</voice><type>whole</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""

# One tempo mark, laid out the way verovio lays one out: the note first, then
# the writing that follows it, each run carrying its own size.
MARK_SVG = """<svg width="100px" height="20px" xmlns="http://www.w3.org/2000/svg">
  <svg class="definition-scale" viewBox="0 0 2500 500">
    <g class="tempo">
      <text x="1000" y="200" font-size="0px">
        <tspan class="rend"><tspan class="text">
          <tspan font-family="Leipzig" font-size="720px">\ue1d5</tspan>
        </tspan></tspan>
        <tspan class="text"><tspan font-size="405px"> = 80</tspan></tspan>
      </text>
    </g>
  </svg>
</svg>"""


def _spans(svg):
    root = etree.fromstring(svg.encode())
    return list(root.iter(f"{{{SVG_NS}}}tspan"))


def _symbols(svg):
    root = etree.fromstring(svg.encode())
    return [g for g in root.iter(f"{{{SVG_NS}}}g")
            if g.get("class") == "symbol-text"]


def _without_symbols(svg):
    """The same page with the drawn symbols taken out again."""
    root = etree.fromstring(svg.encode())
    for group in [g for g in root.iter(f"{{{SVG_NS}}}g")
                  if g.get("class") == "symbol-text"]:
        group.getparent().remove(group)
    return etree.tostring(root, encoding="unicode")


def test_a_tempo_note_is_drawn_rather_than_left_to_a_font(tmp_path):
    """Nothing in the engraved page asks the renderer for a music character."""
    score = tmp_path / "tempo.musicxml"
    score.write_text(TEMPO_MARK)
    svg = engrave(str(score)).svg

    left_to_the_font = [span.text for span in _spans(svg)
                        if any("\ue000" <= c <= "\uf8ff" for c in span.text or "")]
    assert not left_to_the_font, f"still written as font characters: {left_to_the_font}"
    assert _symbols(svg), "the tempo mark's note was not drawn"


def test_the_drawn_note_is_ink_on_the_page(tmp_path):
    """Drawn where verovio put it — an outline placed wrong shows as blank paper.

    Measured against the same page with the drawing taken out again, so the
    reading cannot be satisfied by the "= 80" printed beside it.
    """
    score = tmp_path / "tempo.musicxml"
    score.write_text(TEMPO_MARK)
    svg = engrave(str(score)).svg
    layout = parse_layout(svg)

    height = 1600
    without = _without_symbols(svg)
    added = (rasterise(svg, layout, height) != rasterise(without, layout, height))

    rows, _ = np.nonzero(added.any(axis=2))
    assert len(rows), "the drawn note left no ink on the page"
    # A tempo mark is printed above the music, and that is where it landed.
    assert rows.max() < layout.staff_tops[0] * height / layout.height


def test_the_writing_after_a_symbol_moves_along_by_its_width():
    """The "= 80" keeps the gap verovio measured with the font's own advance."""
    drawn = draw_symbol_text(MARK_SVG)
    (following,) = [span for span in _spans(drawn) if (span.text or "").strip()]

    # metNoteQuarterUp advances 301 font units; the run is set at 720px on an
    # em of 1000, so the pen has moved 301 * 0.72 by the time the "=" is written.
    assert following.get("x") == "1217"


def test_a_symbol_that_follows_writing_is_left_alone():
    """We cannot know where the pen is without measuring text in the renderer's
    own font, and a symbol drawn in the wrong place is worse than a box."""
    after_writing = MARK_SVG.replace(
        '<tspan class="rend">',
        '<tspan class="text"><tspan font-size="405px">Andante </tspan></tspan>'
        '<tspan class="rend">')

    assert draw_symbol_text(after_writing) == after_writing


def test_a_page_with_no_symbols_in_its_text_is_untouched():
    plain = MARK_SVG.replace("\ue1d5", "Andante")
    assert draw_symbol_text(plain) == plain
