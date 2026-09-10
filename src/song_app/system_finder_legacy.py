"""Propose where each printed system sits on a page.

The scan reads exactly the bands ``.systems.json`` names, and until now every
one of them was drawn by hand — by a person dragging in the Systems editor, or
by an AI reading a page with a percentage ruler over it. That is the right
place for the *judgement* to live and this module does not take it away: what
comes back here is a **proposal**, returned to the editor unsaved, for a person
to correct and save. Nothing writes bounds.

**Why this can work when issue #80 could not.** That attempt read the pixels
itself: it looked for staff lines with morphology and grouped staves into
systems by the bracket down the left margin. Staff-line detection died at half a
degree of skew and at 20% ink dropout, and only some editions print the bracket
— across nine real songs the grouping agreed with the score twice. This asks
**homr** instead, which finds staves for a living: the same segmentation network
and the same ``detect_staff`` that read the music, stopped before any of it is
parsed. A page costs seconds rather than the ~30s of a full read, and when it
fails it fails the way the scan will fail on the same page, which is the honest
answer.

**Grouping is decided by the barlines, not by the gaps.** Which staves make up
one system is the part homr does not answer: its ``MultiStaff`` is a grand staff
or a brace, which choral engraving mostly does not print. The obvious rule —
staves close together are one system — is measured here and does not work: on
page 1 of the fixture the gaps *inside* a system run 0.060–0.077 of the page and
the gaps *between* systems run 0.067–0.086, so the two ranges overlap and no
threshold separates them. What does separate them is what the music says: two
staves of the same system carry the same bars, so their barlines stand at the
same x positions, and two staves of different systems hold different bars and
theirs do not. On all seven pages measured, that agreement is 0.6–1.0 within a
system and 0.0–0.5 across a break.

The gaps still have one job, as a **veto**: a break has to show at least as much
white as an ordinary within-system gap does. That is what catches the case where
a poor scan loses a staff's barlines and the agreement collapses on a pair that
is plainly one system (B1b's last system, and one pair on page 3 of the fixture).

Coordinates are fractions of page height throughout — the units
``.systems.json`` uses — so nothing here depends on the resolution it read at.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import heavy_slot, omr, pdf_systems
from .omr import Engine, HomrError, HomrMissing
from .pdf_systems import SystemBounds

Logger = Callable[[str], None]

#: The helper that runs inside homr's venv and reports the staves.
HELPER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts", "homr_staves.py",
)

#: What to rasterise a page at before handing it to homr. homr resizes to 1920
#: wide itself, so more than this buys nothing and costs the render.
FIND_DPI = int(os.getenv("SYSTEM_FIND_DPI", "200"))

#: Segmentation is seconds; this is a wedged-process guard, not a budget.
DEFAULT_TIMEOUT = 300

#: Two barlines are "at the same place" within this fraction of page width.
TOL_X = 0.008

#: A barline this close to a staff's own left or right end is the system's
#: opening or closing line, which every system has wherever its bars fall.
EDGE_X = 0.02

#: How much of the smaller barline set has to line up for one system.
AGREE = 0.6

#: Slack on the gap comparisons, so two gaps that are the same gap do not turn
#: on the last bits of a subtraction. Deliberately tiny: on the pages measured,
#: a real break is 20% wider than an ordinary gap and the closest false one is
#: 6% narrower, so there is nothing here to tune.
SLACK = 1.001


def _noop(_msg: str) -> None:
    pass


# --- the pixels ----------------------------------------------------------


def staves_on_page(
    image_path: str,
    engine: Optional[Engine] = None,
    log: Logger = _noop,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict:
    """Ask homr where the staves and barlines are on one page image.

    Returns the helper's JSON: ``staves`` and ``bar_lines``, each a box in
    fractions of the image. Raises :class:`omr.HomrError` when homr could not
    read the page, the same exception the scan raises for the same reason.
    """
    command, env = _helper_command(engine)
    try:
        out = subprocess.run(
            command + [image_path],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **env},
        )
    except OSError as exc:
        raise HomrMissing(f"Could not run {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HomrError(f"Looking for staves did not finish within {timeout}s.") from exc
    for line in (out.stderr or "").splitlines():
        if line.strip():
            log(line.rstrip())
    if out.returncode != 0 or not (out.stdout or "").strip():
        raise HomrError(
            f"homr found no staves on {os.path.basename(image_path)}.\n"
            + "\n".join((out.stderr or "").splitlines()[-20:])
        )
    try:
        return json.loads(out.stdout)
    except ValueError as exc:
        raise HomrError(
            f"Could not read what homr said about {os.path.basename(image_path)}: {exc}"
        ) from exc


def _helper_command(engine: Optional[Engine]) -> Tuple[List[str], Dict[str, str]]:
    """``(argv, env)`` that runs :data:`HELPER` under ``engine``'s homr.

    The engine's own ``command`` runs homr's CLI; this needs its *interpreter*,
    which for the installed venv sits beside the binary and for a checkout
    engine is that same interpreter with the working copy in front of it on
    ``PYTHONPATH``. Both come off the engine rather than being guessed, so
    proposing bands and reading music use the same homr.
    """
    engine = engine or omr.default_engine()
    if not engine:
        raise HomrMissing(
            f"homr is not installed ({omr.homr_binary()}). Run scripts/install-homr.sh, "
            "or set HOMR_BIN if it lives somewhere else."
        )
    python = engine.command[0] if len(engine.command) > 1 else _python_beside(engine.command[0])
    if not python:
        raise HomrMissing(
            f"Could not find the interpreter beside {engine.command[0]}; "
            "looking for staves needs homr's own venv, not just its binary."
        )
    return [python, HELPER], dict(engine.env)


def _python_beside(binary: str) -> Optional[str]:
    if os.path.sep not in binary:
        return None
    python = os.path.join(os.path.dirname(binary), "python")
    return python if os.access(python, os.X_OK) else None


# --- the grouping --------------------------------------------------------


def _interior_barlines(staff: Dict, bar_lines: Sequence[Dict]) -> List[float]:
    """The x of every barline standing on this staff, minus its own two ends.

    The opening and closing lines are dropped because every system has them at
    the same place whatever bars it holds, so leaving them in makes any two
    staves agree a little and the evidence weaker for it.
    """
    found = []
    for bar in bar_lines:
        if bar["bottom"] < staff["top"] - 0.005 or bar["top"] > staff["bottom"] + 0.005:
            continue
        x = (bar["left"] + bar["right"]) / 2
        if x < staff["left"] + EDGE_X or x > staff["right"] - EDGE_X:
            continue
        found.append(x)
    return sorted(found)


def _agreement(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """How much of the smaller barline set the other one stands under.

    ``None`` when one of the staves has no interior barline to compare — a
    system of a single bar says nothing here, and is left to the gaps.
    """
    if not a or not b:
        return None
    hits_a = sum(1 for x in a if any(abs(x - y) <= TOL_X for y in b))
    hits_b = sum(1 for y in b if any(abs(x - y) <= TOL_X for x in a))
    return max(hits_a / len(a), hits_b / len(b))


def group_staves(staves: Sequence[Dict], bar_lines: Sequence[Dict]) -> List[List[Dict]]:
    """Split a page's staves into systems.

    Adjacent staves are one system when their barlines line up; where neither
    staff has an interior barline the gap decides, and a break is vetoed when
    the page shows no more white there than it does inside a system.
    """
    staves = sorted(staves, key=lambda s: s["top"])
    if len(staves) < 2:
        return [list(staves)] if staves else []

    bars = [_interior_barlines(s, bar_lines) for s in staves]
    gaps = [staves[i + 1]["top"] - staves[i]["bottom"] for i in range(len(staves) - 1)]
    scores = [_agreement(bars[i], bars[i + 1]) for i in range(len(staves) - 1)]

    same = [None if s is None else s >= AGREE for s in scores]

    inside = [g for g, s in zip(gaps, same) if s]
    if inside:
        ordinary = statistics.median(inside)
        # A break needs the white to go with it. A poor scan can lose a staff's
        # barlines outright, and then the agreement collapses on a pair the page
        # plainly prints as one system -- sitting *closer* together than the
        # page's own within-system spacing. Measured: B1b's last system at 0.91
        # of an ordinary gap and one pair on page 3 of the fixture at 0.97,
        # against real breaks at 1.05 to 1.37. Strictly closer, so a page that
        # happens to space its systems like its staves keeps every break the
        # barlines found.
        same = [True if s is False and g < ordinary / SLACK else s
                for s, g in zip(same, gaps)]
        same = [s if s is not None else g <= ordinary * SLACK
                for s, g in zip(same, gaps)]
    else:
        # Nothing to learn from the barlines anywhere on this page (every system
        # is one bar wide, or none was detected). Fall back to the widest step in
        # the sorted gaps, which is the best the geometry alone can do.
        same = [g < _gap_threshold(gaps) for g in gaps]

    systems: List[List[Dict]] = [[staves[0]]]
    for keep, staff in zip(same, staves[1:]):
        if keep:
            systems[-1].append(staff)
        else:
            systems.append([staff])
    return systems


def _gap_threshold(gaps: Sequence[float]) -> float:
    """Where the sorted gaps step up most, as a break/no-break line."""
    ordered = sorted(gaps)
    if len(ordered) < 2:
        return ordered[0] + 1.0            # one gap: no evidence of a break
    steps = [(ordered[i + 1] / max(ordered[i], 1e-6), i) for i in range(len(ordered) - 1)]
    _, cut = max(steps)
    return (ordered[cut] + ordered[cut + 1]) / 2


def bands_for_page(page: int, staves: Sequence[Dict],
                   bar_lines: Sequence[Dict]) -> List[SystemBounds]:
    """One band per system on a page, contiguous, indexed from 1.

    A boundary between two systems is put halfway between them, so the lyrics
    under one system's last staff and anything printed above the next stay with
    their own band. The first and last edges have no neighbour to halve, so they
    are given the same room again and clamped to the page: too generous a band
    costs a little white paper, and too tight a one cuts the words off.
    """
    systems = group_staves(staves, bar_lines)
    if not systems:
        return []
    tops = [s[0]["top"] for s in systems]
    bottoms = [s[-1]["bottom"] for s in systems]
    between = [tops[i + 1] - bottoms[i] for i in range(len(systems) - 1)]
    room = statistics.median(between) if between else 0.05

    bands = []
    for i in range(len(systems)):
        top = max(0.0, tops[i] - room) if i == 0 else (bottoms[i - 1] + tops[i]) / 2
        bottom = (min(1.0, bottoms[i] + room) if i == len(systems) - 1
                  else (bottoms[i] + tops[i + 1]) / 2)
        bands.append(SystemBounds(index=i + 1, page=page, top=top, bottom=bottom))
    return bands


# --- the whole PDF -------------------------------------------------------


def find_bands(
    pdf_path: str,
    out_dir: Optional[str] = None,
    engine: Optional[Engine] = None,
    log: Logger = _noop,
    dpi: int = FIND_DPI,
    queue: bool = True,
) -> List[SystemBounds]:
    """Propose a band for every printed system in a PDF, page by page.

    Indices run across the whole score, the way saved bounds do. Nothing is
    written: the caller shows this and a person saves it.

    **One heavy slot per page**, the rule :mod:`omr` settled: a page is seconds
    of every core, releasing between pages lets a render or a suite in, and a
    page that is interrupted costs that page rather than the PDF. A page homr
    cannot read raises — unlike the scan there is nothing partial worth
    keeping, because a missing band is music that would never be read at all.
    """
    scratch = tempfile.TemporaryDirectory(prefix="sysfind-")
    workdir = out_dir or scratch.name
    try:
        bands: List[SystemBounds] = []
        pages = pdf_systems.page_count(pdf_path)
        for page in range(1, pages + 1):
            image = pdf_systems.render_page(pdf_path, page, dpi, workdir)
            with _queued(f"song app find systems p{page}", log, queue) as slot:
                watched = slot.guard(log)
                watched(f"Looking for systems on page {page} of {pages}")
                found = staves_on_page(image, engine=engine, log=watched)
                slot.check()
            page_bands = bands_for_page(page, found["staves"], found["bar_lines"])
            log(f"Page {page}: {len(found['staves'])} staves in "
                f"{len(page_bands)} system(s)")
            for band in page_bands:
                bands.append(SystemBounds(index=len(bands) + 1, page=page,
                                          top=band.top, bottom=band.bottom))
        return bands
    finally:
        scratch.cleanup()


def _queued(label: str, log: Logger, queue: bool):
    """A heavy slot for this page, or an un-held one for a caller holding it."""
    if not queue:
        from contextlib import nullcontext
        return nullcontext(heavy_slot.Slot())
    return heavy_slot.heavy_slot(label, log=log)
