"""Leaving out staves that carry no music."""

from lxml import etree

from src.scrollvideo.score import drop_parts, prepare, silent_parts

SCORE = """<museScore><Score>
  <Part><trackName>T1</trackName><Staff id="1"/></Part>
  <Part><trackName>Drumset</trackName><Staff id="2"/>
    <Instrument id="drumset"><useDrumset>1</useDrumset></Instrument></Part>
  <Part><trackName>Click</trackName><Staff id="3"/></Part>
  <Staff id="1"><Measure><voice><Chord><Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>
  <Staff id="2"><Measure><voice><Rest/></voice></Measure></Staff>
  <Staff id="3"><Measure><voice><Rest/></voice></Measure></Staff>
</Score></museScore>"""

SINGING_ONLY = """<museScore><Score>
  <Part><trackName>T1</trackName><Staff id="1"/></Part>
  <Staff id="1"><Measure><voice><Chord><Note><pitch>60</pitch></Note></Chord></voice></Measure></Staff>
</Score></museScore>"""


def test_percussion_and_rest_only_staves_are_silent():
    assert silent_parts(etree.fromstring(SCORE)) == ["Drumset", "Click"]


def test_a_singing_part_is_never_silent():
    assert silent_parts(etree.fromstring(SINGING_ONLY)) == []


def test_dropping_a_part_takes_its_staff_with_it():
    root = etree.fromstring(SCORE)
    assert drop_parts(root, ["Drumset", "Click"]) == 2
    assert [p.findtext("trackName") for p in root.iter("Part")] == ["T1"]
    assert [s.get("id") for s in root.find("Score").findall("Staff")] == ["1"]


def test_prepare_leaves_the_original_file_alone(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SCORE)
    before = original.read_text()

    path, dropped = prepare(str(original), str(tmp_path))
    assert dropped == ["Drumset", "Click"]
    assert path != str(original)
    assert original.read_text() == before


def test_prepare_uses_the_score_as_is_when_there_is_nothing_to_drop(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SINGING_ONLY)
    path, dropped = prepare(str(original), str(tmp_path))
    assert (path, dropped) == (str(original), [])


def test_keep_silent_skips_the_whole_thing(tmp_path):
    original = tmp_path / "score.mscx"
    original.write_text(SCORE)
    assert prepare(str(original), str(tmp_path), keep_silent=True) == (str(original), [])
