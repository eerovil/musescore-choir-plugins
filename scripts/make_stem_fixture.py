#!/usr/bin/env python3
"""Cut one printed system out of a song and write it as a homr stem fixture.

A fixture is a picture of one printed system and a reference MusicXML saying
what that system actually holds, note by note, with MuseScore's own engraving
coordinates -- that is what lets the fixture matcher line a detected notehead up
with a printed one without rendering anything.

Both halves come from work already done here: the picture is the band a person
drew in the Systems editor, cropped straight off the PDF, and the reference is
the cleaned score imploded back to the shape of the print, trimmed to that band's
bars.  So the fixture says what the page says, not what any parse said.

    .venv/bin/python scripts/make_stem_fixture.py laulun-aika-3 2 --name laulun-aika-s2

Writes `<HOMR_FIXTURES>/<name>.png` and `.musicxml` (default: the system-4
worktree's `fixtures/`), and prints the entry to add to
`stem-direction-fixtures.json`.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.implode_report import drop_rests_for, override_for  # noqa: E402
from scripts.reference_manifest import reference_files  # noqa: E402
from src.clean_score.implode import implode  # noqa: E402
from src.song_app import pdf_systems  # noqa: E402

FIXTURES = Path(os.environ.get("HOMR_FIXTURES",
                               "/var/home/eero/homr-trees/system-4/fixtures"))
#: What the fixture pictures are cropped at. The existing ones are a few hundred
#: pixels tall, and homr's staff detection wants the staff lines resolved rather
#: than the page legible.
FIXTURE_DPI = 200

#: Carried forward into the trimmed score: a system that does not start the piece
#: prints no clef, key or meter of its own, but a score that opens there needs
#: all three or every pitch in the reference is read against the wrong staff.
CARRIED = ("Clef", "KeySig", "TimeSig")


def trim(root: etree._Element, start: int, end: int) -> None:
    """Keep only bars `start`..`end`, with the state they inherit written in."""
    for staff in root.find("Score").findall("Staff"):
        bars = staff.findall("Measure")
        carried = {}
        for bar in bars[:start - 1]:
            for tag in CARRIED:
                for found in bar.iter(tag):
                    carried[tag] = found
        keep = bars[start - 1:end]
        for bar in bars:
            if bar not in keep:
                staff.remove(bar)
        if not keep:
            continue
        voice = keep[0].find("voice")
        if voice is None:
            voice = etree.SubElement(keep[0], "voice")
        for offset, tag in enumerate(CARRIED):
            if keep[0].find(f".//{tag}") is None and tag in carried:
                voice.insert(offset, carried[tag])


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("system", type=int)
    parser.add_argument("--name", default="")
    parser.add_argument("--dpi", type=int, default=FIXTURE_DPI)
    args = parser.parse_args()

    name = args.name or f"{args.slug}-s{args.system}"
    song_dir = f"songs/{args.slug}"
    sources = reference_files(args.slug)
    pdf = str(sources.pdf)

    bands = {b.index: b for b in pdf_systems.load_bounds(song_dir)}
    band = bands[args.system]
    if not band.measure_start:
        raise SystemExit(f"System {args.system} has no measure range; label the "
                         "bounds against the score first.")

    FIXTURES.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        image = pdf_systems.crop_systems(pdf, [band], tmp, dpi=args.dpi)[0]
        picture = FIXTURES / f"{name}.png"
        picture.write_bytes(Path(image.path).read_bytes())

        root = etree.parse(str(sources.cleaned)).getroot()
        implode(root, override_for(args.slug), drop_rests_for(args.slug))
        trim(root, band.measure_start, band.measure_end)
        score = Path(tmp) / f"{name}.mscx"
        etree.ElementTree(root).write(str(score), encoding="UTF-8",
                                      xml_declaration=True)
        out = FIXTURES / f"{name}.musicxml"
        cli = os.environ.get("MUSESCORE_CLI_PATH", "musescore3")
        result = subprocess.run([cli, str(score), "-o", str(out)],
                                capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not out.exists():
            raise SystemExit(f"MuseScore could not export the reference:\n"
                             f"{result.stderr[-800:]}")

    print(f"{picture}\n{out}")
    print("\nAdd to fixtures/stem-direction-fixtures.json:\n")
    print(json.dumps({name: {"image": picture.name, "reference": out.name,
                             "known_gaps": []}}, indent=2))


if __name__ == "__main__":
    main()
