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


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


def test_bands_refuse_when_the_staves_do_not_divide_into_systems(tmp_path, monkeypatch):
    """The count is the check. Staves that do not divide evenly by the number a
    system has means the render did not come out as expected, and pairing it with
    the scan would line the wrong things up."""
    from src.song_app import pdf_systems

    class FakePage:
        height = 1000
        def convert(self, _mode):
            return self

    monkeypatch.setattr(pdf_systems, "page_count", lambda p: 1)
    monkeypatch.setattr(pdf_systems, "render_page", lambda *a, **k: "x.png")
    monkeypatch.setattr(pdf_systems.Image, "open", lambda p: FakePage())
    monkeypatch.setattr(pdf_systems, "_staff_rows", lambda page: [[1], [2], [3]])

    assert pdf_systems.rendered_system_bands("x.pdf", 4, str(tmp_path)) == []
    assert pdf_systems.rendered_system_bands("x.pdf", 0, str(tmp_path)) == []


@pytest.mark.skipif(not os.path.exists(CLEANED), reason="prototyping fixture not present")
@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_the_render_keeps_the_printed_systems_and_pairs_with_the_scan(tmp_path):
    """End to end: the render is checked to have the same systems as the page, and
    each one pairs with the scan's crop of the same measures."""
    import shutil

    from src.song_app import pdf_systems

    song = tmp_path / "song"
    song.mkdir()
    # Through 20-lyrics on purpose: lyrics widen the spacing, and a system that
    # fits the page without them can be pushed over the edge with them. Tested on
    # the score without lyrics, a render that ignores the system count passes.
    for stage in ("00-registered", "10-cleaned", "20-lyrics"):
        for name in os.listdir(os.path.join(FIXTURE, stage)):
            shutil.copyfile(os.path.join(FIXTURE, stage, name), song / name)
    cleaned = str(song / "Virta-venhetta-vie_cleaned.mscx")
    assert etree.parse(cleaned).getroot().findall(".//Lyrics"), "premise: it has lyrics"

    breaks = pipeline.line_break_measures(str(song / "Virta-venhetta-vie.mscx"))
    assert len(breaks) == 14

    rendered = pipeline.render_score_pdf(cleaned, breaks)
    staves = pipeline.score_staff_count(cleaned)
    assert staves == 4
    bands = pdf_systems.rendered_system_bands(rendered, staves, str(song / ".pages"))
    assert len(bands) == len(breaks) + 1, "the render lost the printed systems"

    pairs = pipeline.compare_systems(str(song), cleaned, breaks)
    assert len(pairs) == 15
    assert pairs[0]["measure_start"] == 1 and pairs[0]["measure_end"] == 3

    crop = pipeline.cleaned_system_crop(str(song), cleaned, breaks, 1, 150)
    assert os.path.getsize(crop) > 0
