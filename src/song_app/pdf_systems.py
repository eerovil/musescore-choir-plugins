"""Cut a score PDF into one image per printed system.

The original PDF is the only place some things exist -- lyrics the OCR dropped,
slurs it lost, what an over-full measure was meant to be. Read as whole pages it
is close to useless: a full A4 rendered small enough to look at is far too coarse
to see a slur or count noteheads. Cropped per system at 400 dpi it is legible.

One module, two consumers: the web app shows a system beside the lyric cell being
typed, and an agent reads the same PNG files off disk. Both call `system_images`.

Two stages, with very different reliability -- measured across nine real songs:

* **Finding staves** is solid. Staff lines are the most dependable feature on a
  page: a long uninterrupted horizontal run of ink. Counting *lines* rather than
  ink rows matters, because a scanned line is ~9 rows thick and an engraved
  hairline is 1, and a threshold that admits one discards the other.

* **Grouping staves into systems is not solved here.** This module guesses from
  the bracket in the left margin, which is exact on some editions and absent on
  others -- one scan produced 45 systems for 45 staves because nothing joined
  them. Treat what comes back as a *proposal* to be confirmed, never as truth.

Hence `system_images` refuses to attach measure numbers unless the number of
systems it found matches the number the score declares. A wrong alignment is
worse than none: it would put lyrics on the wrong measures silently, whereas a
missing one is visible immediately.
"""
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from src.clean_score.utils import per_system

DPI = int(os.getenv("SYSTEM_CROP_DPI", "400"))
_MANIFEST = "systems.json"

# A staff line is a near-full-width horizontal run of ink. 0.35 is well below the
# width of the shortest system and well above any beam, slur or word.
_LINE_RUN = 0.35
# Rows this close together belong to one staff (its five lines, as a block).
_STAFF_MERGE_PX = 0.012         # of page height
# A staff is five lines. Count the lines, not the ink rows: a scanned line is
# ~9 rows thick while an engraved hairline is 1, so a row count that admits a
# scan discards vector engravings wholesale.
_MIN_STAFF_LINES = 4
# The bracket joining a system's staves lives in the left margin.
_BRACKET_WIDTH = 0.08           # of page width
_BRACKET_COVER = 0.5            # of the gap's height, in one column
_GAP_INSET = 3                  # px clear of the staff lines themselves
# Ink darker than this counts (the scans are grey, not black).
_INK = 250


@dataclass(frozen=True)
class SystemImage:
    """One printed system, cropped out of the scan."""
    index: int              # 1-based, running across the whole score
    page: int               # 1-based page it was found on
    path: str               # the PNG
    measure_start: int = 0  # 0 when the measure range could not be matched
    measure_end: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


def _render_page(pdf_path: str, page: int, dpi: int, out_dir: str) -> str:
    stem = os.path.join(out_dir, f"page-{page:02d}")
    out = stem + ".png"
    if not os.path.exists(out):
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
             "-png", "-singlefile", pdf_path, stem],
            check=True, capture_output=True,
        )
    return out


def _page_count(pdf_path: str) -> int:
    out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise RuntimeError(f"Could not read the page count of {pdf_path}")


def _staff_line_rows(ink: np.ndarray) -> List[int]:
    """Rows that look like a staff line: a long uninterrupted horizontal ink run.

    Max run per row, computed a column at a time so each step is one vector op
    over every row at once -- a per-pixel Python loop over a 400 dpi page is
    tens of millions of iterations.
    """
    h, w = ink.shape
    need = int(w * _LINE_RUN)
    run = np.zeros(h, dtype=np.int32)
    best = np.zeros(h, dtype=np.int32)
    for j in range(w):
        col = ink[:, j]
        run = np.where(col, run + 1, 0)
        np.maximum(best, run, out=best)
    return np.flatnonzero(best >= need).tolist()


def _line_count(rows: List[int]) -> int:
    """How many distinct staff lines a group of ink rows represents."""
    return 1 + sum(1 for a, b in zip(rows, rows[1:]) if b - a > 1)


def _group(values: List[int], max_gap: float) -> List[List[int]]:
    out: List[List[int]] = []
    for v in values:
        if out and v - out[-1][-1] <= max_gap:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _bracketed(ink: np.ndarray, above: tuple, below: tuple, width: int) -> bool:
    """True if the bracket down the left margin joins these two staves.

    Gap size alone cannot decide this: one page's gap between systems can be
    smaller than another page's gap within one, so no threshold separates them.
    The bracket is structural and unambiguous -- on the reference scan it reads
    1.0 where two staves share a system and below 0.1 where they do not.
    """
    top, bottom = above[1] + _GAP_INSET, below[0] - _GAP_INSET
    if bottom <= top:
        return True
    band = ink[top:bottom, :int(width * _BRACKET_WIDTH)]
    return bool(band.size and band.mean(axis=0).max() >= _BRACKET_COVER)


def _system_bands(img: Image.Image) -> List[tuple]:
    """(top, bottom) pixel bands, one per system, splitting the page at the gaps."""
    a = np.asarray(img.convert("L"))
    ink = a < _INK
    h, w = a.shape

    # A staff's five lines sit far closer together than two staves ever do, so
    # grouping the detected rows at that scale yields one group per staff.
    staves = [g for g in _group(_staff_line_rows(ink), h * _STAFF_MERGE_PX)
              if _line_count(g) >= _MIN_STAFF_LINES]
    if not staves:
        return []
    spans = [(g[0], g[-1]) for g in staves]
    if len(spans) == 1:
        return [(0, h)]

    groups: List[List[tuple]] = [[spans[0]]]
    for prev, cur in zip(spans, spans[1:]):
        if _bracketed(ink, prev, cur, w):
            groups[-1].append(cur)
        else:
            groups.append([cur])

    # Cut midway between systems, so lyrics above and below stay with their system.
    bands = []
    for i, g in enumerate(groups):
        top = 0 if i == 0 else (groups[i - 1][-1][1] + g[0][0]) // 2
        bottom = h if i == len(groups) - 1 else (g[-1][1] + groups[i + 1][0][0]) // 2
        bands.append((top, bottom))
    return bands


def system_images(
    pdf_path: str,
    mscx_path: Optional[str] = None,
    out_dir: Optional[str] = None,
    dpi: int = DPI,
) -> List[SystemImage]:
    """Crop `pdf_path` into one PNG per printed system, newest-first cached.

    Pass `mscx_path` -- a score that still has its line breaks, i.e. the converted
    input, not a normal-mode cleaned file -- to label each crop with the measure
    range it covers. When the number of systems found on the page does not match
    the number the score declares, the crops are still returned but their measure
    numbers are left at 0 rather than being zipped into a wrong alignment.
    """
    out_dir = out_dir or os.path.join(os.path.dirname(pdf_path), ".systems")
    cached = _cached(pdf_path, out_dir, dpi)
    if cached is not None:
        return cached

    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory() as raw:
        found: List[SystemImage] = []
        for page in range(1, _page_count(pdf_path) + 1):
            img = Image.open(_render_page(pdf_path, page, dpi, raw))
            for top, bottom in _system_bands(img):
                idx = len(found) + 1
                path = os.path.join(out_dir, f"system-{idx:02d}.png")
                img.crop((0, top, img.width, bottom)).save(path)
                found.append(SystemImage(index=idx, page=page, path=path))

    found = _label(found, mscx_path)
    _write_manifest(pdf_path, out_dir, dpi, found)
    return found


def _label(images: List[SystemImage], mscx_path: Optional[str]) -> List[SystemImage]:
    if not mscx_path or not os.path.exists(mscx_path):
        return images
    from lxml import etree
    root = etree.parse(mscx_path).getroot()
    ranges = per_system.system_ranges(root)
    if len(ranges) != len(images):
        return images
    return [
        SystemImage(i.index, i.page, i.path, r.start, r.end)
        for i, r in zip(images, ranges)
    ]


def _write_manifest(pdf_path: str, out_dir: str, dpi: int, images: List[SystemImage]) -> None:
    with open(os.path.join(out_dir, _MANIFEST), "w", encoding="utf-8") as f:
        json.dump({
            "pdf_mtime": os.path.getmtime(pdf_path),
            "dpi": dpi,
            "systems": [i.to_dict() for i in images],
        }, f, indent=2)


def _cached(pdf_path: str, out_dir: str, dpi: int) -> Optional[List[SystemImage]]:
    manifest = os.path.join(out_dir, _MANIFEST)
    if not os.path.exists(manifest):
        return None
    try:
        with open(manifest, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("dpi") != dpi or data.get("pdf_mtime") != os.path.getmtime(pdf_path):
        return None
    images = [SystemImage(**s) for s in data.get("systems", [])]
    return images if all(os.path.exists(i.path) for i in images) else None
