"""Rendering, cropping and storing printed-system bounds.

Where the boundaries come from is not tested here, because it is not decided
here: an AI reads them off the page and a person corrects them. What must hold
is that a stored boundary crops the region it claims to, survives a change of
resolution, and is never labelled with a measure range that does not fit.
"""
import json
import os
import shutil
import subprocess

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


def test_a_crop_follows_the_score_it_was_cut_from(bounds, tmp_path):
    """The same band of a *different* PDF is a different crop.

    The compare view cuts its bands out of a render of the cleaned score, and that
    render changes whenever the score does. Keying the cache on the band alone
    served bar 8 as it looked before a slur was recorded, hours after the slur was
    in the file and in the render -- so the page said the fix had not applied.
    """
    one = [bounds[0]]
    first = pdf_systems.crop_systems(PDF, one, out_dir=str(tmp_path), dpi=100)[0]

    # A different score of the same shape: page 2 of the fixture, alone.
    other = str(tmp_path / "other.pdf")
    subprocess.run(["pdfseparate", "-f", "2", "-l", "2", PDF, other], check=True)
    second = pdf_systems.crop_systems(other, one, out_dir=str(tmp_path), dpi=100)[0]

    assert second.path != first.path
    assert Image.open(second.path).tobytes() != Image.open(first.path).tobytes()


def _staff_pdf(path, systems_per_page, pages, staves=2):
    """A PDF of plain staves, `systems_per_page` systems of `staves` on each page."""
    from PIL import ImageDraw
    w, h = 850, 1100
    images = []
    for _ in range(pages):
        img = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(img)
        step = h // (systems_per_page * staves + 1)
        for k in range(systems_per_page * staves):
            top = step * (k + 1) - 20
            for line in range(5):
                y = top + line * 10
                draw.line([(60, y), (w - 60, y)], fill="black", width=2)
        images.append(img)
    images[0].save(path, "PDF", resolution=100, save_all=True,
                   append_images=images[1:])


def test_a_page_rewritten_in_place_is_rasterised_again(tmp_path):
    """#255: a render rebuilt at the same path kept being read off its old pages.

    The cleaned render is rewritten whenever the score changes, and the page cache
    was named by path alone, so the Fix comparison counted the previous render's
    systems and refused to compare a render that was correct.
    """
    pdf = str(tmp_path / "score.breaks.render.pdf")
    cache = str(tmp_path / ".pages")
    _staff_pdf(pdf, systems_per_page=2, pages=2)
    assert len(pdf_systems.rendered_system_bands(pdf, 2, cache)) == 4

    _staff_pdf(pdf, systems_per_page=3, pages=2)
    os.utime(pdf, ns=(os.stat(pdf).st_atime_ns, os.stat(pdf).st_mtime_ns + 10**9))
    assert len(pdf_systems.rendered_system_bands(pdf, 2, cache)) == 6

    # The old render's pages are gone rather than left to pile up.
    pages = sorted(n for n in os.listdir(cache) if n.startswith("page-"))
    assert len(pages) == 2, pages


def _turned_pdf(path, rotate):
    """One A4 page stored landscape and flagged to turn by `rotate` degrees.

    The stripes run across the *stored* width, so once the page is turned they
    run down it, each at its own depth -- a crop from the wrong height shows
    different stripes rather than more white paper.
    """
    w, h = 841.68, 595.2
    stripes = "".join(f"{x} 0 {width} {h} re f\n"
                      for x, width in ((60, 8), (180, 30), (330, 4), (470, 60),
                                       (620, 16), (760, 40)))
    content = stripes.encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
         f"/Rotate {rotate} /Contents 4 0 R >>").encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    with open(path, "wb") as f:
        f.write(bytes(out))


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_a_crop_of_a_turned_page_is_the_band_the_preview_shows(rotate, tmp_path):
    """#272: a landscape-stored page flagged to turn was cropped by its stored size.

    `pdfinfo` reports the page before its rotation and `pdftoppm` renders it after,
    so every band was cut from a strip of the page above the one the preview
    showed -- and homr read half of one system and half of the next.
    """
    pdf = str(tmp_path / "turned.pdf")
    _turned_pdf(pdf, rotate)
    band = pdf_systems.SystemBounds(index=2, page=1, top=0.41, bottom=0.67)
    crop = Image.open(pdf_systems.crop_systems(pdf, [band], str(tmp_path / "c"), dpi=DPI)[0].path)
    page = Image.open(pdf_systems.render_page(pdf, 1, DPI, str(tmp_path / "p")))
    assert (page.width < page.height) == (rotate in (90, 270))
    expected = page.crop((0, int(page.height * band.top), page.width,
                          int(page.height * band.bottom)))
    assert crop.size == expected.size
    assert crop.convert("L").tobytes() == expected.convert("L").tobytes()


def test_a_turned_page_changes_what_its_crops_are_stamped_with(tmp_path):
    """Crops and scans cut before #272 must not be served again for a turned PDF,
    while an unturned one keeps the stamps its songs already carry."""
    flat, turned = str(tmp_path / "flat.pdf"), str(tmp_path / "turned.pdf")
    _turned_pdf(flat, 0)
    _turned_pdf(turned, 90)
    assert pdf_systems.crop_version(flat) == pdf_systems.file_version(flat)
    assert pdf_systems.crop_version(turned) != pdf_systems.file_version(turned)
