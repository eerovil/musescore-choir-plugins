"""Regression coverage for rhythmic-column alignment in scrolling videos."""

from src.scrollvideo.engrave import engrave


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


def test_half_whole_notes_and_half_rest_share_the_same_rhythmic_column(tmp_path):
    """Different glyph widths must not make the video's beat rectangle jump."""
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
            by_staff[eng.layout.staff_index(geom)] = geom

    labels = {0: "quarter note", 1: "half note", 2: "whole note", 3: "half rest"}
    assert set(by_staff) >= set(labels), (
        "fixture did not produce all four simultaneous symbols: "
        f"{sorted(by_staff)}")

    xs = {labels[i]: by_staff[i].x for i in labels}
    spacing = min(by_staff[i].staff_spacing for i in labels)
    assert max(xs.values()) - min(xs.values()) <= 0.05 * spacing, (
        "simultaneous glyphs are not in one vertical rhythmic column; "
        f"x positions: {xs}, staff spacing: {spacing}")
