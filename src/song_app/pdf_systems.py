"""Crop a score PDF into one image per printed system.

The original PDF is the only place some things exist -- lyrics the OCR dropped,
slurs it lost, what an over-full measure was meant to be. Read as whole pages it
is close to useless: a full A4 rendered small enough to look at is far too coarse
to see a slur or count noteheads. Cropped per system at 400 dpi it is legible.

**Where the system boundaries come from is not decided here.** An AI reads the
pages and proposes them; a person corrects them in the app; this module renders,
crops, stores and labels. Deciding where a system starts turned out to be a poor
fit for image heuristics -- staff-line detection died at 1 degree of skew and at
20% ink dropout, and grouping staves into systems depended on a bracket that only
some editions print -- so the judgement belongs to whoever is looking at the page.

`page_images(..., grid=True)` draws a labelled percentage scale down the margin so
boundaries can be read off a ruler rather than estimated by eye. Bounds are stored
as fractions of page height, so they survive a change of resolution.

One module, two consumers: the web app draws the boundaries over the page and lets
them be dragged, and an agent reads the same crops off disk.
"""
import hashlib
import json
import hashlib
import math
import os
import subprocess
import threading
from dataclasses import dataclass, asdict, replace
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from src.clean_score.utils import per_system

DPI = int(os.getenv("SYSTEM_CROP_DPI", "400"))
PAGE_DPI = int(os.getenv("SYSTEM_PAGE_DPI", "150"))
BOUNDS_FILE = ".systems.json"

_GRID_STEP = 2          # percent between rules
_GRID_LABEL_EVERY = 10  # percent between labelled rules


@dataclass(frozen=True)
class SystemBounds:
    """One printed system, as a band of a page.

    `top`/`bottom` are fractions of page height (0.0 = top of page), so the same
    bounds crop correctly at any resolution.
    """
    index: int              # 1-based, running across the whole score
    page: int               # 1-based
    top: float
    bottom: float
    measure_start: int = 0  # 0 when the range is not known
    measure_end: int = 0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass(frozen=True)
class SystemImage:
    bounds: SystemBounds
    path: str

    @property
    def index(self) -> int:
        return self.bounds.index


def page_count(pdf_path: str) -> int:
    out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise RuntimeError(f"Could not read the page count of {pdf_path}")


def render_page(pdf_path: str, page: int, dpi: int, out_dir: str) -> str:
    """Rasterise one page, cached by (page, dpi) under out_dir.

    Rendered to a private name and moved into place, because the editor asks for
    every page at once: two requests for the same page would otherwise render over
    each other and one of them would serve a half-written file.
    """
    os.makedirs(out_dir, exist_ok=True)
    # The source has to be part of the cache name: a song folder renders both the
    # original scan and the cleaned score, and without this the second one to be
    # asked for is served the first one's pages.
    who = hashlib.sha1(os.path.abspath(pdf_path).encode("utf-8")).hexdigest()[:8]
    out = os.path.join(out_dir, f"page-{who}-{page:02d}@{dpi}.png")
    if os.path.exists(out):
        return out
    stem = os.path.join(out_dir, f".tmp-{os.getpid()}-{threading.get_ident()}-{who}-{page}@{dpi}")
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
         "-png", "-singlefile", pdf_path, stem],
        check=True, capture_output=True,
    )
    os.replace(stem + ".png", out)          # atomic; last writer wins, both are valid
    return out


def page_images(
    pdf_path: str,
    out_dir: str,
    dpi: int = PAGE_DPI,
    grid: bool = False,
) -> List[str]:
    """Rasterise every page. With `grid`, overlay a labelled percentage scale.

    The scale is what makes boundaries readable rather than guessable: a system
    can be reported as "38% to 54%" instead of estimated by eye, which is how
    crops end up clipping the lyric line under the bottom staff.
    """
    paths = []
    for page in range(1, page_count(pdf_path) + 1):
        raw = render_page(pdf_path, page, dpi, out_dir)
        paths.append(_with_grid(raw) if grid else raw)
    return paths


def _with_grid(page_png: str) -> str:
    out = page_png.replace(".png", "-grid.png")
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(page_png):
        return out
    tmp = f"{out}.{os.getpid()}.{threading.get_ident()}.tmp"
    img = Image.open(page_png).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for pct in range(0, 101, _GRID_STEP):
        y = min(h - 1, int(h * pct / 100))
        labelled = pct % _GRID_LABEL_EVERY == 0
        draw.line([(0, y), (w, y)], fill=(255, 0, 0) if labelled else (255, 170, 170),
                  width=2 if labelled else 1)
        if labelled:
            draw.text((6, min(h - 14, y + 3)), f"{pct}%", fill=(255, 0, 0))
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


def _page_size_px(pdf_path: str, dpi: int) -> Tuple[int, int]:
    """Page size in pixels at `dpi`, from the PDF's own point size."""
    out = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("Page size:"):
            parts = line.split()
            w_pt, h_pt = float(parts[2]), float(parts[4])
            # ceil, to match how pdftoppm itself sizes the raster: truncating
            # loses a pixel and the crop no longer matches the rendered page.
            return math.ceil(w_pt * dpi / 72), math.ceil(h_pt * dpi / 72)
    raise RuntimeError(f"Could not read the page size of {pdf_path}")


def _staff_rows(page: "Image.Image", ink: int = 250, run: float = 0.35) -> List[List[int]]:
    """Rows of each staff in a rendered page, grouped.

    Detecting staves in a *scan* was tried and abandoned — it died at half a degree
    of skew and at 20% ink dropout. None of that applies to a page this program
    rendered itself: the lines are exact, horizontal and unbroken. The result is
    still checked against the number of systems the score declares before it is
    used for anything.
    """
    import numpy as np

    a = np.asarray(page.convert("L"))
    mask = a < ink
    h, w = a.shape
    need = int(w * run)
    cur = np.zeros(h, dtype=np.int32)
    best = np.zeros(h, dtype=np.int32)
    for j in range(w):
        cur = np.where(mask[:, j], cur + 1, 0)
        np.maximum(best, cur, out=best)

    groups: List[List[int]] = []
    for r in np.flatnonzero(best >= need).tolist():
        if groups and r - groups[-1][-1] <= h * 0.012:
            groups[-1].append(r)
        else:
            groups.append([r])
    # A staff is five lines; a stray rule is one.
    return [g for g in groups
            if 1 + sum(1 for a_, b_ in zip(g, g[1:]) if b_ - a_ > 1) >= 4]


def rendered_system_bands(
    pdf_path: str,
    staves_per_system: int,
    out_dir: str,
    dpi: int = 150,
) -> List[SystemBounds]:
    """Where each system sits in a PDF this program rendered.

    Staves are grouped into systems by count — the score says how many staves a
    system has, so no bracket-hunting is needed. Returns [] if the staves do not
    divide evenly, which is the signal that the render did not come out as
    expected and nothing downstream should trust it.
    """
    if staves_per_system < 1:
        return []
    bands: List[SystemBounds] = []
    for page in range(1, page_count(pdf_path) + 1):
        img = Image.open(render_page(pdf_path, page, dpi, out_dir))
        staves = _staff_rows(img)
        if not staves or len(staves) % staves_per_system:
            return []
        h = img.height
        for i in range(0, len(staves), staves_per_system):
            group = staves[i:i + staves_per_system]
            top, bottom = group[0][0], group[-1][-1]
            before = staves[i - 1][-1] if i else None
            after = staves[i + staves_per_system][0] if i + staves_per_system < len(staves) else None
            # Halfway to the neighbouring system, so lyrics under the last staff
            # and anything above the first stay with their own system.
            lo = 0 if before is None else (before + top) // 2
            hi = h if after is None else (bottom + after) // 2
            bands.append(SystemBounds(index=len(bands) + 1, page=page,
                                      top=lo / h, bottom=hi / h))
    return bands


def _file_version(path: str) -> str:
    """What the file holds, cheaply: its size and modification time.

    Content would be exact, but these crops are cut from a render that is itself
    rebuilt whenever the score is newer, so a rewrite always moves the mtime. A
    rebuild that happens to be byte-identical costs one wasted crop, which is the
    right way round to be wrong.
    """
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return f"{st.st_size}:{st.st_mtime_ns}"


def crop_systems(
    pdf_path: str,
    bounds: List[SystemBounds],
    out_dir: str,
    dpi: int = DPI,
) -> List[SystemImage]:
    """Render each band to its own PNG at `dpi`.

    Only the band is rasterised, not the page it sits on. A full A4 at 400 dpi
    takes seconds; a system is a fifth of that, and this is on the path where
    someone clicks a lyric cell and waits to see the music.

    The cache name carries the source as well as the band. The scan never changes,
    but the compare view cuts its bands out of a render of the *cleaned* score,
    which changes whenever the score does -- and a name made of the band alone kept
    serving bar 8 as it looked before a slur was recorded, hours after the slur was
    in the file and in the render. The page said the fix had not applied.
    """
    os.makedirs(out_dir, exist_ok=True)
    width, height = _page_size_px(pdf_path, dpi)
    source = _file_version(pdf_path)
    images = []
    for b in bounds:
        top = max(0, min(height - 1, int(height * b.top)))
        band = max(1, min(height - top, int(height * b.bottom) - top))
        geometry = f"{source}:{b.page}:{b.top:.9f}:{b.bottom:.9f}".encode()
        version = hashlib.sha1(geometry).hexdigest()[:10]
        path = os.path.join(out_dir, f"system-{b.index:02d}@{dpi}-{version}.png")
        if not os.path.exists(path):
            stem = os.path.join(
                out_dir, f".tmp-{os.getpid()}-{threading.get_ident()}-s{b.index}@{dpi}")
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-f", str(b.page), "-l", str(b.page),
                 "-x", "0", "-y", str(top), "-W", str(width), "-H", str(band),
                 "-png", "-singlefile", pdf_path, stem],
                check=True, capture_output=True,
            )
            os.replace(stem + ".png", path)
        images.append(SystemImage(bounds=b, path=path))
    return images


def label(bounds: List[SystemBounds], mscx_path: str) -> List[SystemBounds]:
    """Attach each band's measure range, from a score that still has line breaks.

    Refuses when the counts disagree: a silently wrong alignment would put lyrics
    on the wrong measures, whereas a missing one is visible immediately.
    """
    if not mscx_path or not os.path.exists(mscx_path):
        return bounds
    from lxml import etree
    ranges = per_system.system_ranges(etree.parse(mscx_path).getroot())
    if len(ranges) != len(bounds):
        return bounds
    return [replace(b, measure_start=r.start, measure_end=r.end)
            for b, r in zip(bounds, ranges)]


def save_bounds(song_dir: str, bounds: List[SystemBounds]) -> str:
    path = os.path.join(song_dir, BOUNDS_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"systems": [b.to_dict() for b in bounds]}, f, indent=2)
    return path


def load_bounds(song_dir: str) -> List[SystemBounds]:
    path = os.path.join(song_dir, BOUNDS_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [SystemBounds(**s) for s in data.get("systems", [])]
