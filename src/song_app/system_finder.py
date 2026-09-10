"""Ask homr to propose printed-system bounds, with the pre-#213 path as fallback.

The supported implementation now belongs to the homr fork.  This adapter keeps the
song app's one-heavy-slot-per-page scheduling and turns homr's machine-readable JSON
into the existing :class:`SystemBounds` values.  It never saves a proposal.

Until the merged fork has been installed and re-measured on the host, an older homr
that does not know ``--find-system-bounds`` falls back to the previous app-side helper.
That fallback is deliberately isolated in :mod:`system_finder_legacy`; new grouping
work belongs in homr, not here.
"""

from __future__ import annotations

import json
import os
import subprocess
from contextlib import nullcontext
from typing import Callable, Dict, List, Optional

from . import heavy_slot, omr, pdf_systems, system_finder_legacy as legacy
from .omr import Engine, HomrError, HomrMissing
from .pdf_systems import SystemBounds

Logger = Callable[[str], None]
FIND_DPI = int(os.getenv("SYSTEM_FIND_DPI", "200"))
DEFAULT_TIMEOUT = 300

# Compatibility exports for callers/tests written before #213.  Production proposal
# work below does not use these; they stay only while the old installed homr may need
# the fallback implementation.
TOL_X = legacy.TOL_X
EDGE_X = legacy.EDGE_X
AGREE = legacy.AGREE
SLACK = legacy.SLACK
group_staves = legacy.group_staves
bands_for_page = legacy.bands_for_page
_interior_barlines = legacy._interior_barlines
_agreement = legacy._agreement
_gap_threshold = legacy._gap_threshold
staves_on_page = legacy.staves_on_page


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
    """Whether this is the expected old-homr answer that permits compatibility fallback."""
    lowered = stderr.lower()
    return "--find-system-bounds" in lowered and (
        "unrecognized arguments" in lowered or "no such option" in lowered
    )


def _page_from_homr(
    pdf_path: str,
    page: int,
    *,
    engine: Engine,
    dpi: int,
    log: Logger,
    timeout: int = DEFAULT_TIMEOUT,
) -> Optional[List[SystemBounds]]:
    """Ask supported homr for one page; return ``None`` only for an older CLI."""
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
            return None
        raise HomrError(
            f"homr could not propose systems for page {page}.\n"
            + "\n".join((result.stderr or "").splitlines()[-20:])
        )

    try:
        payload = json.loads(result.stdout)
        rows = payload["systems"]
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
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HomrError(f"Could not read homr's system proposal for page {page}: {exc}") from exc

    if any(bound.page != page for bound in bounds):
        raise HomrError(f"homr returned a system for the wrong page while proposing page {page}")
    return bounds


def find_bands(
    pdf_path: str,
    out_dir: Optional[str] = None,
    engine: Optional[Engine] = None,
    log: Logger = _noop,
    dpi: int = FIND_DPI,
    queue: bool = True,
) -> List[SystemBounds]:
    """Request homr's proposal page by page, unsaved, falling back only for old homr."""
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
        if found is None:
            log("Installed homr has no supported system-bound proposal; using compatibility fallback")
            return legacy.find_bands(
                pdf_path,
                out_dir=out_dir,
                engine=engine,
                log=log,
                dpi=dpi,
                queue=queue,
            )
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
