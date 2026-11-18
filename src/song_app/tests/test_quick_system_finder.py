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


def test_a_grey_page_is_read_by_its_own_threshold():
    """#480: a scan on grey paper (here 215) read as all ink under the fixed 230,
    and no system was found on any page."""
    import numpy as np
    page = np.asarray(draw_page([2, 2, 2]))
    grey = Image.fromarray(np.where(page > 127, 215, 40).astype("uint8"))
    assert len(system_finder.page_systems(grey)) == 3
    assert system_finder.page_systems(draw_page([2, 2, 2])) == \
        system_finder.page_systems(grey)              # same bands as on white paper


def _scan(page, paper, lines, dark=40, grain=0.0, seed=4):
    """`page` as a scan: paper at `paper`, staff lines at `lines`, barlines and words
    at `dark`, with Gaussian grain of `grain` grey levels."""
    import numpy as np
    drawn = np.asarray(page)
    staff = np.asarray(draw_page([2, 2, 2], joined=False))       # the lines alone
    out = np.where(drawn > 127, paper, np.where(staff < 128, lines, dark)).astype(float)
    out += np.random.default_rng(seed).normal(0, grain, out.shape)
    return Image.fromarray(out.clip(0, 255).astype("uint8"))


@pytest.mark.parametrize("paper,lines,grain", [
    (236, 216, 0), (236, 216, 3), (240, 225, 3), (233, 213, 3),   # near-white, faint lines
    (225, 205, 0), (215, 200, 2), (215, 40, 0), (200, 150, 5),    # grey paper
])
def test_faint_lines_on_tinted_paper_are_found(paper, lines, grain):
    """The checks on #480's first try: paper a little off white with lines only 15-20
    levels darker lost every system, and so did grey paper with faint lines."""
    assert len(system_finder.page_systems(_scan(draw_page([2, 2, 2]), paper, lines, grain=grain))) == 3


@pytest.mark.parametrize("paper,grain", [(215, 0), (215, 4), (215, 8), (236, 3), (200, 6)])
def test_a_blank_grey_page_has_no_systems(paper, grain):
    page = _scan(Image.new("L", (WIDTH, HEIGHT), 255), paper, paper, grain=grain)
    assert system_finder.page_systems(page) == []


def test_a_grey_crop_still_gets_its_bar_box():
    crop = _scan(draw_page([2]), 215, 40).crop((0, 100, WIDTH, 420))
    assert system_finder.bar_box(crop, 1, 2) is not None


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
