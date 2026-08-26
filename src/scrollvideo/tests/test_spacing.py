"""The spacing staff: cap scroll-speed jumps without widening everything."""

from types import SimpleNamespace

import pytest
from lxml import etree

from src.scrollvideo.engrave import engrave
from src.scrollvideo.geometry import Layout, NoteGeom
from src.scrollvideo.spacing import (DEFAULT_PER_QUARTER, MAX_WIDTH_RATIO, SPACER_ID,
                                     _bar_widths, _measure_length,
                                     _minimum_normalized_widths,
                                     _write_spacer_staff, add_spacer_staff,
                                     visible_height)

SCORE = """<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>T1</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>16</duration></note>
    </measure>
    <measure number="2">
      <note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration></note>
      <note><chord/><pitch><step>F</step><octave>4</octave></pitch><duration>4</duration></note>
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration></note>
    </measure>
  </part>
</score-partwise>"""


def _source(tmp_path, xml=SCORE):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "in.musicxml"
    source.write_text(xml)
    return source


def _fixed_spacer(tmp_path, per_quarter=DEFAULT_PER_QUARTER, xml=SCORE, levels=None):
    source = _source(tmp_path, xml)
    count = len(etree.fromstring(xml.encode()).find("part").findall("measure"))
    levels = [per_quarter] * count if levels is None else levels
    out = tmp_path / "out.musicxml"
    result = _write_spacer_staff(str(source), str(out), levels, per_quarter)
    return result, etree.parse(str(out)).getroot() if result else None


def _adaptive_spacer(tmp_path, xml):
    source = _source(tmp_path, xml)
    out = tmp_path / "out.musicxml"
    result = add_spacer_staff(str(source), str(out))
    return source, result, etree.parse(str(out)).getroot() if result else None


def _note(duration, note_type="quarter", step="C"):
    return (f"<note><pitch><step>{step}</step><octave>4</octave></pitch>"
            f"<duration>{duration}</duration><type>{note_type}</type></note>")


def _two_bars(first_notes, second_notes, divisions=8):
    return f"""<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>T1</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>{divisions}</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      {first_notes}
    </measure>
    <measure number="2">{second_notes}</measure>
  </part>
</score-partwise>"""


def test_a_spacer_part_is_appended(tmp_path):
    _, root = _fixed_spacer(tmp_path)
    ids = [p.get("id") for p in root.findall("part")]
    assert ids == ["P1", SPACER_ID]
    assert [sp.get("id") for sp in root.find("part-list").findall("score-part")][-1] == SPACER_ID


def test_each_measure_gets_rests_in_proportion_to_its_length(tmp_path):
    """A fixed grid still follows musical duration, which adaptive levels rely on."""
    _, root = _fixed_spacer(tmp_path, per_quarter=2)
    spacer = root.findall("part")[-1]
    counts = [len(m.findall("note")) for m in spacer.findall("measure")]
    assert counts == [8, 4]  # four quarters, then two quarters


def test_a_chord_does_not_lengthen_a_measure(tmp_path):
    _, root = _fixed_spacer(tmp_path, per_quarter=2)
    spacer = root.findall("part")[-1]
    assert len(spacer.findall("measure")[1].findall("note")) == 4


def test_multiple_voices_do_not_double_measure_length():
    measure = etree.fromstring("""<measure>
      <note><duration>4</duration></note><note><duration>4</duration></note>
      <backup><duration>8</duration></backup>
      <note><duration>4</duration></note><note><duration>4</duration></note>
    </measure>""")
    assert _measure_length(measure) == 8


def test_subdivision_scales_the_slot_count(tmp_path):
    for per_quarter, expected in ((1, 4), (2, 8), (4, 16), (8, 32)):
        _, root = _fixed_spacer(tmp_path / f"q{per_quarter}", per_quarter)
        spacer = root.findall("part")[-1]
        assert len(spacer.findall("measure")[0].findall("note")) == expected


def test_level_zero_has_duration_but_no_spacing_floor(tmp_path):
    _, root = _fixed_spacer(tmp_path, levels=[0, 0])
    spacer = root.findall("part")[-1]
    for measure in spacer.findall("measure"):
        notes = measure.findall("note")
        assert len(notes) == 1
        assert notes[0].find("rest").get("measure") == "yes"
        assert notes[0].get("print-object") == "no"
        assert notes[0].get("print-spacing") == "no"


def test_minimum_width_envelope_changes_only_as_far_as_the_cap_requires():
    target = _minimum_normalized_widths([100, 100, 300, 100, 100], [1] * 5, 1.30)
    assert target == pytest.approx([
        300 / 1.30**2,
        300 / 1.30,
        300,
        300 / 1.30,
        300 / 1.30**2,
    ])


def test_width_cap_is_about_width_per_quarter_not_raw_bar_width():
    target = _minimum_normalized_widths([200, 100], [2, 1], 1.30)
    assert target == pytest.approx([100, 100])


def test_grid_overshoot_is_remeasured_and_propagated(tmp_path, monkeypatch):
    """A discrete spacer may widen more than requested; the cap must follow reality.

    Natural widths are 100/200 per quarter, so the first bar initially needs only
    153.8. After its first grid is added, pretend Verovio also makes the second bar
    jump to 300. The old one-shot target would stop at first-bar width 160 and ship
    a 1.875x jump. Re-measuring must promote the first bar again to 240/300 = 1.25x.
    """
    quarters = _note(8) * 4
    source = _source(tmp_path, _two_bars(quarters, quarters))
    out = tmp_path / "out.musicxml"
    measured = iter(([400.0, 800.0], [640.0, 1200.0], [960.0, 1200.0]))
    writes = []

    monkeypatch.setattr("src.scrollvideo.spacing.engrave",
                        lambda _path: SimpleNamespace(svg="candidate"))
    monkeypatch.setattr("src.scrollvideo.spacing._bar_widths",
                        lambda _svg: list(next(measured)))

    def fake_write(_source_path, out_path, levels, _per_quarter):
        writes.append(list(levels))
        return out_path

    monkeypatch.setattr("src.scrollvideo.spacing._write_spacer_staff", fake_write)

    assert add_spacer_staff(str(source), str(out)) == str(out)
    assert writes[:2] == [[1, 0], [2, 0]]
    assert writes[-1] == [2, 0]


def test_sparse_score_is_not_widened_for_a_problem_it_does_not_have(tmp_path):
    quarters = _note(8) * 4
    score = _two_bars(quarters, quarters)
    source, spaced, root = _adaptive_spacer(tmp_path, score)

    # No approximation here: if the natural widths already satisfy the cap, the
    # minimum-width answer is to add no spacer staff and engrave the source itself.
    assert spaced == ""
    assert root is None
    natural = _bar_widths(engrave(str(source)).svg)
    adaptive = _bar_widths(engrave(spaced or str(source)).svg)
    assert adaptive == natural


def test_adaptive_spacing_caps_the_real_four_note_to_32_note_jump(tmp_path):
    quarters = _note(8) * 4
    thirty_seconds = _note(1, "32nd") * 32
    score = _two_bars(quarters, thirty_seconds)
    _source_path, spaced, _root = _adaptive_spacer(tmp_path, score)

    widths = _bar_widths(engrave(spaced).svg)
    assert max(widths) / min(widths) <= MAX_WIDTH_RATIO * 1.01


def test_the_spacer_holds_only_rests(tmp_path):
    _, root = _fixed_spacer(tmp_path)
    spacer = root.findall("part")[-1]
    for note in spacer.iter("note"):
        assert note.find("rest") is not None
        assert note.find("pitch") is None


def test_the_singing_parts_are_untouched(tmp_path):
    _, root = _fixed_spacer(tmp_path)
    original = etree.fromstring(SCORE.encode())
    assert (etree.tostring(root.find("part[@id='P1']"))
            == etree.tostring(original.find("part[@id='P1']")))


def test_an_unusable_subdivision_is_refused(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError):
        add_spacer_staff(str(source), str(tmp_path / "out.musicxml"), 3)


def test_the_spacer_uses_divisions_that_can_express_source_and_grid(tmp_path):
    """A quarter-only source can still carry a 16th-note spacing grid."""
    coarse = SCORE.replace("<divisions>4</divisions>", "<divisions>1</divisions>") \
        .replace("<duration>16</duration>", "<duration>WHOLE</duration>") \
        .replace("<duration>4</duration>", "<duration>1</duration>") \
        .replace("<duration>WHOLE</duration>", "<duration>4</duration>")
    _result, root = _fixed_spacer(tmp_path, per_quarter=4, xml=coarse)
    spacer = root.findall("part")[-1]
    assert spacer.findtext(".//divisions") == "4"
    assert len(spacer.find("measure").findall("note")) == 16
    assert {note.findtext("duration") for note in spacer.iter("note")} == {"1"}


def test_source_division_changes_are_applied_to_the_measure_that_declares_them(tmp_path):
    changed = SCORE.replace(
        '<measure number="2">',
        '<measure number="2"><attributes><divisions>8</divisions></attributes>')

    _result, root = _fixed_spacer(tmp_path, per_quarter=8, xml=changed)
    spacer = root.findall("part")[-1]
    counts = [len(measure.findall("note")) for measure in spacer.findall("measure")]
    assert counts == [32, 8]


def test_time_signature_changes_are_copied_to_the_spacer(tmp_path):
    changed = SCORE.replace(
        '<measure number="2">',
        '<measure number="2"><attributes><time><beats>2</beats><beat-type>4</beat-type></time>'
        '</attributes>')
    _result, root = _fixed_spacer(tmp_path, xml=changed)
    spacer = root.findall("part")[-1]
    assert spacer.findall("measure")[1].findtext("attributes/time/beats") == "2"


def _layout(staff_tops, spacing=100.0, height=1000.0):
    notes = {f"n{i}": NoteGeom(0.0, top + 50, top, spacing)
             for i, top in enumerate(staff_tops)}
    return Layout(width=5000.0, height=height, notes=notes, staff_tops=list(staff_tops))


def test_visible_height_cuts_just_above_the_spacer_staff():
    layout = _layout([100.0, 400.0, 800.0])
    assert visible_height(layout) == pytest.approx(800.0 - 15.0)


def test_visible_height_is_the_whole_page_when_there_is_no_spacer():
    assert visible_height(_layout([100.0])) == 1000.0
