"""Bar numbers go on the first bar of each printed system, not on every bar.

The video is one continuous system, so it has no systems of its own to number
from; the grouping that means anything to a singer is the one the printed page
had. What is pinned here is that choice and — the part that could quietly break
a render — that acting on it only takes ink off the page and moves nothing.
"""
import numpy as np
import pytest
from lxml import etree

from src.scrollvideo import score as score_mod
from src.scrollvideo.build import FALLBACK_NUMBER_INTERVAL, numbered_measures
from src.scrollvideo.engrave import engrave, keep_measure_numbers
from src.scrollvideo.geometry import parse_layout, rasterise
from src.scrollvideo.spacing import measure_widths


def _score(tmp_path, measures: int, breaks=(), pages=()):
    """A minimal .mscx of `measures` bars, broken at `breaks` (line) / `pages` (page)."""
    root = etree.Element("museScore")
    staff = etree.SubElement(etree.SubElement(root, "Score"), "Staff")
    staff.set("id", "1")
    for i in range(measures):
        measure = etree.SubElement(staff, "Measure")
        for i_breaks, kind in ((breaks, "line"), (pages, "page")):
            if i in i_breaks:
                etree.SubElement(etree.SubElement(measure, "LayoutBreak"),
                                 "subtype").text = kind
    path = tmp_path / "score.mscx"
    path.write_bytes(etree.tostring(root))
    return str(path)


def _numbers(svg: str) -> list:
    """The bar numbers actually drawn, in order."""
    root = etree.fromstring(svg.encode())
    tag = "{http://www.w3.org/2000/svg}g"
    drawn = []
    for index, measure in enumerate(g for g in root.iter(tag)
                                    if "measure" in (g.get("class") or "").split()):
        if any("mNum" in (g.get("class") or "").split() for g in measure.iter(tag)):
            drawn.append(index)
    return drawn


# --- where the systems come from ------------------------------------------

def test_a_line_break_starts_the_next_system_not_its_own_bar(tmp_path):
    """A break sits on the last bar of a system, so bar 4 opens the second one."""
    assert score_mod.system_starts(_score(tmp_path, 12, breaks=(3, 7))) == [0, 4, 8]


def test_a_break_on_the_final_bar_starts_nothing(tmp_path):
    assert score_mod.system_starts(_score(tmp_path, 8, breaks=(3, 7))) == [0, 4]


def test_a_page_break_ends_a_system_too(tmp_path):
    """A page turn ends a system, so the bar after it opens the next one.

    A score laid out for print puts a page break at each turn and line breaks in
    between; counting only the line breaks merged every turn into one system and
    numbered a bar in the middle of the page instead of the one after the turn.
    """
    assert score_mod.system_starts(_score(tmp_path, 12, breaks=(3,), pages=(7,))) == [0, 4, 8]
    assert score_mod.system_starts(_score(tmp_path, 12, pages=(3, 7))) == [0, 4, 8]


def test_a_score_with_no_breaks_offers_no_grouping(tmp_path):
    assert score_mod.system_starts(_score(tmp_path, 8)) == []


def test_the_caller_s_grouping_wins_over_the_score_s(tmp_path):
    """Cleaning strips line breaks, so the app supplies the input score's."""
    path = _score(tmp_path, 12, breaks=(3, 7))
    assert numbered_measures(path, [0, 5, 9]) == [0, 5, 9]


def test_without_one_the_score_s_own_breaks_are_used(tmp_path):
    """A per-system score keeps its breaks, so it needs telling nothing."""
    assert numbered_measures(_score(tmp_path, 12, breaks=(3, 7))) == [0, 4, 8]


def test_a_score_that_was_never_laid_out_falls_back_to_an_interval(tmp_path):
    """No breaks anywhere: still not every bar, which is the thing complained of."""
    numbered = numbered_measures(_score(tmp_path, 10))
    assert numbered == list(range(0, 10, FALLBACK_NUMBER_INTERVAL))
    assert numbered != list(range(10))


# --- what reaches the page -------------------------------------------------

def test_verovio_numbers_every_bar_until_we_choose(fermata_musicxml):
    assert _numbers(engrave(fermata_musicxml).svg) == [0, 1]


def test_only_the_chosen_bars_keep_their_number(fermata_musicxml):
    assert _numbers(engrave(fermata_musicxml, numbered_measures=[0]).svg) == [0]
    assert _numbers(engrave(fermata_musicxml, numbered_measures=[1]).svg) == [1]


def test_asking_for_none_of_them_leaves_the_page_bare(fermata_musicxml):
    assert _numbers(engrave(fermata_musicxml, numbered_measures=[]).svg) == []


def test_not_choosing_leaves_the_page_exactly_as_verovio_drew_it(fermata_musicxml):
    """`None` is the old behaviour, byte for byte, so nothing else can shift."""
    drawn = engrave(fermata_musicxml).svg
    assert keep_measure_numbers(drawn, None) is drawn


def test_choosing_moves_no_bar(fermata_musicxml):
    """The width was settled when the page was laid out; this only takes ink away.

    It matters because `spacing.even_engraving` measures these widths and solves
    the scroll's evenness from them — a choice of bar numbers that nudged a bar
    would quietly change the video's pacing.
    """
    every = engrave(fermata_musicxml)
    chosen = engrave(fermata_musicxml, numbered_measures=[0])
    assert measure_widths(chosen.svg) == measure_widths(every.svg)
    assert parse_layout(chosen.svg).width == parse_layout(every.svg).width
    assert (sorted(parse_layout(chosen.svg).notes)
            == sorted(parse_layout(every.svg).notes))


def test_the_page_only_loses_ink(fermata_musicxml):
    """Every pixel is the same or lighter: nothing was drawn, only rubbed out."""
    every = engrave(fermata_musicxml)
    chosen = engrave(fermata_musicxml, numbered_measures=[0])
    layout = parse_layout(every.svg)
    before = rasterise(every.svg, layout, 200).astype(np.int16)
    after = rasterise(chosen.svg, parse_layout(chosen.svg), 200).astype(np.int16)

    assert after.shape == before.shape
    assert (after >= before - 1).all(), "the page gained ink where a number was removed"
    assert (after > before + 1).any(), "the second bar's number was not removed"
