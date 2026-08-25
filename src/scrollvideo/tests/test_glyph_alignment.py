"""Regression coverage for beat-marker stability across glyph types."""

import numpy as np

from src.scrollvideo.engrave import engrave
from src.scrollvideo.timing import NoteEvent
from src.scrollvideo.video import blend_beat_marker, place


MIXED_DURATIONS = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1">
  <part-list>
    <score-part id="P1"><part-name>Quarter</part-name></score-part>
    <score-part id="P2"><part-name>Half</part-name></score-part>
    <score-part id="P3"><part-name>Whole</part-name></score-part>
    <score-part id="P4"><part-name>Half rest</part-name></score-part>
  </part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><voice>1</voice><type>quarter</type>
      </note>
      <note><rest/><duration>3</duration><voice>1</voice><type>half</type><dot/></note>
    </measure>
  </part>
  <part id="P2">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>2</duration><voice>1</voice><type>half</type>
      </note>
      <note><rest/><duration>2</duration><voice>1</voice><type>half</type></note>
    </measure>
  </part>
  <part id="P3">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>4</duration><voice>1</voice><type>whole</type>
      </note>
    </measure>
  </part>
  <part id="P4">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        <clef><sign>G</sign><line>2</line></clef>
      </attributes>
      <note><rest/><duration>2</duration><voice>1</voice><type>half</type></note>
      <note>
        <pitch><step>C</step><octave>4</octave></pitch>
        <duration>2</duration><voice>1</voice><type>half</type>
      </note>
    </measure>
  </part>
</score-partwise>
"""


def _marker_columns(marker, width):
    frame = np.full((8, width, 3), 255, dtype=np.uint8)
    blend_beat_marker(frame, marker, left=0)
    changed = np.flatnonzero((frame != 255).any(axis=(0, 2)))
    assert len(changed), "beat marker did not draw"
    return int(changed[0]), int(changed[-1])


def test_half_whole_notes_and_half_rest_do_not_move_the_video_rectangle(tmp_path):
    """The beat rectangle stays on one time column regardless of glyph shape.

    This deliberately tests the marker geometry the video actually uses, not the
    visual centre of each music glyph. Whole noteheads are intrinsically wider than
    quarter/half noteheads, but changing note duration must not make the marker jump.

    Whole rests are intentionally outside this regression: they are a known separate
    failing case and were not part of issue #47's requested glyph set.
    """
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
            by_staff[eng.layout.staff_index(geom)] = drawn_id

    labels = {0: "quarter note", 1: "half note", 2: "whole note", 3: "half rest"}
    assert set(by_staff) >= set(labels), (
        "fixture did not produce all four simultaneous symbols: "
        f"{sorted(by_staff)}")

    markers = {}
    for staff, label in labels.items():
        (marker,) = place([NoteEvent(by_staff[staff], 0.0, 1.0)],
                          eng.layout, px_per_unit=1.0)
        assert marker.snap_marker, f"{label} would not move the beat marker"
        markers[label] = marker

    # `place()` is the source of the rectangle used by render(). All four glyphs
    # must yield the same horizontal centre and width at the same musical instant.
    centers = {label: (marker.x0 + marker.x1) / 2 for label, marker in markers.items()}
    widths = {label: marker.x1 - marker.x0 for label, marker in markers.items()}
    assert len(set(centers.values())) == 1, f"marker centres differ: {centers}"
    assert len(set(widths.values())) == 1, f"marker widths differ: {widths}"

    # Pin the final rectangle drawing too, so a future change in blend_beat_marker
    # cannot reintroduce a glyph-dependent jump after placement has aligned them.
    frame_width = max(marker.x1 for marker in markers.values()) + 100
    columns = {label: _marker_columns(marker, frame_width)
               for label, marker in markers.items()}
    assert len(set(columns.values())) == 1, f"drawn marker columns differ: {columns}"
