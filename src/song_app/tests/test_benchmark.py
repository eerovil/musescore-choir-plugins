"""The committed slice of the OMR benchmark, and what can be checked about it.

Everything #80 concluded rested on a folder on one host. Three of its seven
pages are public domain and now travel with the repository; this is what the
suite does with them, in three tiers so that each dependency buys something and
none of them is required.

**No dependencies.** The ground truth is checked against the transcription it
was derived from, bar for bar, and the page boundary is read off that
transcription's own page breaks. This is the tier that runs in CI, and it is
the one that matters most: a truth table nobody re-derives is a claim, not
evidence.

**poppler.** Each page crops into the printed systems its bounds name.

**homr.** A real scan is read and comes back as the systems the page prints.
CI has no homr and should not grow one, so this tier skips there — the same
way the MuseScore-CLI and Playwright tests skip.
"""
import os
import shutil

import pytest
from lxml import etree

from src.song_app import omr, omr_systems, pdf_systems
from src.song_app.tests import benchmark

PAGE_IDS = [page.id for page in benchmark.pages()]


@pytest.fixture(autouse=True)
def _no_real_deck(monkeypatch):
    """Reading a page asks AgentDeck for a heavy slot. The suite must not take
    one off the host it runs on."""
    monkeypatch.delenv("AGENTDECK_API_URL", raising=False)
    monkeypatch.delenv("AGENTDECK_URL", raising=False)


# --- the set itself ------------------------------------------------------


@pytest.mark.parametrize("page_id", PAGE_IDS)
def test_every_page_in_the_manifest_is_on_disk(page_id):
    page = benchmark.page(page_id)

    assert os.path.exists(page.pdf), page.pdf
    assert page.page <= pdf_systems.page_count(page.pdf)
    for path in (page.truth_path, page.transcription_path):
        assert path is None or os.path.exists(path), path


@pytest.mark.parametrize("page_id", PAGE_IDS)
def test_the_bounds_are_bands_of_one_page_in_order(page_id):
    page = benchmark.page(page_id)
    bounds = page.systems

    assert [b.index for b in bounds] == list(range(1, len(bounds) + 1))
    assert all(b.page == page.page for b in bounds)
    for b in bounds:
        assert 0.0 <= b.top < b.bottom <= 1.0
        assert b.measure_start <= b.measure_end
    for earlier, later in zip(bounds, bounds[1:]):
        assert earlier.bottom <= later.top, "systems overlap"
        assert later.measure_start == earlier.measure_end + 1, "bars skip a system"


def test_b2_is_the_song_fixtures_own_page_and_not_a_second_copy():
    """B2's source PDF is already committed under the Virta venhettä vie
    fixture. Committing the benchmark's extract of it would be 188 KB of the
    same page."""
    b2 = benchmark.page("B2")

    assert b2.pdf.endswith(os.path.join(
        "fixtures", "virta-venhetta-vie", "00-registered", "Virta venhettä vie.pdf"))
    assert not [name for name in os.listdir(benchmark.BENCHMARK_DIR)
                if name.startswith("B2")]


def test_the_committed_slice_stays_small():
    """The card's own constraint: commit the PDFs and rasterise in the test.
    A 300 dpi PNG of one of these pages is 1.0-1.7 MB on its own."""
    files = os.listdir(benchmark.BENCHMARK_DIR)
    total = sum(os.path.getsize(os.path.join(benchmark.BENCHMARK_DIR, name))
                for name in files)

    assert not [name for name in files if name.lower().endswith((".png", ".jpg", ".tif"))]
    assert total < 1_000_000, f"{total} bytes in fixtures/omr-benchmark"


# --- the ground truth ----------------------------------------------------


def counts_from_transcription(mscx_path, first, last):
    """Per bar, per staff, per voice, the way the truth table records it."""
    root = etree.parse(mscx_path).getroot()
    staves = [s for s in root.findall(".//Staff[@id]") if s.findall("Measure")]
    rows = {}
    for staff_no, staff in enumerate(staves, start=1):
        for measure_no, measure in enumerate(staff.findall("Measure"), start=1):
            if not first <= measure_no <= last:
                continue
            for voice_no, voice in enumerate(measure.findall("voice"), start=1):
                chords = voice.findall("Chord")
                rows[(measure_no, staff_no, voice_no)] = (
                    len(chords),
                    sum(len(c.findall("Note")) for c in chords),
                    sum(1 for c in chords if len(c.findall("Note")) > 1),
                    len(voice.findall("Rest")),
                )
    return rows


def breaks_in(mscx_path):
    """``{measure number: 'line'|'page'}`` off the top staff."""
    root = etree.parse(mscx_path).getroot()
    staff = next(s for s in root.findall(".//Staff[@id]") if s.findall("Measure"))
    return {n: b.findtext("subtype")
            for n, measure in enumerate(staff.findall("Measure"), start=1)
            for b in measure.iter("LayoutBreak")}


def test_the_truth_table_is_what_the_transcription_says():
    """The truth table is derived, so it can be re-derived. If this fails,
    either the transcription changed or the table was edited by hand."""
    page = benchmark.page("B1a")
    rows = page.truth()
    assert rows, "B1a has no truth table"

    counts = counts_from_transcription(
        page.transcription_path,
        min(r.measure for r in rows), max(r.measure for r in rows))

    for row in rows:
        voice = int(row.part.split("voice ")[1].rstrip(")"))
        assert counts[(row.measure, row.staff, voice)] == (
            row.chords, row.notes, row.chords_with_2plus_noteheads, row.rests
        ), f"bar {row.measure}, staff {row.staff}, {row.part}"
    assert len(rows) == len(counts), "the transcription has voices the table does not"
    assert sum(r.notes for r in rows) == 120


def test_the_basses_are_in_unison_in_bar_15():
    """The page prints one line for the two basses there, so the truth is one
    voice — the exception that a scoring rule has to allow for."""
    page = benchmark.page("B1a")
    bass_15 = [r for r in page.truth() if r.measure == 15 and r.staff == 2]

    assert [r.notes for r in bass_15] == [6]
    assert all(len([r for r in page.truth() if r.measure == m and r.staff == 2]) == 2
               for m in (11, 12, 13, 14, 16, 17))


@pytest.mark.parametrize("page_id", ["B1a", "B1b"])
def test_the_page_boundary_comes_from_the_transcriptions_own_breaks(page_id):
    """Why this page is bars 11-17 and why its systems are 11-13 / 14-15 /
    16-17: the transcription was entered with the print's own layout, so the
    breaks say it rather than someone counting bars off a scan."""
    page = benchmark.page(page_id)
    marks = breaks_in(page.transcription_path)
    pages = sorted(n for n, kind in marks.items() if kind == "page")

    start, end = pages[0] + 1, pages[1]
    assert (start, end) == (11, 17)
    system_ends = [n for n in sorted(marks) if start <= n <= end]
    assert [b.measure_end for b in page.systems] == system_ends
    assert page.systems[0].measure_start == start


# --- cropping ------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="poppler (pdftoppm) is not installed")
@pytest.mark.parametrize("page_id", PAGE_IDS)
def test_a_page_crops_into_the_systems_it_declares(page_id, tmp_path):
    from PIL import Image

    page = benchmark.page(page_id)

    images = pdf_systems.crop_systems(page.pdf, page.systems, str(tmp_path), dpi=150)

    assert [i.index for i in images] == [b.index for b in page.systems]
    for image in images:
        band = Image.open(image.path).convert("L")
        ink = sum(1 for pixel in band.getdata() if pixel < 128) / (band.width * band.height)
        assert band.width > band.height, "a printed system is wider than it is tall"
        assert 0.005 < ink < 0.4, f"system {image.index} holds {ink:.3f} ink"


# --- the real thing ------------------------------------------------------


needs_homr = pytest.mark.skipif(
    not omr.homr_available(), reason="homr not installed (scripts/install-homr.sh)")
needs_poppler = pytest.mark.skipif(
    not shutil.which("pdftoppm"), reason="poppler (pdftoppm) is not installed")


@needs_homr
@needs_poppler
@pytest.mark.omr
@pytest.mark.parametrize("page_id", PAGE_IDS)
def test_a_real_scan_reads_as_the_systems_the_page_prints(page_id, tmp_path):
    """#80's bar, on real music rather than a synthetic document: right staves,
    right bar count, one system at a time. B1b is the one worth watching — the
    same page as B1a at 150 dpi with dropout, and it has to come back the same.
    """
    page = benchmark.page(page_id)

    scans = omr_systems.read_systems(
        page.pdf, page.systems, str(tmp_path), queue=False)

    assert [s.width for s in scans] == [page.staves] * len(page.systems)
    assert [s.bars for s in scans] == [
        b.measure_end - b.measure_start + 1 for b in page.systems]


@needs_homr
@needs_poppler
@pytest.mark.omr
def test_a_real_scan_assembles_into_one_score(tmp_path):
    """The systems are separate documents until they are not: one part per
    staff column, bar numbers running on, and a break at each join."""
    page = benchmark.page("B1a")

    scans = omr_systems.read_systems(page.pdf, page.systems, str(tmp_path), queue=False)
    assembled = omr_systems.assemble(scans, str(tmp_path / "assembled.musicxml"))

    root = etree.parse(assembled).getroot()
    parts = root.findall("part")
    assert len(parts) == page.staves
    for part in parts:
        measures = part.findall("measure")
        assert len(measures) == page.bars == 7
        assert [m.get("number") for m in measures] == [str(n) for n in range(1, 8)]
    breaks = [m.get("number") for m in parts[0].findall("measure")
              if m.findall('print[@new-system="yes"]')]
    assert breaks == ["4", "6"], "a break at each join and none at the start"
