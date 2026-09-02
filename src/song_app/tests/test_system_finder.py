"""Proposing the printed-system bands off a page.

Two tiers, so the dependency buys something and is not required.

**No dependencies.** The grouping rule itself, written as little pages of staves
and barlines, because that is the level the decision lives at: which staves make
one system, what happens when a staff's barlines are missing, and where the band
edges land. Nothing here runs homr — whether it can find a staff is homr's
business, and stubbing the pixels is what lets the rule be argued with at all.

**homr** (marked ``omr``). The rule against the bands a person actually drew:
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

# --- the rule -------------------------------------------------------------
#
# A staff is a box in fractions of the page; so is a barline. These build the
# two so a test can say "these two staves carry the same bars" in one line.


def staff(top, bottom, left=0.1, right=0.9):
    return {"top": top, "bottom": bottom, "left": left, "right": right}


def barline(staff_box, x):
    return {"top": staff_box["top"], "bottom": staff_box["bottom"],
            "left": x - 0.001, "right": x + 0.001}


def page(*rows):
    """``(staves, bar_lines)`` from ``(top, bottom, [barline x, ...])`` rows."""
    staves, bars = [], []
    for top, bottom, xs in rows:
        s = staff(top, bottom)
        staves.append(s)
        bars.extend(barline(s, x) for x in xs)
    return staves, bars


def test_staves_carrying_the_same_bars_are_one_system():
    staves, bars = page(
        (0.10, 0.14, [0.3, 0.5, 0.7]),
        (0.22, 0.26, [0.3, 0.5, 0.7]),
    )
    assert len(system_finder.group_staves(staves, bars)) == 1


def test_staves_carrying_different_bars_are_different_systems():
    # The gaps say nothing here — both are 0.08 — which is the case measured on
    # page 1 of the fixture, where within-system and between-system gaps overlap.
    staves, bars = page(
        (0.10, 0.14, [0.3, 0.5, 0.7]),
        (0.22, 0.26, [0.3, 0.5, 0.7]),
        (0.34, 0.38, [0.4, 0.6]),
        (0.46, 0.50, [0.4, 0.6]),
    )
    assert [len(s) for s in system_finder.group_staves(staves, bars)] == [2, 2]


def test_the_opening_and_closing_lines_are_not_evidence():
    """Every system is closed at both ends, wherever its bars fall.

    Left in, they make any two staves agree a little, and the agreement is
    weaker for it — so they are not counted at all.
    """
    ends, bars = page((0.10, 0.14, [0.1, 0.9]))
    assert system_finder._interior_barlines(ends[0], bars) == []

    staves, bars = page(
        (0.10, 0.14, [0.1, 0.5, 0.9]),
        (0.22, 0.26, [0.1, 0.5, 0.9]),
        (0.34, 0.38, [0.1, 0.3, 0.9]),   # only the ends in common
        (0.46, 0.50, [0.1, 0.3, 0.9]),
    )
    assert [len(s) for s in system_finder.group_staves(staves, bars)] == [2, 2]


def test_a_break_needs_the_white_to_go_with_it():
    """A poor scan loses a staff's barlines; it does not move the staff.

    Measured on B1b, whose last system's two staves disagree completely, and on
    page 3 of the fixture. Both sit closer together than the page's own
    within-system spacing, so there is no break to find.
    """
    staves, bars = page(
        (0.10, 0.14, [0.3, 0.5]),
        (0.22, 0.26, [0.3, 0.5]),
        (0.42, 0.46, [0.35, 0.55]),
        (0.53, 0.57, [0.62]),          # nothing in common, but no more space either
    )
    assert [len(s) for s in system_finder.group_staves(staves, bars)] == [2, 2]


def test_a_staff_with_no_barline_of_its_own_is_decided_by_the_gap():
    staves, bars = page(
        (0.10, 0.14, [0.3, 0.5]),
        (0.21, 0.25, []),              # a system one bar wide says nothing
        (0.50, 0.54, [0.4]),
        (0.62, 0.66, [0.4]),
    )
    assert [len(s) for s in system_finder.group_staves(staves, bars)] == [2, 2]


def test_one_staff_on_the_page_is_one_system():
    staves, bars = page((0.10, 0.14, [0.3]))
    assert [len(s) for s in system_finder.group_staves(staves, bars)] == [1]


def test_a_boundary_is_halfway_between_two_systems():
    staves, bars = page(
        (0.10, 0.14, [0.3, 0.5]),
        (0.22, 0.26, [0.3, 0.5]),
        (0.40, 0.44, [0.6]),
        (0.52, 0.56, [0.6]),
    )
    bands = system_finder.bands_for_page(2, staves, bars)
    assert [b.index for b in bands] == [1, 2]
    assert all(b.page == 2 for b in bands)
    assert bands[0].bottom == pytest.approx(0.33)      # 0.26 -> 0.40
    assert bands[1].top == pytest.approx(0.33)         # contiguous, no gap


def test_the_outer_edges_are_given_room_and_clamped_to_the_page():
    """Too generous a band costs white paper; too tight a one cuts the words off."""
    staves, bars = page(
        (0.02, 0.06, [0.3, 0.5]),
        (0.14, 0.18, [0.3, 0.5]),
        (0.80, 0.84, [0.6]),
        (0.92, 0.96, [0.6]),
    )
    bands = system_finder.bands_for_page(1, staves, bars)
    assert bands[0].top == 0.0                          # would be below zero
    assert bands[-1].bottom == 1.0                      # would be past the page
    assert bands[0].top < 0.02 and bands[-1].bottom > 0.96


def test_a_page_with_no_staves_proposes_nothing():
    assert system_finder.bands_for_page(1, [], []) == []


# --- reaching homr --------------------------------------------------------


def test_the_helper_runs_under_the_engine_that_would_read_the_page(monkeypatch, tmp_path):
    """Proposing bands and reading music have to be the same homr.

    The engine's own command runs homr's CLI; this needs its interpreter, and a
    checkout engine needs its working copy in front of it on PYTHONPATH.
    """
    venv_bin = tmp_path / "bin"
    venv_bin.mkdir()
    (venv_bin / "homr").write_text("")
    python = venv_bin / "python"
    python.write_text("")
    python.chmod(0o755)

    installed = omr.Engine(key="default", label="main", command=[str(venv_bin / "homr")],
                           default=True)
    monkeypatch.setattr(omr, "default_engine", lambda: installed)
    command, env = system_finder._helper_command(None)
    assert command == [str(python), system_finder.HELPER]
    assert env == {}

    checkout = omr.Engine(key="wt", label="branch", command=[str(python), "-c", "..."],
                          env={"PYTHONPATH": "/somewhere/homr"})
    command, env = system_finder._helper_command(checkout)
    assert command == [str(python), system_finder.HELPER]
    assert env == {"PYTHONPATH": "/somewhere/homr"}


def test_no_homr_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(omr, "default_engine", lambda: None)
    with pytest.raises(omr.HomrMissing) as raised:
        system_finder._helper_command(None)
    assert "install-homr.sh" in str(raised.value)


def test_a_page_homr_could_not_read_says_so(monkeypatch, tmp_path):
    image = tmp_path / "page.png"
    image.write_bytes(b"")

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "No noteheads found"

    monkeypatch.setattr(system_finder, "_helper_command",
                        lambda engine: (["/bin/true"], {}))
    monkeypatch.setattr(system_finder.subprocess, "run", lambda *a, **k: Failed())
    with pytest.raises(omr.HomrError) as raised:
        system_finder.staves_on_page(str(image))
    assert "No noteheads found" in str(raised.value)


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
