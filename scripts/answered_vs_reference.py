#!/usr/bin/env python3
"""Score an assembled page again, this time with the per-system grid answered.

`omr_systems.assemble` writes `scanned.musicxml`, which is a **positional**
document: a short system's staves go in the top columns and the rest rest, and
which part each column holds is deliberately not decided (see that module).  So
scoring that file against a reference whose rows are parts measures the missing
operator answer as if it were lost music -- which is what issue #195 was opened
to separate.

This runs the rest of the way the app runs: the assembled page is converted,
cleaned in per-system mode with the grid answered, and imploded back to the
page's printed shape.  That is the pipeline the reference itself came out of, so
the two sides are comparable.

    .venv/bin/python scripts/answered_vs_reference.py                 # every frozen page
    .venv/bin/python scripts/answered_vs_reference.py kaksi-laulua-krapulasta-2-p3

**Run the uniform pages too.**  A page whose systems all print the same number of
staves has nothing for the grid to fix, so it measures what the round trip costs
on its own; without that control an improvement on the varying pages says
nothing.  Measured for #195 it is 1.0 point over 10 pages.

**The grid answer is frozen, not read live.**  It comes from
`fixtures/answered-pages.json` (`scripts/answered_pages.py`), which records each
band's index, its printed measure range and the grouping the grid was answered
with.  A page that has not been frozen is an error and never a fall back to the
song; a song that has moved under a frozen page stops the run naming the band.
That matters because the bands and the reviewed cleaned score are host state and
have moved under earlier measurements on this map already -- see #195.

    .venv/bin/python scripts/answered_vs_reference.py --record        # write the manifest

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

from scripts import answered_pages  # noqa: E402

DEFAULT_STORE = Path.home() / ".local/share/musescore-choir-plugins/issue-192"
DEFAULT_SCORER = Path.home() / "homr/.worktrees/issue-195-scorer"


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)


def read_live(slug, page, parts):
    """What the song on this host says about a page, or `None` if it has none.

    Only ever used to *check* a frozen entry, or to record one in the first
    place. Nothing scored is taken from here.
    """
    try:
        bands = sorted(
            (b for b in parts["pdf_systems"].load_bounds(f"songs/{slug}")
             if b.page == page),
            key=lambda b: b.index,
        )
        cleaned = parts["reference_files"](slug).cleaned
    except Exception:                                       # noqa: BLE001
        return None
    if not bands:
        return None
    found = []
    for band in bands:
        grouping = parts["band_grouping"](
            slug, etree.parse(str(cleaned)).getroot(), band.measure_start
        )
        if grouping is None:
            grouping = [
                printed.names
                for printed in parts["implode_grouping"](
                    etree.parse(str(cleaned)).getroot(), None
                ).printed
            ]
        found.append(answered_pages.FrozenBand(
            index=band.index,
            measure_start=band.measure_start or 0,
            measure_end=band.measure_end or 0,
            grouping=[list(staff) for staff in grouping],
        ))
    return answered_pages.FrozenPage(
        song=slug, page=page, bands=found,
        bounds_sha256=answered_pages.digest(Path(f"songs/{slug}/.systems.json")),
        cleaned_sha256=answered_pages.digest(Path(cleaned)),
    )


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


def grid_answers(frozen, layouts, cleared):
    """The per-system grid, filled in from the frozen answer and nothing else.

    This is where the measurement's one judgement enters, so it is a function of
    the frozen page and the file's own layout -- there is no argument it could
    take the live song through.
    """
    per_band = frozen.groupings
    if len(layouts) != len(per_band):
        raise RuntimeError(
            f"{len(layouts)} systems in the file against {len(per_band)} frozen bands"
        )
    answers = {}
    for layout, grouping in zip(layouts, per_band):
        answers[layout.index] = {
            row.staff_id: (",".join(grouping[n]) if n < len(grouping) else cleared)
            for n, row in enumerate(layout.staves)
        }
    return answers


def answer(frozen, assembled, work, parts, cli):
    """Clean the assembled page with the frozen grid answer, imploded to the page."""
    widest = max(frozen.groupings, key=len)

    work.mkdir(parents=True, exist_ok=True)
    source = work / f"{frozen.name}.musicxml"
    source.write_bytes(assembled.read_bytes())
    mscx = parts["pipeline"].convert_to_mscx(str(source), str(work))

    per_system = parts["per_system"]
    answers = grid_answers(
        frozen, per_system.layout_for_file(mscx), per_system.CLEARED
    )

    cleaned = work / f"{frozen.name}_cleaned.mscx"
    with per_system.use_answer_file(str(work / "answers.json")):
        per_system.save_answers(mscx, answers)
        parts["clean_main"](mscx, str(cleaned), interactive=False, per_system=True)
    if not cleaned.exists():
        raise RuntimeError("cleaning produced nothing")

    root = etree.parse(str(cleaned)).getroot()
    parts["implode"](root, widest, parts["drop_rests_for"](frozen.song))
    imploded = work / f"{frozen.name}_imploded.mscx"
    etree.ElementTree(root).write(str(imploded), encoding="UTF-8",
                                 xml_declaration=True)
    exported = work / f"{frozen.name}_answered.musicxml"
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


def wanted(store: Path, chosen) -> list:
    """The pages to work on, in the store's own order."""
    found = []
    for line in (store / "results.jsonl").read_text().splitlines():
        done = json.loads(line)
        name = f"{done['song']}-p{done['page']}"
        if chosen and name not in chosen:
            continue
        if any(name == f"{s}-p{p}" for s, p in found):
            continue
        found.append((done["song"], done["page"]))
    return found


def record(store, chosen, manifest, parts) -> None:
    """Write down the grid answers the songs on this host give today."""
    held = answered_pages.load(manifest)
    for slug, page in wanted(store, chosen):
        live = read_live(slug, page, parts)
        if live is None:
            log(f"{slug}-p{page}: no song on this host to record from; left alone")
            continue
        was = held.get((slug, page))
        if was is None:
            log(f"{slug}-p{page}: recorded, {len(live.bands)} bands")
        else:
            moved = answered_pages.compare(was, live)
            log(f"{slug}-p{page}: re-recorded" if moved else
                f"{slug}-p{page}: unchanged")
            for line in moved:
                log(f"    was -> now: {line}")
        held[(slug, page)] = live
    answered_pages.save(list(held.values()), manifest)
    log(f"wrote {manifest}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", nargs="*", help="e.g. kaksi-laulua-krapulasta-2-p3")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE,
                        help="where the cached parses and references live")
    parser.add_argument("--scorer", type=Path, default=DEFAULT_SCORER,
                        help="a homr checkout to take `fixturecheck.compare` from")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="the frozen grid answers "
                             f"(default {answered_pages.MANIFEST})")
    parser.add_argument("--record", action="store_true",
                        help="write the manifest from the songs on this host, "
                             "instead of scoring")
    args = parser.parse_args()

    os.chdir(ROOT)
    manifest = args.manifest or answered_pages.MANIFEST

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
                 pipeline=pipeline, pdf_systems=pdf_systems)

    if args.record:
        record(args.store, args.pages, manifest, parts)
        return

    sys.path.insert(0, str(args.scorer))
    from fixturecheck.compare import compare_output

    cli = os.environ.get("MUSESCORE_CLI_PATH", "musescore3")
    pooled = {}
    with tempfile.TemporaryDirectory(prefix="answered-") as scratch:
        for slug, page in wanted(args.store, args.pages):
            name = f"{slug}-p{page}"
            reference = args.store / "refs" / f"{name}.musicxml"
            assembled = args.store / "parses" / f"assembled-{name}.musicxml"
            if not reference.exists() or not assembled.exists():
                log(f"{name}: nothing cached for it; skipped")
                continue
            # Frozen first, and the song only as a check on it. A page nobody
            # wrote down, or one the song has moved under, stops the run rather
            # than being scored against whatever this host holds today.
            try:
                frozen = answered_pages.frozen_page(slug, page, manifest)
                answered_pages.check(frozen, read_live(slug, page, parts))
            except (answered_pages.NotFrozen, answered_pages.Drifted) as refused:
                # Loudly and with a non-zero exit, because the alternative is a
                # run that keeps printing a number for a question that moved.
                raise SystemExit(str(refused))
            before = row(compare_output(reference, assembled))
            try:
                made, rows = answer(frozen, assembled, Path(scratch) / name,
                                    parts, cli)
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
