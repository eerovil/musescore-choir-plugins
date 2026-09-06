#!/usr/bin/env python3
"""Score an assembled page again, this time with the per-system grid answered.

`omr_systems.assemble` writes `scanned.musicxml`, which is a **positional**
document: a short system's staves go in the top columns and the rest rest, and
which part each column holds is deliberately not decided (see that module).  So
scoring that file against a reference whose rows are parts measures the missing
operator answer as if it were lost music -- which is what issue #195 was opened
to separate.

This runs the rest of the way the app runs: the assembled page is converted,
cleaned in per-system mode with the grid answered from the reviewed score's own
per-band grouping, and imploded back to the page's printed shape.  That is the
pipeline the reference itself came out of, so the two sides are comparable.

    .venv/bin/python scripts/answered_vs_reference.py                 # every page
    .venv/bin/python scripts/answered_vs_reference.py kaksi-laulua-krapulasta-2-p3

**Run the uniform pages too.**  A page whose systems all print the same number of
staves has nothing for the grid to fix, so it measures what the round trip costs
on its own; without that control an improvement on the varying pages says
nothing.  Measured for #195 it is 1.0 point over 10 pages.

Nothing here reads a page: it re-scores parses somebody else already made.  The
cached band parses, page references and assembled pages come from #192's store
(`--store`), and the scorer is the fork's own `compare_output` from a homr
worktree (`--scorer`) -- the same two pieces #192 used, so the numbers stay
comparable with the ones it published.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_STORE = Path.home() / ".local/share/musescore-choir-plugins/issue-192"
DEFAULT_SCORER = Path.home() / "homr/.worktrees/issue-195-scorer"


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def groupings(slug: str, bands, band_grouping, implode_grouping, reference_files):
    """What each band's system prints, in the words the reference is built from.

    A song with nothing recorded per system falls back to the grouping `implode`
    infers from the part names -- which is what the reference fell back to as
    well, so the two sides still agree about what a row is.
    """
    cleaned = reference_files(slug).cleaned
    found = [
        band_grouping(slug, etree.parse(str(cleaned)).getroot(), band.measure_start)
        for band in bands
    ]
    if any(g is None for g in found):
        inferred = [
            printed.names
            for printed in implode_grouping(
                etree.parse(str(cleaned)).getroot(), None
            ).printed
        ]
        found = [g if g is not None else inferred for g in found]
    return found


def match_octave_notation(source: Path, made: Path) -> None:
    """Undo an octave the clean added in notation only.

    Cleaning marks a men's-choir treble staff `G8vb` (`clef-octave-change=-1`).
    The scorer reads a note as a staff position, so a clef saying "sounds an
    octave lower" with the pitches left where they were puts every note of that
    staff seven positions out against a reference written the other way round.
    That is a difference of notation between two files rather than of music, and
    it has nothing to do with which row a staff landed on, so it is normalised
    away here instead of being measured.
    """
    was = {}
    for part in etree.parse(str(source)).getroot().findall("part"):
        clef = part.find("measure/attributes/clef")
        was[part.get("id")] = int(
            (clef.findtext("clef-octave-change") or 0) if clef is not None else 0
        )
    tree = etree.parse(str(made))
    moved = False
    for part in tree.getroot().findall("part"):
        clef = part.find("measure/attributes/clef")
        now = int(
            (clef.findtext("clef-octave-change") or 0) if clef is not None else 0
        )
        shift = now - was.get(part.get("id"), 0)
        if not shift:
            continue
        moved = True
        for octave in part.iter("octave"):
            octave.text = str(int(octave.text) + shift)
    if moved:
        tree.write(str(made), xml_declaration=True, encoding="UTF-8")


def answer(slug, page, bands, assembled, work, parts, cli):
    """Clean the assembled page with the grid answered, imploded to the page shape."""
    per_band = groupings(slug, bands, parts["band_grouping"],
                         parts["implode_grouping"], parts["reference_files"])
    widest = max(per_band, key=len)

    work.mkdir(parents=True, exist_ok=True)
    source = work / f"{slug}-p{page}.musicxml"
    source.write_bytes(assembled.read_bytes())
    mscx = parts["pipeline"].convert_to_mscx(str(source), str(work))

    per_system = parts["per_system"]
    layouts = per_system.layout_for_file(mscx)
    if len(layouts) != len(bands):
        raise RuntimeError(
            f"{len(layouts)} systems in the file against {len(bands)} bands"
        )
    answers = {}
    for layout, grouping in zip(layouts, per_band):
        answers[layout.index] = {
            row.staff_id: (",".join(grouping[n]) if n < len(grouping)
                           else per_system.CLEARED)
            for n, row in enumerate(layout.staves)
        }

    cleaned = work / f"{slug}-p{page}_cleaned.mscx"
    with per_system.use_answer_file(str(work / "answers.json")):
        per_system.save_answers(mscx, answers)
        parts["clean_main"](mscx, str(cleaned), interactive=False, per_system=True)
    if not cleaned.exists():
        raise RuntimeError("cleaning produced nothing")

    root = etree.parse(str(cleaned)).getroot()
    parts["implode"](root, widest, parts["drop_rests_for"](slug))
    imploded = work / f"{slug}-p{page}_imploded.mscx"
    etree.ElementTree(root).write(str(imploded), encoding="UTF-8",
                                 xml_declaration=True)
    exported = work / f"{slug}-p{page}_answered.musicxml"
    made = subprocess.run([cli, str(imploded), "-o", str(exported)],
                          capture_output=True, text=True, timeout=600)
    if made.returncode != 0 or not exported.exists():
        raise RuntimeError(f"MuseScore refused the export: {made.stderr[-200:]}")
    match_octave_notation(assembled, exported)
    return exported, ",".join("+".join(g) for g in widest)


def row(result) -> dict:
    return dict(score=round(result.score, 1), scored=result.scored,
                agree=result.agree, voice=result.voice, pitch=result.pitch,
                size=result.size, timing=result.timing, meter=result.meter)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", nargs="*", help="e.g. kaksi-laulua-krapulasta-2-p3")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE,
                        help="where the cached parses and references live")
    parser.add_argument("--scorer", type=Path, default=DEFAULT_SCORER,
                        help="a homr checkout to take `fixturecheck.compare` from")
    args = parser.parse_args()

    sys.path.insert(0, str(args.scorer))
    from fixturecheck.compare import compare_output

    os.chdir(ROOT)
    from scripts.implode_report import drop_rests_for
    from scripts.make_stem_fixture import band_grouping
    from scripts.reference_manifest import reference_files
    from src.clean_score.implode import grouping as implode_grouping
    from src.clean_score.implode import implode
    from src.clean_score.main import main as clean_main
    from src.clean_score.utils import per_system
    from src.song_app import pdf_systems, pipeline

    parts = dict(band_grouping=band_grouping, implode_grouping=implode_grouping,
                 reference_files=reference_files, drop_rests_for=drop_rests_for,
                 implode=implode, clean_main=clean_main, per_system=per_system,
                 pipeline=pipeline)
    cli = os.environ.get("MUSESCORE_CLI_PATH", "musescore3")

    pooled = {}
    with tempfile.TemporaryDirectory(prefix="answered-") as scratch:
        for line in (args.store / "results.jsonl").read_text().splitlines():
            done = json.loads(line)
            slug, page = done["song"], done["page"]
            name = f"{slug}-p{page}"
            if args.pages and name not in args.pages:
                continue
            reference = args.store / "refs" / f"{name}.musicxml"
            assembled = args.store / "parses" / f"assembled-{name}.musicxml"
            if not reference.exists() or not assembled.exists():
                log(f"{name}: nothing cached for it; skipped")
                continue
            bands = sorted(
                (b for b in pdf_systems.load_bounds(f"songs/{slug}") if b.page == page),
                key=lambda b: b.index,
            )
            before = row(compare_output(reference, assembled))
            try:
                made, rows = answer(slug, page, bands, assembled,
                                    Path(scratch) / name, parts, cli)
            except Exception as failure:                        # noqa: BLE001
                log(f"{name}: could not answer it — {failure}")
                continue
            after = row(compare_output(reference, made))
            log(f"{name}: assembled {before['score']}% (size {before['size']}) "
                f"-> answered {after['score']}% (size {after['size']}); rows {rows}")
            for side, got in (("assembled", before), ("answered", after)):
                held = pooled.setdefault(side, dict(agree=0, scored=0))
                held["agree"] += got["agree"]
                held["scored"] += got["scored"]

    for side, held in pooled.items():
        if held["scored"]:
            log(f"{side}: {100.0 * held['agree'] / held['scored']:.1f}% "
                f"over {held['scored']} notes")


if __name__ == "__main__":
    main()
