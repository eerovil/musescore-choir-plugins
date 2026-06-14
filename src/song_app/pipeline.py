"""Glue between the web app and the existing scripts.

Conversion, cleaning (clean_score), and lyric import (lyric_txt) — driven
non-interactively. No musical logic lives here; this only orchestrates.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from src.clean_score.main import main as clean_main
from src.clean_score.lyric_txt import import_file
from src.clean_score.utils import per_system as ps

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


def cache_key(mscx_path: str) -> str:
    """The .persystem_cache.json key clean_score uses (input basename, no ext)."""
    return os.path.splitext(os.path.basename(mscx_path))[0]


def system_grid(mscx_path: str) -> List[Dict]:
    """Per-system staff layout for the clean-stage grid form.

    Returns one entry per printed system: measure range + each note-bearing staff's
    id, voice count and a short content summary. Pre-fills answers from the cache.
    """
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    score = root if root.tag == "Score" else root.find(".//Score")
    staves = score.findall("Staff")
    systems = ps.find_systems(root)
    cache = ps.load_answer_cache(cache_key(mscx_path)) or {}

    grid: List[Dict] = []
    for sidx, (a, b) in enumerate(systems):
        rows = []
        for staff in staves:
            sid = int(staff.get("id", "0"))
            nv = ps._max_voices_in_range(staff, a, b)
            if nv == 0:
                continue
            rows.append({
                "staff_id": sid,
                "voices": nv,
                "summary": ps._first_nonempty_summary(staff, a, b),
                "answer": cache.get(sidx, {}).get(sid, ""),
            })
        grid.append({
            "system": sidx,
            "measure_start": a + 1,
            "measure_end": b + 1,
            "staves": rows,
        })
    return grid


def save_system_answers(mscx_path: str, answers: Dict[int, Dict[int, str]]) -> None:
    """Persist the grid answers to .persystem_cache.json so clean can run headless."""
    ps.save_answer_cache(cache_key(mscx_path), answers)


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


def render_score_pdf(mscx_path: str) -> str:
    """Render a .mscx to a PDF via the MuseScore CLI (cached; re-renders if stale).

    Returns the rendered PDF path. The render lives next to the score as
    <base>.render.pdf and is regenerated whenever the source score is newer.
    """
    out = os.path.splitext(mscx_path)[0] + ".render.pdf"
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(mscx_path):
        return out
    cli = os.getenv("MUSESCORE_CLI_PATH", "musescore3")
    result = subprocess.run([cli, mscx_path, "-o", out], capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(
            "MuseScore CLI render failed. Check MUSESCORE_CLI_PATH.\n"
            + (result.stderr or result.stdout or "")
        )
    return out


_WARN_RE = re.compile(r"^Warning:\s*(.*)$", re.MULTILINE)


def run_lyric_import(json_path: str, cleaned_path: str, replace: bool = True) -> List[str]:
    """Import lyric JSON in place into the cleaned score; return any warning lines."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        import_file(json_path, cleaned_path, cleaned_path, replace=replace)
    return [m.strip() for m in _WARN_RE.findall(buf.getvalue())]
