"""Putting the printed line breaks back for the preview render.

Normal-mode cleaning strips layout breaks, so the cleaned score reflows into
MuseScore's own systems and cannot be read against the page it came from. The
breaks are taken from the converted input, which usually has them — the "usually"
is the point of half these tests.
"""
import os
from fractions import Fraction

import pytest
from lxml import etree

from src.song_app import pipeline

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie")
INPUT = os.path.join(FIXTURE, "10-cleaned", "Virta-venhetta-vie.mscx")
CLEANED = os.path.join(FIXTURE, "20-lyrics", "Virta-venhetta-vie_cleaned.mscx")


def _score(n_measures, break_at=(), staves=1):
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for sid in range(1, staves + 1):
        staff = etree.SubElement(score, "Staff", id=str(sid))
        for i in range(n_measures):
            m = etree.SubElement(staff, "Measure")
            if sid == 1 and i in break_at:
                etree.SubElement(etree.SubElement(m, "LayoutBreak"), "subtype").text = "line"
    return root


def _breaks_on_top(root):
    staff = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None][0]
    return [i for i, m in enumerate(staff.findall("Measure"))
            if m.find("LayoutBreak") is not None]


@pytest.mark.skipif(not os.path.exists(INPUT), reason="prototyping fixture not present")
def test_the_input_score_carries_the_printed_breaks():
    """15 printed systems means 14 breaks, and the cleaned score has lost them."""
    assert len(pipeline.line_break_measures(INPUT)) == 14
    assert pipeline.line_break_measures(CLEANED) == []


def test_a_score_without_breaks_yields_none():
    """The case that cannot be relied on: some sources simply have no breaks."""
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".mscx")
    os.close(fd)
    etree.ElementTree(_score(8)).write(path, encoding="UTF-8", xml_declaration=True)
    try:
        assert pipeline.line_break_measures(path) == []
    finally:
        os.remove(path)


def test_a_missing_or_unreadable_file_yields_none(tmp_path):
    assert pipeline.line_break_measures(str(tmp_path / "nope.mscx")) == []
    bad = tmp_path / "bad.mscx"
    bad.write_text("<museScore><Score>")
    assert pipeline.line_break_measures(str(bad)) == []


def test_breaks_land_on_the_top_staff_only():
    root = _score(10, staves=4)
    assert pipeline._apply_line_breaks(root, [2, 5]) == 2
    assert _breaks_on_top(root) == [2, 5]
    others = [s for s in root.findall(".//Score/Staff")][1:]
    assert all(s.find(".//LayoutBreak") is None for s in others)


def test_breaks_are_not_applied_to_a_score_of_a_different_length():
    """Applied by index, so a shorter score would get its systems in the wrong
    places — worse than leaving the render alone."""
    root = _score(4)
    assert pipeline._apply_line_breaks(root, [2, 6, 9]) == 0
    assert _breaks_on_top(root) == []


def test_existing_breaks_are_not_doubled():
    root = _score(10, break_at=(2,))
    assert pipeline._apply_line_breaks(root, [2, 5]) == 1     # only the new one
    staff = root.find(".//Staff")
    assert len(staff.findall("Measure")[2].findall("LayoutBreak")) == 1


@pytest.mark.skipif(not os.path.exists(CLEANED), reason="prototyping fixture not present")
def test_the_two_renders_do_not_share_a_cache_file(tmp_path):
    """With and without breaks are different pictures of the same score."""
    import shutil
    copy = tmp_path / "s.mscx"
    shutil.copyfile(CLEANED, copy)
    plain = os.path.splitext(str(copy))[0] + ".render.pdf"
    withb = os.path.splitext(str(copy))[0] + ".breaks.render.pdf"
    assert plain != withb
