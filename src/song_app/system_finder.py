"""Ask homr to propose printed-system bounds.

The grouping rule belongs to the homr fork (``homr/system_finder.py``,
eerovil/homr#65).  This adapter keeps the song app's one-heavy-slot-per-page
scheduling and turns homr's machine-readable JSON into the existing
:class:`SystemBounds` values.  It never saves a proposal.

A homr older than that fork does not know ``--find-system-bounds``.  The app-side
copy of the rule that used to stand in for it was removed by #144, once the fork was
installed and re-measured, so such a homr is told to update rather than quietly
answered by a second implementation.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from contextlib import nullcontext
from typing import Callable, Dict, List, Optional

from . import heavy_slot, omr, pdf_systems
from .omr import Engine, HomrError, HomrMissing
from .pdf_systems import SystemBounds

Logger = Callable[[str], None]
FIND_DPI = int(os.getenv("SYSTEM_FIND_DPI", "200"))
DEFAULT_TIMEOUT = 300

def _noop(_message: str) -> None:
    pass


def _engine_command(engine: Engine) -> List[str]:
    """Return the public homr CLI command for either an install or checkout engine."""
    if len(engine.command) == 1:
        return [engine.command[0]]
    # Checkout engines run the same package through ``python -c`` for ordinary OMR.
    # Proposal mode is a public homr CLI feature, so invoke that module directly while
    # preserving the engine's PYTHONPATH.
    return [engine.command[0], "-m", "homr.main"]


def _unsupported(stderr: str) -> bool:
    """Whether homr rejected the proposal flag itself, i.e. is too old to have it."""
    # argparse also prints supported options in its usage text. Finding the flag
    # there must not turn an unrelated CLI error into "update homr".
    return re.search(
        r"(?:unrecognized arguments|no such option):[^\r\n]*"
        r"(?<![\w-])--find-system-bounds(?![\w-])",
        stderr,
        re.IGNORECASE,
    ) is not None


def _page_from_homr(
    pdf_path: str,
    page: int,
    *,
    engine: Engine,
    dpi: int,
    log: Logger,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[SystemBounds]:
    """Ask homr for one page's proposal."""
    command = _engine_command(engine) + [
        pdf_path,
        "--gpu",
        "no",
        "--find-system-bounds",
        "--system-page",
        str(page),
        "--system-dpi",
        str(dpi),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **engine.env},
        )
    except OSError as exc:
        raise HomrMissing(f"Could not run {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HomrError(f"Looking for systems did not finish within {timeout}s.") from exc

    for line in (result.stderr or "").splitlines():
        if line.strip():
            log(line.rstrip())
    if result.returncode != 0:
        if _unsupported(result.stderr or ""):
            raise HomrError(
                f"This homr ({engine.label}) is too old to propose systems: it has no "
                "--find-system-bounds. Update it (scripts/install-homr.sh, or git pull "
                "in a working copy) or draw the bands by hand."
            )
        raise HomrError(
            f"homr could not propose systems for page {page}.\n"
            + "\n".join((result.stderr or "").splitlines()[-20:])
        )

    try:
        payload = json.loads(result.stdout)
        rows = payload["systems"]
        if not isinstance(rows, list):
            raise ValueError("systems must be a list")
        bounds = [
            SystemBounds(
                index=int(row["index"]),
                page=int(row["page"]),
                top=float(row["top"]),
                bottom=float(row["bottom"]),
                measure_start=int(row.get("measure_start", 0)),
                measure_end=int(row.get("measure_end", 0)),
            )
            for row in rows
        ]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise HomrError(f"Could not read homr's system proposal for page {page}: {exc}") from exc

    if any(bound.page != page for bound in bounds):
        raise HomrError(f"homr returned a system for the wrong page while proposing page {page}")
    if any(bound.index < 1 or not 0.0 <= bound.top < bound.bottom <= 1.0 for bound in bounds):
        raise HomrError(f"homr returned invalid system bounds while proposing page {page}")
    return bounds


def find_bands(
    pdf_path: str,
    out_dir: Optional[str] = None,
    engine: Optional[Engine] = None,
    log: Logger = _noop,
    dpi: int = FIND_DPI,
    queue: bool = True,
) -> List[SystemBounds]:
    """Request homr's proposal page by page, unsaved."""
    engine = engine or omr.default_engine()
    if not engine:
        raise HomrMissing(
            f"homr is not installed ({omr.homr_binary()}). Run scripts/install-homr.sh, "
            "or set HOMR_BIN if it lives somewhere else."
        )

    pages = pdf_systems.page_count(pdf_path)
    proposed: List[SystemBounds] = []
    for page in range(1, pages + 1):
        lease = (
            heavy_slot.heavy_slot(f"song app find systems p{page}", log=log)
            if queue
            else nullcontext(heavy_slot.Slot())
        )
        with lease as slot:
            watched = slot.guard(log)
            watched(f"Looking for systems on page {page} of {pages}")
            found = _page_from_homr(
                pdf_path,
                page,
                engine=engine,
                dpi=dpi,
                log=watched,
            )
            slot.check()
        log(f"Page {page}: {len(found)} system(s)")
        for bound in found:
            proposed.append(
                SystemBounds(
                    index=len(proposed) + 1,
                    page=page,
                    top=bound.top,
                    bottom=bound.bottom,
                    measure_start=bound.measure_start,
                    measure_end=bound.measure_end,
                )
            )
    return proposed


# --- the quick finder: no homr, no heavy slot ------------------------------
#
# Asking homr is ~8s a page and needs homr installed. Most of the time a person
# is going to drag the bands into place anyway, so a proposal that is nearly
# right in under a second a page is worth more than one that is right in a
# minute. This reads the page itself, deterministically: the same page always
# comes back as the same bands.
#
# #80 tried reading the page and failed on two things, and this answers each.
# Staff lines broke at half a degree of skew and at ink dropout because they
# were looked for across the whole page width; here the page is cut into narrow
# vertical strips, each strip finds its own five-line staves, and a staff is
# kept when enough strips agree on it — a tilted or broken line is still
# straight and whole across one strip. And grouping relied on a bracket only
# some editions print; here two staves belong to one system when any single
# column of ink runs unbroken across the gap between them, which the systemic
# barline at the left does in every edition on this host, bracket or not.
#
# Measured against the bands a person drew on all 14 scanned songs on this host
# and the benchmark's B1a/B1b: every page comes back with the number of systems
# it prints, with each internal boundary within ~0.03 of page height of the
# hand-drawn one. The proposal is still only a proposal.

QUICK_DPI = 150          # the dpi the Systems editor shows, so the render is shared
_INK = 230               # grey up to this is ink; scans print faint staff lines
_STRIPS = 24             # narrow enough that a skewed line stays in a few rows
_LINE_FILL = 0.45        # share of a strip's width a staff line row has to cover
_STRIP_AGREE = 0.25      # share of strips that must see a staff for it to count
_JOIN_FILL = 0.9         # share of the gap a joining column must cover
_EDGE = 0.03             # page edges ignored for joins: a scan's border is a line


def _strip_staves(strip) -> List[tuple]:
    """Five evenly spaced line rows in one vertical strip: (top, bottom, spacing)."""
    import numpy as np

    fill = strip.mean(axis=1)
    lines: List[List[int]] = []
    for row in np.flatnonzero(fill >= _LINE_FILL).tolist():
        if lines and row - lines[-1][1] <= 1:
            lines[-1][1] = row
        else:
            lines.append([row, row])
    centres = [(a + b) / 2 for a, b in lines if b - a <= 6]   # thicker is a beam
    found = []
    i = 0
    while i + 4 < len(centres):
        gaps = np.diff(centres[i:i + 5])
        if gaps.min() >= 3 and gaps.max() <= 1.35 * gaps.min() and gaps.max() <= 40:
            found.append((centres[i], centres[i + 4], float(gaps.mean())))
            i += 5
        else:
            i += 1
    return found


def _staves(ink) -> List[tuple]:
    """Every staff on the page, top to bottom: (top row, bottom row, line spacing)."""
    import numpy as np

    width = ink.shape[1]
    edges = np.linspace(width * 0.08, width * 0.92, _STRIPS + 1).astype(int)
    seen = sorted(s for k in range(_STRIPS)
                  for s in _strip_staves(ink[:, edges[k]:edges[k + 1]]))
    clusters: List[List[tuple]] = []
    for s in seen:
        if clusters:
            here = float(np.median([(t[0] + t[1]) / 2 for t in clusters[-1]]))
            if abs((s[0] + s[1]) / 2 - here) <= (s[1] - s[0]) * 0.6:
                clusters[-1].append(s)
                continue
        clusters.append([s])
    need = max(2, int(_STRIPS * _STRIP_AGREE))
    return [tuple(float(np.median([t[k] for t in c])) for k in range(3))
            for c in clusters if len(c) >= need]


def _joined(ink, upper: tuple, lower: tuple) -> bool:
    """Whether one column of ink spans the whole gap between two staves."""
    space = max(upper[2], lower[2])
    # A staff space clear of each staff, so a tilted page's lines are not counted.
    lo, hi = int(upper[1] + space * 1.2), int(lower[0] - space * 1.2)
    if hi - lo < 3:
        return True
    gap = ink[lo:hi]
    gap = gap[:, :-2] | gap[:, 1:-1] | gap[:, 2:]      # a barline can lean a pixel
    margin = int(gap.shape[1] * _EDGE)
    return bool(gap[:, margin:gap.shape[1] - margin].mean(axis=0).max() >= _JOIN_FILL)


def page_systems(image: "Image.Image") -> List[tuple]:
    """The printed systems of one page image, as (top, bottom) fractions of height."""
    import numpy as np

    ink = np.asarray(image.convert("L")) < _INK
    height = ink.shape[0]
    staves = _staves(ink)
    if not staves:
        return []
    joins = [_joined(ink, a, b) for a, b in zip(staves, staves[1:])]
    if len(staves) > 2 and not any(joins):
        # Nothing joins anything: either one-staff systems, or an edition that
        # prints no systemic barline. Let the gaps decide when they clearly split
        # into two sizes, and otherwise call each staff its own system.
        gaps = [b[0] - a[1] for a, b in zip(staves, staves[1:])]
        if max(gaps) > 1.25 * min(gaps):
            cut = (max(gaps) + min(gaps)) / 2
            joins = [g < cut for g in gaps]

    systems: List[List[float]] = []
    for i, staff in enumerate(staves):
        if i and joins[i - 1]:
            systems[-1][1] = staff[1]
        else:
            systems.append([staff[0], staff[1]])

    # Edges halfway between systems, so the lyrics under a system's last staff
    # stay with it. The first and last get a little more than the room their
    # neighbour gets: a generous band costs white paper, a tight one cuts words off.
    halves = [(b[0] - a[1]) / 2 for a, b in zip(systems, systems[1:])]
    outer = 1.2 * max(halves) if halves else 8 * float(np.median([s[2] for s in staves]))
    bands = []
    for i, (top, bottom) in enumerate(systems):
        lo = (systems[i - 1][1] + top) / 2 if i else max(0.0, top - outer)
        hi = (bottom + systems[i + 1][0]) / 2 if i + 1 < len(systems) else min(height, bottom + outer)
        bands.append((round(lo / height, 4), round(hi / height, 4)))
    return bands


def quick_bands(pdf_path: str, out_dir: str, log: Logger = _noop) -> List[SystemBounds]:
    """Propose a band for every printed system without homr, unsaved."""
    from PIL import Image

    pages = pdf_systems.page_count(pdf_path)
    proposed: List[SystemBounds] = []
    for page in range(1, pages + 1):
        image = Image.open(pdf_systems.render_page(pdf_path, page, QUICK_DPI, out_dir))
        found = page_systems(image)
        log(f"Page {page}: {len(found)} system(s)")
        for top, bottom in found:
            proposed.append(SystemBounds(index=len(proposed) + 1, page=page,
                                         top=top, bottom=bottom))
    return proposed


# ---------------------------------------------------------------- one bar of one staff
#
# The Fix panel shows a whole printed system beside homr's readings of one bar, and
# a crop of four staves and five bars does not say which of them a choice is about
# (#368). This finds the box to draw round it, with the same staff-line finder as
# above. It answers only what it can check: the staff when the crop has exactly the
# staves the score says the system prints, and the bar when exactly as many
# barlines are found as the system has bars. Otherwise the whole staff, or nothing;
# a box round the wrong bar would be worse than none.
#
# A barline is a column of ink from a staff's top line to its bottom one. So is a
# stem, and two things tell them apart: a stem has its notehead beside it where a
# barline has only the staff lines, and a barline stands at the same x on every
# staff of the system, where stems line up only when the voices move together.
# Measured on the crops of ten songs here, the two together find every bar on about
# 60% of the systems, and the count check turns the rest into a staff-wide box.

_BARLINE_FILL = 0.9      # share of a staff's height a barline column covers
_BARLINE_CLEAR = 0.25    # share of the rows beside it a barline may have ink in


def _barline_candidates(ink, top: float, bottom: float, space: float) -> List[tuple]:
    """(x, clear) for each full-height column of ink across one staff.

    `clear` is whether nothing but staff lines stands within most of a staff space
    either side of it (or only paper to its right: the closing line).
    """
    import numpy as np

    rows = ink[int(round(top)):int(round(bottom)) + 1]
    if rows.shape[0] < 4:
        return []
    width = rows.shape[1]
    lean = rows.copy()
    lean[:, 1:-1] = rows[:, :-2] | rows[:, 1:-1] | rows[:, 2:]   # a barline can lean a pixel
    groups: List[List[int]] = []
    # Columns closer than a staff space are one line: a thick barline, a double
    # barline, or a repeat sign's two strokes.
    for x in np.flatnonzero(lean.mean(axis=0) >= _BARLINE_FILL).tolist():
        if groups and x - groups[-1][-1] <= space:
            groups[-1].append(x)
        else:
            groups.append([x])
    lines = rows.mean(axis=1) >= 0.3
    lines = lines | np.roll(lines, 1) | np.roll(lines, -1)
    between = rows[~lines]
    reach = max(3, int(0.9 * space))

    def busy(a: int, b: int) -> float:
        window = between[:, max(0, a):min(width, b)]
        return float(window.any(axis=1).mean()) if window.size else 0.0

    out = []
    for g in groups:
        left = busy(g[0] - 2 - reach, g[0] - 2)
        right = busy(g[-1] + 3, g[-1] + 3 + reach)
        closing = not between[:, g[-1] + 3:].any()
        out.append(((g[0] + g[-1]) / 2,
                    left <= _BARLINE_CLEAR and (right <= _BARLINE_CLEAR or closing)))
    return out


def _staff_start(ink, top: float, bottom: float) -> float:
    """The x where the staff lines begin."""
    import numpy as np

    rows = ink[int(round(top)):int(round(bottom)) + 1]
    filled = np.flatnonzero(rows.mean(axis=0) >= 0.3).tolist()
    return float(filled[0]) if filled else 0.0


def barlines(ink, staves: List[tuple]) -> List[float]:
    """The x of every barline of a system after its opening line, left to right."""
    import numpy as np

    if not staves:
        return []
    space = float(np.median([s[2] for s in staves]))
    per = [_barline_candidates(ink, *s) for s in staves]
    start = max(_staff_start(ink, s[0], s[1]) for s in staves)
    out = []
    for x, _ in per[0]:
        if x - start <= 2 * space:
            continue  # the opening line, with a bracket or clef against it
        hits = [next((clear for y, clear in p if abs(x - y) <= 0.6 * space), None) for p in per]
        if all(h is not None for h in hits) and 2 * sum(hits) >= len(hits):
            out.append(x)
    return out


def bar_box(image: "Image.Image", staff: int, staves: int,
            bar: Optional[int] = None, bars: Optional[int] = None) -> Optional[Dict]:
    """Where staff `staff` of `staves` (and bar `bar` of `bars`) is in a system crop.

    Fractions of the image, `{top, bottom, left, right, bar}`; `bar` says whether the
    box is narrowed to the bar or covers the whole staff. None when the crop does not
    show `staves` staves, so the one meant cannot be told.
    """
    import numpy as np

    ink = np.asarray(image.convert("L")) < _INK
    height, width = ink.shape
    found = _staves(ink)
    if len(found) != staves or not 1 <= staff <= staves:
        return None
    top, bottom, space = found[staff - 1]
    box = {"top": max(0.0, top - 1.5 * space) / height,
           "bottom": min(float(height), bottom + 1.5 * space) / height,
           "left": 0.0, "right": 1.0, "bar": False}
    if bar and bars and 1 <= bar <= bars:
        lines = [_staff_start(ink, top, bottom)] + barlines(ink, found)
        if len(lines) == bars + 1:
            box.update(left=lines[bar - 1] / width, right=lines[bar] / width, bar=True)
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in box.items()}
