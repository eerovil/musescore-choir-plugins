"""Proposing the printed-system bands off a page, end to end through homr.

The grouping rule and its little-pages-of-staves tests live in the homr fork
(``homr/system_finder.py``, ``tests/test_system_finder.py``) since #144; the app's
adapter contract is pinned in ``test_system_finder_homr_api.py`` and
``test_system_finder_proposal_contract.py``.

What stays here is the acceptance (marked ``omr``), against the bands a person actually drew:
every page of the hand-corrected fixture and both Herää Suomi scans come back
with the systems the page prints, and each boundary within a fiftieth of the
page of where the hand put it. That is the acceptance — the numbers this was
built against — and it skips without homr or poppler, the same way the
MuseScore-CLI and Playwright tests skip.
"""

import json
import os
import shutil

import pytest

from src.song_app import omr, pdf_systems, system_finder
from src.song_app.tests import benchmark

# --- against the bands a person drew --------------------------------------

needs_homr = pytest.mark.skipif(
    not omr.homr_available(), reason="homr not installed (scripts/install-homr.sh)")
needs_poppler = pytest.mark.skipif(
    not shutil.which("pdftoppm"), reason="poppler (pdftoppm) is not installed")

#: How far a proposed boundary may sit from the one a person dragged. A fiftieth
#: of an A4 is ~6mm — a band that is out by that still holds its whole system.
TOLERANCE = 0.02

FIXTURE_BOUNDS = os.path.join(
    benchmark.REPO_ROOT, "fixtures", "virta-venhetta-vie", "10-cleaned", ".systems.json")


def hand_drawn():
    with open(FIXTURE_BOUNDS, encoding="utf-8") as f:
        return [pdf_systems.SystemBounds(**s) for s in json.load(f)["systems"]]


def check(found, wanted):
    """The proposal against the bands a person drew, page by page."""
    for page_no in sorted({b.page for b in wanted}):
        mine = [b for b in found if b.page == page_no]
        theirs = [b for b in wanted if b.page == page_no]
        assert len(mine) == len(theirs), (
            f"page {page_no}: proposed {len(mine)} systems, the page prints {len(theirs)}")
        for a, b in zip(mine[:-1], theirs[:-1]):
            assert abs(a.bottom - b.bottom) <= TOLERANCE, (
                f"page {page_no} system {b.index}: boundary at {a.bottom:.3f}, "
                f"hand-drawn at {b.bottom:.3f}")
        # The outer edges have no neighbour to halve, so what matters is that
        # they hold the whole system rather than sit on a particular number.
        assert mine[0].top <= theirs[0].top
        assert mine[-1].bottom >= theirs[-1].bottom


@needs_homr
@needs_poppler
@pytest.mark.omr
def test_the_fixture_comes_back_as_the_systems_the_page_prints(tmp_path):
    """Four pages of a real 19th-century scan against 15 hand-drawn bands.

    ~35s: a page is a segmentation pass, not a parse.
    """
    pdf = os.path.join(benchmark.REPO_ROOT, "fixtures", "virta-venhetta-vie",
                       "00-registered", "Virta venhettä vie.pdf")
    found = system_finder.find_bands(pdf, out_dir=str(tmp_path), queue=False)
    wanted = hand_drawn()
    assert len(found) == len(wanted)
    assert [b.index for b in found] == list(range(1, len(wanted) + 1))
    check(found, wanted)


@needs_homr
@needs_poppler
@pytest.mark.omr
@pytest.mark.parametrize("page_id", ["B1a", "B1b"])
def test_both_scans_of_the_same_page_come_back_the_same(page_id, tmp_path):
    """The good scan and the poor one propose the same three systems.

    B1b is the one that made the gap veto necessary: at 150 dpi with dropout its
    last system loses its barlines outright.
    """
    entry = benchmark.page(page_id)
    found = system_finder.find_bands(entry.pdf, out_dir=str(tmp_path), queue=False)
    assert len(found) == len(entry.systems)
    check(found, entry.systems)
