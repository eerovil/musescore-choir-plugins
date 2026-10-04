"""The quick system finder: reading the printed systems off a page without homr.

Two halves. Little pages drawn here pin the rules — staves found, staves joined
into a system by a line across the gap, a tilted page, and the fallback when
nothing joins. Then the acceptance against the bands a person drew on real scans:
the fixture's four pages and both Herää Suomi copies come back with the systems
each page prints. That half needs poppler and skips without it.
"""

import json
import os
import shutil

import pytest
from PIL import Image, ImageDraw

from src.song_app import pdf_systems, system_finder
from src.song_app.tests import benchmark

WIDTH, HEIGHT, SPACE = 1240, 1754, 11


def draw_page(systems, *, joined=True, tilt=0.0, gap=150):
    """A page of five-line staves; `systems` is how many staves each system has."""
    page = Image.new("L", (WIDTH, HEIGHT), 255)
    pen = ImageDraw.Draw(page)
    y = 150
    for count in systems:
        first = y
        for _ in range(count):
            for line in range(5):
                row = y + line * SPACE
                pen.line([(100, row), (WIDTH - 100, row + tilt * (WIDTH - 200))], fill=0, width=2)
            y += 4 * SPACE + 90                       # staff, then room for lyrics
        last = y - 90
        if joined and count > 1:
            pen.line([(100, first), (100, last)], fill=0, width=3)   # systemic barline
        pen.text((300, last + 30), "lyrics under the staff", fill=0)
        y += gap
    return page


def test_staves_joined_by_a_barline_are_one_system():
    bands = system_finder.page_systems(draw_page([2, 2, 2]))
    assert len(bands) == 3
    assert all(a[1] == b[0] for a, b in zip(bands, bands[1:])), "edges meet halfway"
    assert 0 < bands[0][0] < bands[-1][1] < 1


def test_systems_of_different_sizes_are_read_as_printed():
    assert len(system_finder.page_systems(draw_page([2, 3, 2]))) == 3


def test_a_tilted_page_still_finds_its_staves():
    assert len(system_finder.page_systems(draw_page([2, 2, 2], tilt=0.009))) == 3


def test_without_any_joining_line_the_gaps_decide():
    """An edition with no systemic barline: wide gaps between systems still split."""
    assert len(system_finder.page_systems(draw_page([2, 2, 2], joined=False, gap=260))) == 3


def test_one_staff_systems_stay_one_staff():
    assert len(system_finder.page_systems(draw_page([1, 1, 1, 1]))) == 4


def test_a_page_with_no_music_has_no_systems():
    page = Image.new("L", (WIDTH, HEIGHT), 255)
    ImageDraw.Draw(page).text((400, 300), "Title page", fill=0)
    assert system_finder.page_systems(page) == []


def test_the_same_page_always_gives_the_same_bands():
    page = draw_page([2, 3, 2])
    assert system_finder.page_systems(page) == system_finder.page_systems(page)


# --- against the bands a person drew --------------------------------------

needs_poppler = pytest.mark.skipif(
    not shutil.which("pdftoppm"), reason="poppler (pdftoppm) is not installed")

#: How far a proposed internal boundary may sit from the hand-drawn one. Looser
#: than homr's 0.02: the quick finder puts a boundary halfway between systems,
#: and people tend to draw it a little nearer the system below. A person drags
#: the proposal into place anyway; what must be right is the count.
TOLERANCE = 0.035


def check(found, wanted):
    for page_no in sorted({b.page for b in wanted}):
        mine = [b for b in found if b.page == page_no]
        theirs = [b for b in wanted if b.page == page_no]
        assert len(mine) == len(theirs), (
            f"page {page_no}: proposed {len(mine)} systems, the page prints {len(theirs)}")
        for a, b in zip(mine[:-1], theirs[:-1]):
            assert abs(a.bottom - b.bottom) <= TOLERANCE, (
                f"page {page_no} system {b.index}: boundary at {a.bottom:.3f}, "
                f"hand-drawn at {b.bottom:.3f}")


@needs_poppler
def test_the_fixture_comes_back_as_the_systems_the_page_prints(tmp_path):
    pdf = os.path.join(benchmark.REPO_ROOT, "fixtures", "virta-venhetta-vie",
                       "00-registered", "Virta venhettä vie.pdf")
    bounds = os.path.join(benchmark.REPO_ROOT, "fixtures", "virta-venhetta-vie",
                          "10-cleaned", ".systems.json")
    with open(bounds, encoding="utf-8") as f:
        wanted = [pdf_systems.SystemBounds(**s) for s in json.load(f)["systems"]]
    found = system_finder.quick_bands(pdf, str(tmp_path))
    assert [b.index for b in found] == list(range(1, len(wanted) + 1))
    check(found, wanted)


@needs_poppler
@pytest.mark.parametrize("page_id", ["B1a", "B1b"])
def test_both_scans_of_heraa_suomi_come_back_as_printed(tmp_path, page_id):
    """B1b is the 150 dpi copy with dropout: faint, broken staff lines."""
    entry = benchmark.page(page_id)
    found = system_finder.quick_bands(entry.pdf, str(tmp_path))
    check(found, entry.systems)
