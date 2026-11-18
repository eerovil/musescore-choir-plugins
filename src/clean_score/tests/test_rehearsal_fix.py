"""The `rehearsal` fix (#480): put a rehearsal mark the scan cannot read on a bar.

homr has no output for rehearsal marks at all, so a scanned score reaches the app
without the letters singers count bars from. This kind writes one where MuseScore
keeps it: on the top staff that sings, in the first voice, ahead of the bar's first note.
"""
import json
import os
import shutil

import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, apply_fixes

TEST_FILES = os.path.join(os.path.dirname(__file__), "test_files")


def _bar(sig=""):
    return (f"<Measure><voice>{sig}<Chord><durationType>whole</durationType>"
            f"<Note><pitch>60</pitch><tpc>14</tpc></Note></Chord></voice></Measure>")


def _score():
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "S"), (2, "A")))
    staves = "".join(
        f'<Staff id="{sid}">{_bar("<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>")}'
        f'{_bar()}{_bar()}</Staff>' for sid in (1, 2))
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


MARK = {"kind": "rehearsal", "measure": 2, "text": "B", "why": "the page prints B at bar 2"}


def _marks(root):
    return [(staff.get("id"), number, mark.findtext("text"))
            for staff in root.findall("Score/Staff")
            for number, bar in enumerate(staff.findall("Measure"), start=1)
            for mark in bar.iter("RehearsalMark")]


def test_the_mark_lands_on_the_top_staff_ahead_of_the_first_note():
    root = _score()
    done = apply_fixes(root, [MARK])
    assert _marks(root) == [("1", 2, "B")]
    voice = root.find("Score/Staff").findall("Measure")[1].find("voice")
    assert [child.tag for child in voice] == ["RehearsalMark", "Chord"]
    assert "rehearsal mark 'B'" in done[0]


def test_a_top_staff_of_only_rests_is_passed_over():
    """A click staff on top is dropped from the publish copy, and the mark with it."""
    root = _score()
    for chord in root.find("Score/Staff").iter("Chord"):
        rest = etree.Element("Rest")
        etree.SubElement(rest, "durationType").text = "measure"
        chord.getparent().replace(chord, rest)
    done = apply_fixes(root, [MARK])
    assert _marks(root) == [("2", 2, "B")]
    assert "on staff 2" in done[0]


def test_a_drum_staff_on_top_is_passed_over_though_it_has_notes():
    """Publishing drops percussion even with notes in it, so the mark goes on the
    first singer (the review's case on #480: a drumset part above the choir)."""
    root = _score()
    root.find("Score/Part").append(etree.fromstring(
        "<Instrument id='drumset'><useDrumset>1</useDrumset></Instrument>"))
    done = apply_fixes(root, [MARK])
    assert _marks(root) == [("2", 2, "B")]
    assert "on staff 2" in done[0]


def test_it_follows_the_publish_rule():
    """The fix and the publish copy read one rule: the staff marked is a kept part's."""
    from src.score_export.score import silent_parts
    root = _score()
    root.find("Score/Part").append(etree.fromstring("<Instrument id='drumset'/>"))
    assert silent_parts(root) == ["S"]
    apply_fixes(root, [MARK])
    assert _marks(root) == [("2", 2, "B")]


def test_it_goes_after_a_signature_at_the_head_of_the_bar():
    root = _score()
    apply_fixes(root, [{**MARK, "measure": 1, "text": "A"}])
    voice = root.find("Score/Staff").find("Measure/voice")
    assert [child.tag for child in voice] == ["TimeSig", "RehearsalMark", "Chord"]


def test_a_bar_that_already_has_one_refuses():
    root = _score()
    apply_fixes(root, [MARK])
    with pytest.raises(FixError, match=r"m2 \(rehearsal\): staff 1 already has rehearsal mark 'B'"):
        apply_fixes(root, [{**MARK, "text": "C"}])
    assert _marks(root) == [("1", 2, "B")]


def test_one_on_a_lower_staff_refuses_too():
    root = _score()
    lower = root.findall("Score/Staff")[1].findall("Measure")[1].find("voice")
    lower.insert(0, etree.fromstring("<RehearsalMark><text>B</text></RehearsalMark>"))
    with pytest.raises(FixError, match="staff 2 already has"):
        apply_fixes(root, [MARK])


@pytest.mark.parametrize("text", [None, "", "  "])
def test_text_is_required(text):
    with pytest.raises(FixError, match="'text'"):
        apply_fixes(_score(), [{**MARK, "text": text}])


def test_a_bar_past_the_end_refuses():
    with pytest.raises(FixError, match="no measure 9"):
        apply_fixes(_score(), [{**MARK, "measure": 9}])


def test_a_recorded_entry_replays_on_a_rebuild(tmp_path):
    from src.song_app.pipeline import apply_recorded_fixes
    (tmp_path / "fixes.json").write_text(json.dumps([MARK]))
    cleaned = tmp_path / "s_cleaned.mscx"
    for _ in range(2):          # a re-clean writes the score afresh, and the mark comes back
        etree.ElementTree(_score()).write(str(cleaned))
        assert apply_recorded_fixes(str(cleaned), str(tmp_path)) == 1
        assert _marks(etree.parse(str(cleaned)).getroot()) == [("1", 2, "B")]


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_the_publish_copy_keeps_it(tmp_path):
    from src.score_export import musescore, score
    src = tmp_path / "song_cleaned.mscx"
    shutil.copy(os.path.join(TEST_FILES, "medium_1_output.mscx"), src)
    root = etree.parse(str(src)).getroot()
    apply_fixes(root, [MARK])
    etree.ElementTree(root).write(str(src), encoding="UTF-8", xml_declaration=True)
    copy, _ = score.prepare(str(src), str(tmp_path))
    out = musescore.run_musescore(copy, str(tmp_path / "score.musicxml"))
    part = etree.parse(out).getroot().find("part")
    found = [(m.get("number"), r.text) for m in part.findall("measure") for r in m.iter("rehearsal")]
    assert found == [("2", "B")]
