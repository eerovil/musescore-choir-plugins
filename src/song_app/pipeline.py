"""Glue between the web app and the existing scripts.

Conversion, cleaning (clean_score), and lyric import (lyric_txt) — driven
non-interactively. No musical logic lives here; this only orchestrates.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from . import pdf_systems
from src.clean_score.main import main as clean_main
from src.clean_score import lyric_txt
from src.clean_score.lyric_txt import LyricImport, import_file
from src.clean_score.utils import per_system

MUSESCORE_EXTS = (".mscz", ".mscx", ".musicxml", ".xml")
Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def convert_to_mscx(input_path: str, out_dir: str, log: Logger = _noop) -> str:
    """Return a .mscx path for input_path inside out_dir, converting if needed.

    .mscx -> used as-is; .mscz -> unzipped; .musicxml/.xml -> MuseScore CLI.
    """
    lower = input_path.lower()
    if lower.endswith(".mscx"):
        return input_path

    base = os.path.splitext(os.path.basename(input_path))[0]
    target = os.path.join(out_dir, base + ".mscx")

    # Reuse a previous conversion if it's still newer than the source.
    if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(input_path):
        return target

    if lower.endswith(".mscz"):
        log(f"Unzipping {os.path.basename(input_path)}")
        tmp = os.path.join(out_dir, "_temp_extracted")
        os.makedirs(tmp, exist_ok=True)
        try:
            with zipfile.ZipFile(input_path, "r") as zf:
                zf.extractall(tmp)
            inner = next((os.path.join(tmp, e) for e in os.listdir(tmp)
                          if e.lower().endswith(".mscx")), None)
            if not inner:
                raise RuntimeError("No .mscx found inside the .mscz archive.")
            shutil.copy2(inner, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return target

    # MusicXML -> MuseScore CLI
    log(f"Converting {os.path.basename(input_path)} with MuseScore CLI")
    cli = os.getenv("MUSESCORE_CLI_PATH", "musescore3")
    result = subprocess.run(
        [cli, input_path, "-o", target], capture_output=True, text=True
    )
    if result.returncode != 0 or not os.path.exists(target):
        raise RuntimeError(
            "MuseScore CLI conversion failed. Check MUSESCORE_CLI_PATH.\n"
            + (result.stderr or result.stdout or "")
        )
    return target


def system_grid(mscx_path: str) -> List[Dict]:
    """Per-system staff layout for the clean-stage grid form.

    Returns one entry per printed system: measure range + each note-bearing staff's
    id, voice count and a short content summary, prefilled with any saved answer.
    """
    return [layout.to_dict() for layout in per_system.layout_for_file(mscx_path)]


def save_system_answers(mscx_path: str, answers: Dict[int, Dict[int, str]]) -> None:
    """Record the grid answers for this score so cleaning can run headless."""
    per_system.save_answers(mscx_path, answers)


def has_system_answers(input_path: str) -> bool:
    """True if this score has a recorded per-system answer set (so it is per-system)."""
    return per_system.has_answers(input_path)


def system_ranges(root: etree._Element) -> List[per_system.SystemRange]:
    """The score's printed systems as 1-based measure spans (for the lyric grid)."""
    return per_system.system_ranges(root)


def run_clean(
    input_path: str,
    out_dir: str,
    per_system: bool,
    add_staffs: Optional[str] = None,
    log: Logger = _noop,
) -> Tuple[str, str]:
    """Convert + clean. Returns (cleaned_path, mscx_intermediate_path).

    Runs non-interactively: per-system reads .persystem_cache.json; normal mode
    reduces >2-voice measures automatically (the health check flags them).
    """
    mscx_path = convert_to_mscx(input_path, out_dir, log)
    base = os.path.splitext(os.path.basename(mscx_path))[0]
    cleaned = os.path.join(out_dir, base + "_cleaned.mscx")
    log("Cleaning score" + (" (per-system)" if per_system else ""))
    clean_main(
        mscx_path, cleaned,
        add_staffs=add_staffs or "",
        interactive=False,
        per_system=per_system,
    )
    if not os.path.exists(cleaned):
        raise RuntimeError("Cleaning produced no output (no parts declared?).")
    log("Cleaned score written.")
    return cleaned, mscx_path


def strip_lyrics_copy(mscx_path: str) -> str:
    """Write a copy of the score with all lyrics removed (cached by mtime).

    Lets us show the cleaned structure without lyrics regardless of what's been
    imported, so it never goes stale relative to the live cleaned file.
    """
    out = os.path.splitext(mscx_path)[0] + ".nolyrics.mscx"
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(mscx_path):
        return out
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    for lyr in root.findall(".//Lyrics"):
        parent = lyr.getparent()
        if parent is not None:
            parent.remove(lyr)
    with open(out, "wb") as f:
        f.write(etree.tostring(root, pretty_print=True, encoding="UTF-8"))
    return out


# Shrink the staff for the rendered previews so the score's own system breaks fit on
# the page (otherwise MuseScore adds extra breaks). Tunable via .env.
SPATIUM_SCALE = float(os.getenv("RENDER_SPATIUM_SCALE", "0.65"))


def _scaled_staff_mscx(mscx_path: str) -> Optional[str]:
    """Write a temp copy of the score with the staff size reduced by SPATIUM_SCALE.

    Returns the temp path, or None if no scaling is needed/possible (caller then
    renders the original). Caller must delete the temp file.
    """
    if SPATIUM_SCALE >= 1.0:
        return None
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    score = root if root.tag == "Score" else root.find(".//Score")
    style = score.find("Style") if score is not None else None
    if style is None:
        return None
    sp = style.find("Spatium")
    if sp is None:
        sp = etree.SubElement(style, "Spatium")
        base = 1.74978  # MuseScore 3 default
    else:
        try:
            base = float(sp.text)
        except (TypeError, ValueError):
            base = 1.74978
    sp.text = f"{base * SPATIUM_SCALE:.5f}"
    fd, tmp = tempfile.mkstemp(suffix=".mscx")
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(etree.tostring(root, encoding="UTF-8"))
    return tmp


def _page_cache(song_dir: str) -> str:
    return os.path.join(song_dir, ".pages")


def system_bounds(song_dir: str) -> List[Dict]:
    """The stored printed-system boundaries, as plain dicts for the wire."""
    return [b.to_dict() for b in pdf_systems.load_bounds(song_dir)]


def save_system_bounds(song_dir: str, bands: List[Dict], mscx_path: str = "") -> List[Dict]:
    """Store boundaries, re-indexed in page order and labelled where possible.

    `bands` are {page, top, bottom} as fractions of page height -- whatever the
    editor currently shows. Indices and measure ranges are derived here rather
    than trusted from the browser, so a drag can never invent an alignment.
    """
    ordered = sorted(bands, key=lambda b: (int(b["page"]), float(b["top"])))
    bounds = [
        pdf_systems.SystemBounds(
            index=i, page=int(b["page"]),
            top=max(0.0, min(1.0, float(b["top"]))),
            bottom=max(0.0, min(1.0, float(b["bottom"]))),
        )
        for i, b in enumerate(ordered, 1)
    ]
    if mscx_path:
        bounds = pdf_systems.label(bounds, mscx_path)
    pdf_systems.save_bounds(song_dir, bounds)
    return [b.to_dict() for b in bounds]


def declared_system_count(mscx_path: str) -> int:
    """How many printed systems the score itself declares (0 if unknown)."""
    if not mscx_path or not os.path.exists(mscx_path):
        return 0
    try:
        return len(per_system.system_ranges(etree.parse(mscx_path).getroot()))
    except Exception:
        return 0


def page_image(song_dir: str, pdf_path: str, page: int, dpi: int, grid: bool = False) -> str:
    """One rasterised page, cached under the song folder."""
    out = _page_cache(song_dir)
    raw = pdf_systems.render_page(pdf_path, page, dpi, out)
    return pdf_systems._with_grid(raw) if grid else raw


def page_count(pdf_path: str) -> int:
    return pdf_systems.page_count(pdf_path)


def system_crop(song_dir: str, pdf_path: str, index: int, dpi: int) -> str:
    """One printed system, cropped from the stored bounds."""
    bounds = pdf_systems.load_bounds(song_dir)
    match = [b for b in bounds if b.index == index]
    if not match:
        raise ValueError(f"No stored bounds for system {index}")
    images = pdf_systems.crop_systems(pdf_path, match, _page_cache(song_dir), dpi=dpi)
    return images[0].path


def render_score_pdf(mscx_path: str) -> str:
    """Render a .mscx to a PDF via the MuseScore CLI (cached; re-renders if stale).

    Returns the rendered PDF path. The render lives next to the score as
    <base>.render.pdf and is regenerated whenever the source score is newer. The
    staff is shrunk (SPATIUM_SCALE) so the score's own system breaks fit the page.
    """
    out = os.path.splitext(mscx_path)[0] + ".render.pdf"
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(mscx_path):
        return out
    cli = os.getenv("MUSESCORE_CLI_PATH", "musescore3")
    src = _scaled_staff_mscx(mscx_path) or mscx_path
    try:
        result = subprocess.run([cli, src, "-o", out], capture_output=True, text=True)
    finally:
        if src != mscx_path and os.path.exists(src):
            os.remove(src)
    if result.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(
            "MuseScore CLI render failed. Check MUSESCORE_CLI_PATH.\n"
            + (result.stderr or result.stdout or "")
        )
    return out


def _printed_systems(song_dir: str) -> Optional[List[Tuple[int, int]]]:
    """Measure ranges of the printed systems, from the bounds read off the scan.

    Normal-mode cleaning strips layout breaks, so the cleaned score has no systems
    left to find and the lyric editor would offer one cell per part for the whole
    piece. The printed systems still exist on the page; these are them.
    """
    if not song_dir:
        return None
    bounds = [b for b in pdf_systems.load_bounds(song_dir) if b.measure_start]
    if not bounds:
        return None
    return [(b.measure_start, b.measure_end) for b in bounds]


def lyric_grid(mscx_path: str, song_dir: str = "") -> Dict:
    """The manual editor's projection of a score: parts x printed systems, prefilled."""
    root = etree.parse(mscx_path).getroot()
    return lyric_txt.editor_grid(root, systems=_printed_systems(song_dir)).to_dict()


def lyric_blocks(mscx_path: str, cells: Dict, song_dir: str = "") -> List[Dict]:
    """The editor's typed cells as lyric JSON blocks, addressed by part name."""
    root = etree.parse(mscx_path).getroot()
    grid = lyric_txt.editor_grid(root, systems=_printed_systems(song_dir))
    return lyric_txt.blocks_from_cells(grid, cells)


def run_lyric_import(
    json_path: str, cleaned_path: str, replace: bool = True
) -> LyricImport:
    """Import lyric JSON in place into the cleaned score; return the placement result."""
    return import_file(json_path, cleaned_path, cleaned_path, replace=replace)
