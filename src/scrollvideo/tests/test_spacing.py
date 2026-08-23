"""The spacing staff: measure width should follow beats, not note density."""

import pytest
from lxml import etree

from src.scrollvideo.geometry import Layout, NoteGeom
from src.scrollvideo.spacing import SPACER_ID, add_spacer_staff, visible_height

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


def _spacer(tmp_path, per_quarter=2, xml=SCORE):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "in.musicxml"
    source.write_text(xml)
    out = tmp_path / "out.musicxml"
    result = add_spacer_staff(str(source), str(out), per_quarter)
    return result, etree.parse(str(out)).getroot() if result else None


def test_a_spacer_part_is_appended(tmp_path):
    _, root = _spacer(tmp_path)
    ids = [p.get("id") for p in root.findall("part")]
    assert ids == ["P1", SPACER_ID]
    assert [sp.get("id") for sp in root.find("part-list").findall("score-part")][-1] == SPACER_ID


def test_each_measure_gets_rests_in_proportion_to_its_length(tmp_path):
    """This is the whole point: slots per measure follow beats."""
    _, root = _spacer(tmp_path, per_quarter=2)          # eighths: 2 per quarter
    spacer = root.findall("part")[-1]
    counts = [len(m.findall("note")) for m in spacer.findall("measure")]
    assert counts == [8, 4]                              # 4/4 bar, then a half-length bar


def test_a_chord_does_not_lengthen_a_measure(tmp_path):
    """Measure 2's chord note shares its beat; it must not add a rest slot."""
    _, root = _spacer(tmp_path, per_quarter=2)
    spacer = root.findall("part")[-1]
    assert len(spacer.findall("measure")[1].findall("note")) == 4


def test_subdivision_scales_the_slot_count(tmp_path):
    for per_quarter, expected in ((1, 4), (2, 8), (4, 16)):
        _, root = _spacer(tmp_path / f"q{per_quarter}", per_quarter)
        spacer = root.findall("part")[-1]
        assert len(spacer.findall("measure")[0].findall("note")) == expected


def test_the_spacer_holds_only_rests(tmp_path):
    _, root = _spacer(tmp_path)
    spacer = root.findall("part")[-1]
    for note in spacer.iter("note"):
        assert note.find("rest") is not None
        assert note.find("pitch") is None


def test_the_singing_parts_are_untouched(tmp_path):
    _, root = _spacer(tmp_path)
    original = etree.fromstring(SCORE.encode())
    assert (etree.tostring(root.find("part[@id='P1']"))
            == etree.tostring(original.find("part[@id='P1']")))


def test_an_unusable_subdivision_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _spacer(tmp_path, per_quarter=3)


def test_too_coarse_divisions_give_up_rather_than_guess(tmp_path):
    """divisions=1 cannot be split into sixteenths; the caller engraves as-is."""
    coarse = SCORE.replace("<divisions>4</divisions>", "<divisions>1</divisions>")
    result, _ = _spacer(tmp_path, per_quarter=4, xml=coarse)
    assert result is None


def _layout(staff_tops, spacing=100.0, height=1000.0):
    notes = {f"n{i}": NoteGeom(0.0, top + 50, top, spacing)
             for i, top in enumerate(staff_tops)}
    return Layout(width=5000.0, height=height, notes=notes, staff_tops=list(staff_tops))


def test_visible_height_cuts_just_above_the_spacer_staff():
    """Only enough margin to clear the staff line: the last singing staff's
    lyrics live in that gap and a generous margin clips them."""
    layout = _layout([100.0, 400.0, 800.0])
    assert visible_height(layout) == pytest.approx(800.0 - 15.0)


def test_visible_height_is_the_whole_page_when_there_is_no_spacer():
    assert visible_height(_layout([100.0])) == 1000.0
