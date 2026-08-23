"""The scan is cut into printed systems, and they line up with the score."""
import os
import shutil
from collections import Counter

import pytest

pytest.importorskip("numpy")
pytest.importorskip("PIL")
if not shutil.which("pdftoppm"):
    pytest.skip("pdftoppm (poppler) is not installed", allow_module_level=True)

from lxml import etree

from src.clean_score.utils import per_system
from src.song_app import pdf_systems

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "fixtures", "virta-venhetta-vie",
)
PDF = os.path.join(FIXTURE, "00-registered", "Virta venhettä vie.pdf")
MSCX = os.path.join(FIXTURE, "10-cleaned", "Virta-venhetta-vie.mscx")

# 150 dpi is plenty to find the staves; the crops are only rendered large when a
# person or an agent actually asks to read one.
DPI = 150


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    out = tmp_path_factory.mktemp("systems")
    return pdf_systems.system_images(PDF, mscx_path=MSCX, out_dir=str(out), dpi=DPI)


def test_finds_every_printed_system(images):
    """15 systems, laid out 4/4/4/3 across the four pages."""
    assert len(images) == 15
    assert dict(Counter(i.page for i in images)) == {1: 4, 2: 4, 3: 4, 4: 3}


def test_measure_ranges_match_the_score(images):
    """Each crop is labelled with the measures the score says that system holds."""
    root = etree.parse(MSCX).getroot()
    ranges = per_system.system_ranges(root)
    assert [(i.measure_start, i.measure_end) for i in images] == \
           [(r.start, r.end) for r in ranges]


def test_crops_are_written_and_ordered(images):
    """Every band is a real image, and they run down the page in order."""
    for img in images:
        assert os.path.getsize(img.path) > 0
    for page in {i.page for i in images}:
        on_page = [i for i in images if i.page == page]
        assert on_page == sorted(on_page, key=lambda i: i.index)


def test_refuses_to_label_when_counts_disagree(images):
    """A wrong measure alignment is worse than none, so it declines to guess.

    Exercised against the labelling step directly rather than through
    system_images, which would mean rasterising all four pages a second time.
    """
    root = etree.parse(MSCX).getroot()
    for staff in root.iter("Staff"):
        for measure in list(staff.findall("Measure"))[8:]:
            staff.remove(measure)
    short = os.path.join(os.path.dirname(images[0].path), "short.mscx")
    etree.ElementTree(root).write(short, encoding="UTF-8", xml_declaration=True)

    assert len(per_system.system_ranges(root)) != len(images)      # premise

    fresh = [pdf_systems.SystemImage(i.index, i.page, i.path) for i in images]
    assert pdf_systems._label(fresh, short) == fresh                # unlabelled
    assert pdf_systems._label(fresh, MSCX) != fresh                 # and would label


def _synthetic(height=2000, width=1200, rule=True, thickness=3):
    """Two bracketed staves, optionally with a full-width rule above them.

    The reference scan has no such rule, so the guard against one is not
    exercised by the fixture tests; this covers it directly.
    """
    import numpy as np
    from PIL import Image

    page = np.full((height, width), 255, dtype=np.uint8)
    tops = (400, 900)
    for top in tops:
        for i in range(5):                       # five staff lines, 12px apart
            y = top + i * 12
            page[y:y + thickness, 60:width - 40] = 0
    page[400:912, 55:60] = 0                     # bracket joining the two staves
    if rule:
        page[100:102, 0:width] = 0               # a thin full-width rule
    return Image.fromarray(page)


def test_a_full_width_rule_is_not_mistaken_for_a_staff():
    assert len(pdf_systems._system_bands(_synthetic(rule=True))) == 1
    assert len(pdf_systems._system_bands(_synthetic(rule=False))) == 1


@pytest.mark.parametrize("thickness", [1, 3, 9])
def test_finds_staves_however_thick_the_lines_are(thickness):
    """A vector engraving's hairlines are 1px; a scan's are ~9.

    Counting ink rows instead of staff lines silently discarded every vector
    PDF -- five 1px lines look like five rows of noise.
    """
    assert len(pdf_systems._system_bands(_synthetic(rule=False, thickness=thickness))) == 1
