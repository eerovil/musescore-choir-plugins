"""The `marker` and `jump` fixes: a coda or segno sign and a D.C. or D.S. jump.

homr reads neither, and some pages need one: Karjalan laulu (eeron-omat-taskit #292)
repeats bars 1-6 inside a repeat of 1-16, and MuseScore skips the outer "1." ending
on the way back, so no repeat sign plays it as printed. D.C. al Coda does.
"""
import os
import shutil

import pytest
from lxml import etree

from src.clean_score.utils.score_fixes import FixError, apply_fixes

SCORE_FILES = os.path.join(os.path.dirname(__file__), "..", "..", "score_export",
                           "tests", "test_files")


def _bar():
    return ("<Measure><voice><Chord><durationType>whole</durationType>"
            "<Note><pitch>60</pitch><tpc>14</tpc></Note></Chord></voice></Measure>")


def _score():
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "T"), (2, "B")))
    staves = "".join(f'<Staff id="{sid}">{_bar() * 4}</Staff>' for sid in (1, 2))
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


def _signs(root):
    return [(staff.get("id"), number, sign.tag, sign.findtext("text"))
            for staff in root.findall("Score/Staff")
            for number, bar in enumerate(staff.findall("Measure"), start=1)
            for sign in bar if sign.tag in ("Marker", "Jump")]


def test_a_marker_goes_in_the_top_staff_ahead_of_the_voice():
    root = _score()
    done = apply_fixes(root, [{"kind": "marker", "measure": 2, "type": "tocoda"}])
    assert _signs(root) == [("1", 2, "Marker", "To Coda")]
    bar = root.find("Score/Staff").findall("Measure")[1]
    assert [child.tag for child in bar] == ["Marker", "voice"]
    assert bar.find("Marker/label").text == "coda"
    assert "marker 'To Coda'" in done[0]


def test_a_jump_carries_where_it_goes_and_plays_until():
    root = _score()
    apply_fixes(root, [{"kind": "jump", "measure": 3, "type": "dcalcoda", "play_repeats": True}])
    jump = root.find("Score/Staff").findall("Measure")[2].find("Jump")
    assert [(c.tag, c.text) for c in jump] == [
        ("style", "Repeat Text Right"), ("text", "D.C. al Coda"), ("jumpTo", "start"),
        ("playUntil", "coda"), ("continueAt", "codab"), ("playRepeats", "1")]


def test_repeats_are_taken_once_unless_asked():
    root = _score()
    apply_fixes(root, [{"kind": "jump", "measure": 3, "type": "dc"}])
    assert root.find(".//Jump/playRepeats") is None


@pytest.mark.parametrize("kind", ["marker", "jump"])
def test_an_unknown_type_refuses(kind):
    with pytest.raises(FixError, match=rf"m2 \({kind}\): 'type' must be one of"):
        apply_fixes(_score(), [{"kind": kind, "measure": 2, "type": "dalsegno"}])


def test_a_bar_that_already_has_one_refuses():
    root = _score()
    fix = {"kind": "marker", "measure": 2, "type": "coda"}
    apply_fixes(root, [fix])
    with pytest.raises(FixError, match="staff 1 already has Marker 'Coda'"):
        apply_fixes(root, [fix])


def test_a_bar_past_the_end_refuses():
    with pytest.raises(FixError, match="no measure 9"):
        apply_fixes(_score(), [{"kind": "jump", "measure": 9, "type": "dc"}])


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


def _order(root, tmp_path):
    from src.score_export.clock import read_mpos
    from src.score_export.musescore import run_musescore
    path = tmp_path / "s.mscx"
    etree.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True)
    return read_mpos(run_musescore(str(path), str(tmp_path / "s.mpos")))[1]


def _plain_five_bars(tmp_path):
    """dal_segno.mscx with its own signs taken off: five bars, played straight."""
    path = tmp_path / "plain.mscx"
    shutil.copy(os.path.join(SCORE_FILES, "dal_segno.mscx"), path)
    root = etree.parse(str(path)).getroot()
    for sign in root.xpath(".//Marker | .//Jump"):
        sign.getparent().remove(sign)
    return root


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_plays_the_written_da_capo_al_coda(tmp_path):
    root = _plain_five_bars(tmp_path)
    assert _order(root, tmp_path) == [0, 1, 2, 3, 4]
    apply_fixes(root, [{"kind": "marker", "measure": 3, "type": "tocoda"},
                       {"kind": "jump", "measure": 4, "type": "dcalcoda"},
                       {"kind": "marker", "measure": 5, "type": "coda"}])
    assert _order(root, tmp_path) == [0, 1, 2, 3, 0, 1, 2, 4]


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_play_repeats_sings_the_inner_repeat_again(tmp_path):
    """Karjalan laulu's shape, small: |: 1 2 :| 3 4 D.C. al Coda, the repeat sung again."""
    root = _plain_five_bars(tmp_path)
    apply_fixes(root, [{"kind": "endrepeat", "measure": 2},
                       {"kind": "marker", "measure": 3, "type": "tocoda"},
                       {"kind": "jump", "measure": 4, "type": "dcalcoda", "play_repeats": True},
                       {"kind": "marker", "measure": 5, "type": "coda"}])
    assert _order(root, tmp_path) == [0, 1, 0, 1, 2, 3, 0, 1, 0, 1, 2, 4]
