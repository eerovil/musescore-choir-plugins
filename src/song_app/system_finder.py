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
from typing import Callable, List, Optional

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
