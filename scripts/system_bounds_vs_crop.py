#!/usr/bin/env python3
"""The app's own crop against homr's ``--system-bounds``, on the same systems (#223).

Both routes cut the same padded band out of the same page at the same dpi with
the same pixel rounding; the only thing that differs is what draws the PDF --
poppler (``pdftoppm``) for the app, pypdfium for homr. So whatever differs below
is the rasteriser, read through one homr.

    .venv/bin/python scripts/system_bounds_vs_crop.py --homr <venv>/bin/homr --out /tmp/x

Systems: the Virta fixture's 15 (hand-drawn bands) and the benchmark's B1a/B1b
(3 each, bounds from ``fixtures/omr-benchmark/pages.json``). B1a and B1b are also
scored note by note against the hand transcription, bars 11-17: a note is right
when a note starts on that beat of that bar on that staff. Onsets only -- the
transcription is rhythm and counts, every notehead written as a C.

Run it under a heavy slot (it is ~40 homr reads); it takes one itself unless
``--no-queue``. Reads already on disk under ``--out`` are reused.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.flatten_vs_reference import (  # noqa: E402
    as_homr_wrote_it, score)
from src.song_app import heavy_slot, pdf_systems, scan  # noqa: E402
from src.song_app.pdf_systems import SystemBounds  # noqa: E402
from src.song_app.tests import benchmark  # noqa: E402

DPI = 200
PAD = 0.02
ROOT = Path(__file__).resolve().parent.parent
VIRTA_PDF = ROOT / "fixtures/virta-venhetta-vie/00-registered/Virta venhettä vie.pdf"
VIRTA_BOUNDS = ROOT / "fixtures/virta-venhetta-vie/10-cleaned/.systems.json"
TRANSCRIPTION = ROOT / "fixtures/omr-benchmark/B1-heraa-suomi-hand-transcription.mscx"
HOMR_ARGS = ["--gpu", "no", "--no-title"]


def pages() -> dict:
    """``{page id: (pdf, [SystemBounds])}`` with indices from 1 in score order."""
    out = {}
    bounds = json.loads(VIRTA_BOUNDS.read_text())["systems"]
    out["virta"] = (VIRTA_PDF, [
        SystemBounds(index=i, page=b["page"], top=b["top"], bottom=b["bottom"],
                     measure_start=b.get("measure_start", 0),
                     measure_end=b.get("measure_end", 0))
        for i, b in enumerate(bounds, start=1)])
    for page_id in ("B1a", "B1b"):
        page = benchmark.page(page_id)
        out[page_id] = (Path(page.pdf), list(page.systems))
    return out


def run(argv, log) -> None:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=3600)
    log.write(f"$ {' '.join(map(str, argv))}\n{proc.stdout}{proc.stderr}\n")
    if proc.returncode:
        raise SystemExit(f"homr failed ({proc.returncode}); see the log")


def app_route(homr: str, pdf: Path, bands, work: Path, log) -> dict:
    """The app's crop (poppler), one image per system, read one at a time."""
    images = work / "crops"
    done = {b.index: images / f"system-{b.index:03d}.musicxml" for b in bands}
    if all(p.exists() for p in done.values()):
        return done
    images.mkdir(parents=True, exist_ok=True)
    for band in bands:
        image = pdf_systems.crop_systems(str(pdf), [scan.padded(band, PAD)],
                                         str(images), dpi=DPI)[0].path
        os.replace(image, images / f"system-{band.index:03d}.png")
    # A directory is read file by file; a list of files would be joined into one page.
    run([homr, str(images), *HOMR_ARGS], log)
    return {b.index: images / f"system-{b.index:03d}.musicxml" for b in bands}


def homr_route(homr: str, pdf: Path, bands, work: Path, log) -> dict:
    """homr's own crop (pypdfium) from the same bands, all in one run."""
    done = {b.index: work / f"score_system-{b.index:03d}.musicxml" for b in bands}
    if all(p.exists() for p in done.values()):
        return done
    work.mkdir(parents=True, exist_ok=True)
    local = work / "score.pdf"
    shutil.copyfile(pdf, local)
    bounds = work / "bounds.json"
    bounds.write_text(json.dumps({"systems": [
        {"index": b.index, "page": b.page, "top": b.top, "bottom": b.bottom}
        for b in bands]}))
    run([homr, str(local), *HOMR_ARGS, "--system-bounds", str(bounds),
         "--system-dpi", str(DPI), "--system-pad", str(PAD)], log)
    return {b.index: work / f"score_system-{b.index:03d}.musicxml" for b in bands}


def shape(path: Path) -> dict:
    """What can be counted off one parse without a reference."""
    root = etree.parse(str(path)).getroot()
    staves = as_homr_wrote_it(str(path))
    notes = [n for n in root.iter("note")
             if n.find("rest") is None and n.find("grace") is None]
    return {
        "staves": len(staves),
        "bars": max((len(s) for s in staves), default=0),
        "notes": len(notes),
        "slurs": sum(1 for s in root.iter("slur") if s.get("type") == "start"),
    }


def body(path: Path) -> bytes:
    """The parse with homr's per-note image positions taken out."""
    from src.song_app.omr import strip_image_positions, strip_provenance
    return strip_image_positions(strip_provenance(path.read_bytes()))


def truth(cli: str, work: Path) -> list:
    out = work / "transcription.musicxml"
    if not out.exists():
        subprocess.run([cli, "-o", str(out), str(TRANSCRIPTION)], check=True,
                       capture_output=True, timeout=300)
    return as_homr_wrote_it(str(out))  # one part of two staves: split per staff


def rhythm(staves: list) -> list:
    """Every note's pitch forgotten: the transcription is rhythm only (it writes
    every notehead as a C), so a note is right when one starts on that beat."""
    return [{bar: [(beat, 0) for beat, _p in notes] for bar, notes in staff.items()}
            for staff in staves]


def against_truth(want: list, fragments: dict, bands) -> Counter:
    """Every system of one page against the transcription, onsets only."""
    tally = Counter()
    want = rhythm(want)
    for band in bands:
        got = rhythm(as_homr_wrote_it(str(fragments[band.index])))
        bars = band.measure_end - band.measure_start + 1
        tally.update(score(want, got, band.measure_start, bars))
        tally["read"] += sum(len(got[c].get(n + 1, []))
                             for c in range(len(got)) for n in range(bars))
    return tally


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--homr", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-queue", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "homr.log", "a")

    def measure():
        results = {}
        for page_id, (pdf, bands) in pages().items():
            results[page_id] = (bands,
                                app_route(args.homr, pdf, bands, out / page_id / "app", log),
                                homr_route(args.homr, pdf, bands, out / page_id / "homr", log))
        return results

    if args.no_queue:
        results = measure()
    else:
        with heavy_slot.heavy_slot("#223 system-bounds vs crop", log=print):
            results = measure()

    print("page sys | app: staves bars notes slurs | homr: staves bars notes slurs | same")
    same = 0
    total = 0
    for page_id, (bands, app, homr) in results.items():
        for band in bands:
            a, h = shape(app[band.index]), shape(homr[band.index])
            identical = body(app[band.index]) == body(homr[band.index])
            same += identical
            total += 1
            print(f"{page_id:5} {band.index:3} | {a['staves']} {a['bars']:2} {a['notes']:3} "
                  f"{a['slurs']:2} | {h['staves']} {h['bars']:2} {h['notes']:3} "
                  f"{h['slurs']:2} | {'yes' if identical else 'no'}")
    print(f"identical parses: {same}/{total}")

    cli = os.getenv("MUSESCORE_CLI_PATH")
    if not cli:
        print("MUSESCORE_CLI_PATH unset: no note-level score")
        return
    want = truth(cli, out)
    print("\npage route | onsets right / truth (recall) | read (precision) | missing")
    for page_id in ("B1a", "B1b"):
        bands, app, homr = results[page_id]
        for name, fragments in (("app", app), ("homr", homr)):
            t = against_truth(want, fragments, bands)
            print(f"{page_id:4} {name:4} | {t['right']:3} / {t['notes']:3} "
                  f"({t['right'] / t['notes']:.1%}) | {t['read']:3} "
                  f"({t['right'] / max(1, t['read']):.1%}) | {t['missing'] + t['off_beat']}")


if __name__ == "__main__":
    main()
