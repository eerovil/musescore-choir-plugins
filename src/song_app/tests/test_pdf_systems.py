"""Rendering, cropping and storing printed-system bounds.

Where the boundaries come from is not tested here, because it is not decided
here: an AI reads them off the page and a person corrects them. What must hold
is that a stored boundary crops the region it claims to, survives a change of
resolution, and is never labelled with a measure range that does not fit.
"""
import json
import os
import shutil

import pytest

pytest.importorskip("PIL")
if not shutil.which("pdftoppm"):
    pytest.skip("pdftoppm (poppler) is not installed", allow_module_level=True)

from lxml import etree
from PIL import Image

from src.clean_score.utils import per_system
from src.song_app import pdf_systems

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie",
)
PDF = os.path.join(FIXTURE, "00-registered", "Virta venhettä vie.pdf")
MSCX = os.path.join(FIXTURE, "10-cleaned", "Virta-venhetta-vie.mscx")
BOUNDS = os.path.join(FIXTURE, "10-cleaned", pdf_systems.BOUNDS_FILE)

DPI = 100          # enough to check geometry; the crops are not read here


@pytest.fixture(scope="module")
def bounds():
    return pdf_systems.load_bounds(os.path.join(FIXTURE, "10-cleaned"))


def test_the_fixture_bounds_cover_every_printed_system(bounds):
    """15 systems over four pages, laid out 4/4/4/3."""
    assert len(bounds) == 15
    from collections import Counter
    assert dict(Counter(b.page for b in bounds)) == {1: 4, 2: 4, 3: 4, 4: 3}


def test_bounds_are_ordered_and_do_not_overlap(bounds):
    assert [b.index for b in bounds] == list(range(1, 16))
    for a, b in zip(bounds, bounds[1:]):
        assert a.top < a.bottom
        if a.page == b.page:
            assert a.bottom <= b.top, f"system {a.index} overlaps {b.index}"


def test_measure_ranges_match_the_score(bounds):
    root = etree.parse(MSCX).getroot()
    ranges = per_system.system_ranges(root)
    assert [(b.measure_start, b.measure_end) for b in bounds] == \
           [(r.start, r.end) for r in ranges]


def test_refuses_to_label_when_counts_disagree(bounds, tmp_path):
    """A wrong measure alignment is worse than none, so it declines to guess."""
    root = etree.parse(MSCX).getroot()
    for staff in root.iter("Staff"):
        for measure in list(staff.findall("Measure"))[8:]:
            staff.remove(measure)
    short = tmp_path / "short.mscx"
    etree.ElementTree(root).write(str(short), encoding="UTF-8", xml_declaration=True)
    assert len(per_system.system_ranges(root)) != len(bounds)          # premise

    blank = [pdf_systems.SystemBounds(b.index, b.page, b.top, b.bottom) for b in bounds]
    assert pdf_systems.label(blank, str(short)) == blank               # unlabelled
    assert pdf_systems.label(blank, MSCX) != blank                     # would label


def test_a_crop_is_the_band_it_claims(bounds, tmp_path):
    """The PNG covers exactly the fraction of the page the bounds name."""
    one = [bounds[7]]                                                  # m27-30
    images = pdf_systems.crop_systems(PDF, one, out_dir=str(tmp_path), dpi=DPI)
    page = Image.open(pdf_systems.render_page(PDF, one[0].page, DPI, str(tmp_path)))
    crop = Image.open(images[0].path)
    assert crop.width == page.width
    expected = int(page.height * one[0].bottom) - int(page.height * one[0].top)
    assert crop.height == expected


def test_bounds_survive_a_change_of_resolution(bounds, tmp_path):
    """Fractions, not pixels: the same band at two dpi differs only in scale."""
    one = [bounds[0]]
    low = pdf_systems.crop_systems(PDF, one, out_dir=str(tmp_path / "lo"), dpi=100)
    high = pdf_systems.crop_systems(PDF, one, out_dir=str(tmp_path / "hi"), dpi=200)
    lo, hi = Image.open(low[0].path), Image.open(high[0].path)
    assert abs(hi.height / lo.height - 2.0) < 0.02
    assert abs(hi.width / lo.width - 2.0) < 0.02


def test_bounds_round_trip_through_the_song_folder(bounds, tmp_path):
    pdf_systems.save_bounds(str(tmp_path), bounds)
    assert pdf_systems.load_bounds(str(tmp_path)) == bounds


def test_saving_edited_bounds_invalidates_same_dpi_crops(bounds, tmp_path):
    from src.song_app import pipeline

    song_dir = str(tmp_path)
    pdf_systems.save_bounds(song_dir, bounds)
    first = pipeline.system_crop(song_dir, PDF, 1, DPI)
    old_height = Image.open(first).height

    bands = [b.to_dict() for b in bounds]
    bands[0]["bottom"] = bands[0]["bottom"] - 0.02
    pipeline.save_system_bounds(song_dir, bands)
    second = pipeline.system_crop(song_dir, PDF, 1, DPI)

    assert second != first
    assert Image.open(second).height < old_height


def test_a_missing_or_broken_bounds_file_reads_as_none(tmp_path):
    assert pdf_systems.load_bounds(str(tmp_path)) == []
    with open(tmp_path / pdf_systems.BOUNDS_FILE, "w") as f:
        f.write("{not json")
    assert pdf_systems.load_bounds(str(tmp_path)) == []


def test_the_grid_overlay_is_the_page_with_a_scale_on_it(tmp_path):
    """The scale is what lets boundaries be read off rather than guessed."""
    plain = pdf_systems.render_page(PDF, 1, DPI, str(tmp_path))
    gridded = pdf_systems.page_images(PDF, out_dir=str(tmp_path), dpi=DPI, grid=True)[0]
    a, b = Image.open(plain), Image.open(gridded)
    assert a.size == b.size
    assert gridded != plain
    reds = [px for px in b.convert("RGB").getdata() if px[0] > 200 and px[1] < 100]
    assert reds, "no scale was drawn"
