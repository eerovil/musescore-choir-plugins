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
import json
import os
import subprocess
from dataclasses import dataclass, asdict, replace
from typing import Dict, List, Optional

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
    """Rasterise one page, cached by (page, dpi) under out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.join(out_dir, f"page-{page:02d}@{dpi}")
    out = stem + ".png"
    if not os.path.exists(out):
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page),
             "-png", "-singlefile", pdf_path, stem],
            check=True, capture_output=True,
        )
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
    img.save(out)
    return out


def crop_systems(
    pdf_path: str,
    bounds: List[SystemBounds],
    out_dir: str,
    dpi: int = DPI,
) -> List[SystemImage]:
    """Render each band to its own PNG at `dpi`."""
    os.makedirs(out_dir, exist_ok=True)
    images = []
    for b in bounds:
        page = Image.open(render_page(pdf_path, b.page, dpi, out_dir))
        h = page.height
        box = (0, max(0, int(h * b.top)), page.width, min(h, int(h * b.bottom)))
        path = os.path.join(out_dir, f"system-{b.index:02d}.png")
        page.crop(box).save(path)
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
