"""Regression coverage for rhythmic-column alignment in scrolling videos."""

import numpy as np
from lxml import etree

from src.scrollvideo import geometry
from src.scrollvideo.engrave import engrave


MIXED_DURATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1"><part-name>Quarter</part-name></score-part>
    <score-part id="P2"><part-name>Half</part-name></score-part>
    <score-part id="P3"><part-name>Whole</part-name></score-part>
    <score-part id="P4"><part-name>Half rest</part-name></score-part>
    <score-part id="P5"><part-name>Whole rest</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>5</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type>
      </note>
      <note><rest/><duration>4</duration><voice>1</voice><type>whole</type></note>
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>5</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>2</duration><voice>1</voice><type>half</type>
      </note>
      <note><rest/><duration>3</duration><voice>1</voice><type>half</type><dot/></note>
    </measure>
  </part>
  <part id="P3">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>5</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>4</duration><voice>1</voice><type>whole</type>
      </note>
      <note><rest/><duration>1</duration><voice>1</voice><type>quarter</type></note>
    </measure>
  </part>
  <part id="P4">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>5</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><rest/><duration>2</duration><voice>1</voice><type>half</type></note>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>3</duration><voice>1</voice><type>half</type><dot/>
      </note>
    </measure>
  </part>
  <part id="P5">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>5</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><rest/><duration>4</duration><voice>1</voice><type>whole</type></note>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""


def _rendered_glyph_center_x(eng, element_id: str, height_px: int = 480) -> float:
    """Actual horizontal centre of one rendered notehead/rest, in Verovio units."""
    root = etree.fromstring(eng.svg.encode())
    nodes = root.xpath(f".//*[@id='{element_id}']")
    assert len(nodes) == 1, f"expected one SVG node for {element_id}, got {len(nodes)}"
    node = nodes[0]

    # Notes: marker/scroll alignment follows the head, not stems/flags/beams.
    targets = [node]
    if node.get("class") == "note":
        targets = node.xpath(".//*[local-name()='g' and @class='notehead']")
        assert targets, f"note {element_id} has no notehead"

    for target in targets:
        for part in target.iter():
            if etree.QName(part).localname in geometry._DRAWN:
                part.set("style", geometry._MARK_STYLE)

    marked = etree.tostring(root, encoding="unicode")
    coverage = geometry._coverage(marked, eng.layout, height_px)
    cols = np.flatnonzero((coverage > 0).any(axis=0))
    assert len(cols), f"marked glyph {element_id} rendered no pixels"
    scale = height_px / eng.layout.height
    return ((cols[0] + cols[-1] + 1) / 2.0) / scale


def test_mixed_notes_and_rests_share_the_same_rendered_rhythmic_column(tmp_path):
    """Rendered glyphs, not just their SVG anchors, must share one time column."""
    score = tmp_path / "mixed-durations.musicxml"
    score.write_text(MIXED_DURATIONS)
    eng = engrave(str(score))

    first = next(entry for entry in eng.timemap
                 if float(entry.get("qstamp", -1)) == 0.0)
    timed_ids = (*first.get("on", []), *first.get("restsOn", []))

    by_staff = {}
    for timed_id in timed_ids:
        drawn_id = eng.drawn_id.get(timed_id, timed_id)
        geom = eng.layout.playing(drawn_id)
        if geom is not None:
            by_staff[eng.layout.staff_index(geom)] = (drawn_id, geom)

    labels = {
        0: "quarter note",
        1: "half note",
        2: "whole note",
        3: "half rest",
        4: "whole rest",
    }
    assert set(by_staff) >= set(labels), (
        "fixture did not produce all five simultaneous symbols: "
        f"{sorted(by_staff)}")

    whole_rest = by_staff[4][1]
    assert not getattr(whole_rest, "measure_rest", False), (
        "whole-rest fixture was rendered as a whole-measure rest; "
        "that would not test ordinary whole-rest alignment")

    centers = {
        labels[i]: _rendered_glyph_center_x(eng, by_staff[i][0])
        for i in labels
    }
    anchors = {labels[i]: by_staff[i][1].x for i in labels}
    spacing = min(by_staff[i][1].staff_spacing for i in labels)

    # The old anchor-only check can pass even when a glyph's own geometry is offset
    # inside its <use>. The rendered centres are what the video actually shows.
    assert max(centers.values()) - min(centers.values()) <= 0.10 * spacing, (
        "simultaneous rendered glyphs are not in one vertical rhythmic column; "
        f"rendered centers: {centers}, SVG anchors: {anchors}, staff spacing: {spacing}")
