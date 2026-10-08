"""The red marks cleaning leaves on a bar it had to change (#238).

A mark is a red staff text starting with `⚠`, put in the bar where somebody fixing the
score in MuseScore is looking. Deleting it is how they say the bar is done: until then
health lists it and the Fix panel reads it off the score, and the scrolling video never shows it.
"""
import json
import os

from lxml import etree

from src.clean_score.utils.problem_marks import PREFIX, mark_bar, marks, strip_marks
from src.scrollvideo import score as score_mod
from src.song_app import health, pipeline


def _score():
    staff = ('<Staff id="{0}"><Measure><voice><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>'
             '<Chord><durationType>whole</durationType><Note><pitch>60</pitch></Note></Chord>'
             '</voice></Measure><Measure><voice><Chord><durationType>whole</durationType>'
             '<Note><pitch>62</pitch></Note></Chord></voice></Measure></Staff>')
    return etree.fromstring(
        ('<museScore><Score><Part><trackName>T1</trackName><Staff id="1"/></Part>'
         '<Part><trackName>B1</trackName><Staff id="2"/></Part>'
         f'{staff.format(1)}{staff.format(2)}</Score></museScore>').encode())


def _bar(root, staff, bar):
    return root.findall(".//Score/Staff")[staff - 1].findall("Measure")[bar - 1]


def _write(root, path):
    etree.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True)
    return str(path)


def test_a_mark_is_red_starts_with_the_warning_sign_and_sits_before_the_music():
    root = _score()
    assert mark_bar(_bar(root, 2, 2), "check this")
    voice = _bar(root, 2, 2).find("voice")
    assert voice[0].tag == "StaffText"
    assert voice[0].find("color").attrib == {"r": "255", "g": "0", "b": "0", "a": "255"}
    assert voice[0].findtext("text") == PREFIX + "check this"
    assert marks(root) == [{"staff": 2, "measure": 2, "text": "check this"}]


def test_the_same_mark_twice_is_one_mark():
    root = _score()
    mark_bar(_bar(root, 1, 2), "check this")
    assert not mark_bar(_bar(root, 1, 2), "check this")
    assert len(marks(root)) == 1


def test_stripping_takes_marks_and_leaves_printed_text():
    root = _score()
    mark_bar(_bar(root, 1, 2), "check this")
    printed = etree.SubElement(_bar(root, 1, 1).find("voice"), "StaffText")
    etree.SubElement(printed, "text").text = "dolce"
    assert strip_marks(root) == 1
    assert marks(root) == []
    assert [t.findtext("text") for t in root.iter("StaffText")] == ["dolce"]


def test_health_lists_a_mark_until_somebody_deletes_it(tmp_path):
    root = _score()
    mark_bar(_bar(root, 2, 2), "bar was 9/8 under 4/4, cut to 4/4")
    path = _write(root, tmp_path / "s_cleaned.mscx")
    found = [i for i in health.scan(path) if i["kind"] == "marked-problem"]
    assert [(i["id"], i["measure"], i["staff"], i["detail"]) for i in found] == [
        ("marked-m2-s2", 2, "B1", "bar was 9/8 under 4/4, cut to 4/4")]
    strip_marks(root)
    _write(root, path)
    assert [i for i in health.scan(path) if i["kind"] == "marked-problem"] == []


def test_two_marks_on_one_bar_are_two_rows(tmp_path):
    root = _score()
    mark_bar(_bar(root, 1, 2), "one")
    mark_bar(_bar(root, 1, 2), "two")
    path = _write(root, tmp_path / "s_cleaned.mscx")
    assert [i["id"] for i in health.scan(path) if i["kind"] == "marked-problem"] == [
        "marked-m2-s1", "marked-m2-s1-2"]


def test_a_clean_writes_no_copy_of_its_marks_and_takes_old_copies_away(tmp_path):
    """The Fix panel reads the marks off the score (#290), so a copy in fixes.json only
    buried the real fixes and outlived the marks it copied (#347)."""
    typed = {"kind": "text", "what": "somebody typed this"}
    old_copy = {"kind": "text", "source": pipeline.CLEAN_MARK_SOURCE, "measure": 2,
                "staff": 1, "what": "Bar 2, T1 (red mark in the score): first reading"}
    (tmp_path / "fixes.json").write_text(json.dumps([typed, old_copy]))
    root = _score()
    mark_bar(_bar(root, 1, 2), "first reading")
    path = _write(root, tmp_path / "s_cleaned.mscx")
    said = []
    assert pipeline.record_clean_marks(path, str(tmp_path), said.append) == 1
    assert json.loads((tmp_path / "fixes.json").read_text()) == [typed]
    assert pipeline.free_text_fixes(str(tmp_path)) == ["somebody typed this"]
    assert said == ["  Bar 2, T1 (red mark in the score): first reading"]


def test_a_clean_leaves_a_fixes_file_with_no_copies_untouched(tmp_path):
    (tmp_path / "fixes.json").write_text('[{"kind": "text", "what": "typed"}]')
    root = _score()
    mark_bar(_bar(root, 1, 2), "check")
    pipeline.record_clean_marks(_write(root, tmp_path / "s_cleaned.mscx"), str(tmp_path))
    assert (tmp_path / "fixes.json").read_text() == '[{"kind": "text", "what": "typed"}]'


def test_a_bar_musescore_rejects_is_marked_where_it_was_reset(tmp_path):
    root = _score()
    path = _write(root, tmp_path / "s.mscx")
    pipeline.reset_rejected_bars(path, [{"measure": 2, "staff": 2, "voice": 1,
                                         "message": "Measure 2, staff 2 incomplete."}])
    [mark] = marks(etree.parse(path).getroot())
    assert (mark["staff"], mark["measure"]) == (2, 2)
    assert "Taken out: D4" in mark["text"]


def test_the_video_never_shows_a_mark(tmp_path):
    root = _score()
    mark_bar(_bar(root, 1, 2), "check this")
    path = _write(root, tmp_path / "s_cleaned.mscx")
    rendered, _ = score_mod.prepare(path, str(tmp_path), keep_silent=True)
    assert rendered != path
    assert marks(etree.parse(rendered).getroot()) == []
    # The source keeps it: the mark is for the person, only the render drops it.
    assert len(marks(etree.parse(path).getroot())) == 1


def test_the_video_never_shows_a_red_note(tmp_path):
    """A note a warning coloured red (#274) and nobody turned back stays red in the
    score, and plays black in the practice video."""
    root = _score()
    note = root.find(".//Note")
    etree.SubElement(note, "color", r="255", g="0", b="0", a="255")
    path = _write(root, tmp_path / "s_cleaned.mscx")
    rendered, _ = score_mod.prepare(path, str(tmp_path), keep_silent=True)
    assert rendered != path
    assert etree.parse(rendered).getroot().find(".//Note/color") is None
    assert etree.parse(path).getroot().find(".//Note/color") is not None
