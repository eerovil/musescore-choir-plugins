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

from scripts.implode_report import (  # noqa: E402
    drop_rests_for,
    override_for,
    system_override_for,
)
from scripts.reference_manifest import reference_files  # noqa: E402
from src.clean_score.implode import grouping, implode  # noqa: E402
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


def system_override(root: etree._Element, start: int) -> list[list[str]] | None:
    """The staves the page prints in the system this band cuts out.

    `implode` groups for a whole score: one slot per top-to-bottom page
    position, and each slot holds every part that ever occupies it.  That is
    right for a score and wrong for one band of it, in two ways at once.

    A band gets **too many staves**, because the slots are sized by the widest
    system in the piece and a part that rests through a system is simply not
    printed.  `kayttaytymisohjeita-s1` came out with a third staff of rests the
    page has no row for.

    And a band gets **the wrong staves in a slot**, because slot membership is
    unioned too.  On `laulun-aika-3` position 1 carries T1 and T2 in some systems
    and T3 in others, so every band was given one tenor staff labelled `T3/T1/T2`
    holding all three -- against a page that prints them on three separate
    staves.

    Both go away by asking the band's own system instead of the score.  The
    per-system map records it position by position, `Grouping.systems` carries it
    through, and `implode` already accepts a grouping by part name -- the same
    door a person's reading of the page comes through.

    The positions are numbered by musical rank, so this can still be **wrong
    about the order** where the page is: a voice printed above the parts it
    ranks below -- `laulun-aika-3`'s T3 over T1 and T2 -- comes back underneath
    them.  `system_override_for` is where that is corrected, and `band_grouping`
    is the order the three answers are asked in.

    Nothing is inferred here.  A score with no per-system map, or one whose map
    does not cover this bar, falls back to whatever `band_grouping` has left.
    """
    found = grouping(root)
    system = next((s for s in found.systems if s.start <= start <= s.end), None)
    if system is None or not system.printed:
        return None
    printed = [list(system.printed[position].names) for position in sorted(system.printed)]
    if any(not name for group in printed for name in group):
        return None
    return printed


def band_grouping(
    slug: str, root: etree._Element, start: int
) -> list[list[str]] | None:
    """The staves this band's own system prints, best evidence first.

    A reading of **this system** beats the score's own map, which beats a
    reading of the **whole song** -- and that last step is a correction.  A
    whole-song reading used to beat everything, on the principle that a person
    who looked at the page outranks a map the app wrote.  It does, about the
    same thing: `laulun-aika-3`'s recorded reading is one grouping for a song
    that prints two staves in four systems, three in two of them and four in the
    last, so it was applied to five bands it does not describe and made three of
    the worst six references in the corpus.  Specificity, not authority, is what
    orders these -- so a per-system reading is added and sits at the top.

    The map is right about which staves a system prints and can be wrong about
    their **order**: it numbers positions by musical rank (S<A<T<B), and an
    extra voice printed above the others -- `laulun-aika-3`'s T3 -- is then
    ranked below the parts it sits over.  That is what a per-system reading is
    for here; where the two agree there is nothing to record.
    """
    return (
        system_override_for(slug, start)
        or system_override(root, start)
        or override_for(slug)
    )


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
        override = band_grouping(args.slug, root, band.measure_start)
        implode(root, override, drop_rests_for(args.slug))
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
