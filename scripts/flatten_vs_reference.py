#!/usr/bin/env python3
"""What flattening a system costs, measured against the page.

Issue #166 measured this while answering a different question and left the
ticket to the board: `omr_systems.flatten` scored **73.5%** of the notes right
where homr's own reading of the same crop scored **92.3%**, and issue #172 is
the card for it.  Neither the `scan-eval/` scratch nor that session's matcher
survived it -- nothing in `scripts/` counts notes at this level -- so this is
that comparison rebuilt and committed, and it is what a change to the
flattening has to be shown against.

Three readings of the same homr parses, so the difference between them is the
app's code and nothing else:

    as homr wrote it   the fragment's own notes, placed by following homr's
                       cursor -- a note moves it on, a `backup` winds it back,
                       a `forward` moves it on.  The ceiling: the app cannot be
                       righter than what it was handed.
    after flattening   `omr_systems.flatten`, which is what the scan stage
                       actually assembles a score out of.

A note is **right** when the reference has one on the same printed staff, in the
same bar, at the same beat, at the same pitch.  Beat and pitch both, because
either alone hides the defect this exists to see: a voice slid to the head of
the bar keeps every pitch it had.  So a note landing on a beat the page does not
put it on is reported separately -- that is the slide, counted.

The reference is the cleaned score imploded back to the shape of the print, the
same one `scan_vs_reference.py` uses, converted to MusicXML so both sides are
read by one reader.  It needs the MuseScore CLI.

    .venv/bin/python scripts/flatten_vs_reference.py
    .venv/bin/python scripts/flatten_vs_reference.py --engine system-4 <slug> ...

Reads `scan-eval/<engine>/<slug>/` (written by scripts/scan_references.py).
Writes nothing but a table.
"""

import json
import os
import subprocess
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.implode_report import drop_rests_for, override_for  # noqa: E402
from scripts.reference_manifest import manifest, reference_files  # noqa: E402
from scripts.scan_vs_reference import SCRATCH, read_with  # noqa: E402
from src.clean_score.implode import implode  # noqa: E402
from src.song_app import omr_systems, pdf_systems  # noqa: E402

CLI_TIMEOUT = 180


# --- one reader, used on both sides --------------------------------------


def events(part: etree._Element, divisions_hint: int = 0) -> dict:
    """The sounding notes of one MusicXML part: ``{bar: [(beat, pitch), ...]}``.

    Positions come from the cursor, not from adding durations up: a bar written
    as one voice after another is only half as long as its notes, and a chord
    note sounds *with* the note before it rather than after it.  Beats are
    quarter notes from the head of the bar, so two documents that chose
    different ``divisions`` still compare.
    """
    out: dict[int, list] = {}
    divisions = divisions_hint or 1
    for number, measure in enumerate(part.findall("measure"), start=1):
        found: list = []
        at = Fraction(0)
        onset = Fraction(0)
        for child in measure:
            if child.tag == "attributes":
                declared = child.findtext("divisions")
                if declared and declared.strip().isdigit():
                    divisions = max(1, int(declared.strip()))
            elif child.tag == "backup":
                at -= Fraction(_int(child.findtext("duration")), divisions)
            elif child.tag == "forward":
                at += Fraction(_int(child.findtext("duration")), divisions)
            elif child.tag == "note":
                length = Fraction(_int(child.findtext("duration")), divisions)
                stacked = child.find("chord") is not None
                grace = child.find("grace") is not None
                if not stacked and not grace:
                    onset = at
                    at += length
                if child.find("rest") is None and not grace:
                    found.append((onset, pitch_of(child)))
        out[number] = found
    return out


#: Semitones above C for each letter, so a pitch is one number and an octave is
#: an addition rather than a piece of string to compare.
_STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def pitch_of(note: etree._Element) -> int:
    step = (note.findtext("pitch/step") or "C").strip().upper()
    octave = _int(note.findtext("pitch/octave"))
    return _STEPS.get(step, 0) + _int(note.findtext("pitch/alter")) + 12 * octave


def _int(text) -> int:
    try:
        return int(float((text or "").strip()))
    except (ValueError, AttributeError):
        return 0


# --- the reference -------------------------------------------------------


def reference_parts(slug: str, cache: Path) -> list[dict]:
    """The imploded reference as one ``events`` map per printed staff.

    Imploded, because the scan reads the lines the page prints and the cleaned
    score has one staff per singing part: a page printing two staves for four
    voices would otherwise score every divisi bar as two staves of invention.
    """
    out = cache / f"{slug}.reference.musicxml"
    if not out.exists():
        root = etree.parse(str(reference_files(slug).cleaned)).getroot()
        implode(root, override_for(slug), drop_rests_for(slug))
        source = cache / f"{slug}.reference.mscx"
        etree.ElementTree(root).write(str(source), xml_declaration=True,
                                      encoding="UTF-8")
        cli = os.getenv("MUSESCORE_CLI_PATH")
        if not cli:
            raise SystemExit("MUSESCORE_CLI_PATH is unset; the reference cannot "
                             "be converted for comparison.")
        subprocess.run([cli, "-o", str(out), str(source)], check=True,
                       timeout=CLI_TIMEOUT, capture_output=True)
    root = etree.parse(str(out)).getroot()
    return [events(part) for part in root.findall("part")]


# --- the two readings of a scan fragment ---------------------------------


def as_homr_wrote_it(path: str) -> list[dict]:
    """The fragment's staves, split but not re-laid.

    The same split `flatten` makes -- a part is one or two staves and the name
    is fiction -- with homr's own onsets left exactly as they are.  Anything
    below this number is the app losing something it was given.
    """
    root = etree.parse(path).getroot()
    out = []
    for part in root.findall("part"):
        for number in sorted(_staves_of(part)):
            out.append(_events_for_staff(part, number))
    return out


def _staves_of(part: etree._Element) -> set:
    declared = 0
    for attrs in part.iter("attributes"):
        text = attrs.findtext("staves")
        if text and text.strip().isdigit():
            declared = max(declared, int(text.strip()))
    used = {int(n.text.strip()) for n in part.iter("staff")
            if n.text and n.text.strip().isdigit()}
    return set(range(1, declared + 1)) | used or {1}


def _events_for_staff(part: etree._Element, number: int) -> dict:
    """One staff's notes, read off the whole part's cursor.

    The cursor is the *part's*, so a staff reached through a backup starts where
    that backup put it -- which is the whole of what the flattening has to keep.
    """
    out: dict[int, list] = {}
    divisions = 1
    for bar, measure in enumerate(part.findall("measure"), start=1):
        found: list = []
        at = Fraction(0)
        onset = Fraction(0)
        for child in measure:
            if child.tag == "attributes":
                declared = child.findtext("divisions")
                if declared and declared.strip().isdigit():
                    divisions = max(1, int(declared.strip()))
            elif child.tag == "backup":
                at -= Fraction(_int(child.findtext("duration")), divisions)
            elif child.tag == "forward":
                at += Fraction(_int(child.findtext("duration")), divisions)
            elif child.tag == "note":
                stacked = child.find("chord") is not None
                grace = child.find("grace") is not None
                if not stacked and not grace:
                    onset = at
                    at += Fraction(_int(child.findtext("duration")), divisions)
                here = child.findtext("staff")
                mine = int(here.strip()) if here and here.strip().isdigit() else 1
                if mine == number and child.find("rest") is None and not grace:
                    found.append((onset, pitch_of(child)))
        out[bar] = found
    return out


def after_flattening(path: str) -> list[dict]:
    """The staves the scan stage actually assembles, through `flatten`."""
    out = []
    for staff in omr_systems.flatten(path):
        part = etree.Element("part")
        for measure in staff.measures:
            part.append(measure)
        out.append(events(part, divisions_hint=staff.divisions))
    return out


# --- scoring -------------------------------------------------------------


#: The octaves a printed staff may be read at and still be the same music. A
#: men's-choir tenor line is printed in treble and *sounds* an octave down, and
#: `clean_score` moves it there (`octave_down`), so the reference is an octave
#: below the page for those staves while homr reads what is printed. Counting
#: that as an error would mark every note of the top staff wrong on half this
#: corpus and drown the thing being measured.
OCTAVES = (0, -12, 12, -24, 24)


def score(want: list[dict], got: list[dict], first_bar: int, bars: int,
          shifts: Sequence[int] = ()) -> Counter:
    """How much of the page one reading found, staff column by staff column.

    Columns are paired top-down, which is how `assemble` fills them; bars are
    paired by position in the band.  A reading with fewer staves or fewer bars
    than the page simply scores nothing for what it does not have, rather than
    being excused it.
    """
    tally = Counter()
    for column in range(len(want)):
        shift = shifts[column] if column < len(shifts) else 0
        reference = {bar: [(beat, p + shift) for beat, p in notes]
                     for bar, notes in want[column].items()}
        reading = got[column] if column < len(got) else {}
        for n in range(bars):
            here = list(reference.get(first_bar + n, []))
            theirs = list(reading.get(n + 1, []))
            tally["notes"] += len(here)
            left = list(theirs)
            for note in list(here):
                if note in left:
                    left.remove(note)
                    here.remove(note)
                    tally["right"] += 1
            # What is left over is scored again on pitch alone: a note the
            # reading has at the wrong beat is the slide, and counting it as
            # simply missing would hide what kind of wrong it is.
            pitches = Counter(p for _beat, p in left)
            for _beat, p in here:
                if pitches[p]:
                    pitches[p] -= 1
                    tally["off_beat"] += 1
                else:
                    tally["missing"] += 1
    return tally


def octave_shifts(want: list[dict], read: list) -> list[int]:
    """Which octave each printed staff of this song is written at.

    Chosen once per song per staff, off **homr's own** reading rather than the
    flattened one, and then used for both -- so the two readings are always
    scored against the same reference and the choice cannot flatter either.
    """
    shifts = []
    for column in range(len(want)):
        best, chosen = -1, 0
        for shift in OCTAVES:
            found = sum(
                score(want[column:column + 1],
                      homr[column:column + 1] if column < len(homr) else [],
                      band.measure_start,
                      band.measure_end - band.measure_start + 1,
                      [shift])["right"]
                for _index, band, homr, _flat in read)
            if found > best:
                best, chosen = found, shift
        shifts.append(chosen)
    return shifts


def main() -> None:
    load_dotenv()
    key, argv = read_with(sys.argv[1:])
    root = SCRATCH / key
    listed = manifest()
    slugs = argv or [n for n, e in listed.items()
                     if e["review"]["status"] != "excluded"]
    # Beside the scan it is a reference for: converting it costs a MuseScore
    # run, and the same six are wanted again on the next measurement.
    cache = root / ".reference"
    cache.mkdir(parents=True, exist_ok=True)

    totals = Counter()
    print(f"read with: {key}")
    print(f"{'system':<36} {'homr':>7} {'flatten':>9}   "
          f"{'slid':>5} {'lost':>5}  staves")
    for slug in slugs:
        state = root / slug / ".song.json"
        if not state.exists():
            print(f"{slug}: not scanned yet")
            continue
        bands = {b.index: b for b in pdf_systems.load_bounds(f"songs/{slug}")}
        want = reference_parts(slug, cache)
        systems = (json.loads(state.read_text()).get("scan") or {}).get("systems", {})
        read = []
        for entry in sorted(systems.values(), key=lambda e: int(e["index"])):
            index = int(entry["index"])
            band = bands.get(index)
            if entry.get("error") or band is None or not band.measure_start:
                print(f"{slug}-s{index:<30} unread")
                continue
            path = str(root / slug / entry["musicxml"])
            read.append((index, band, as_homr_wrote_it(path), after_flattening(path)))

        shifts = octave_shifts(want, read)
        for index, band, homr_read, flat_read in read:
            bars = band.measure_end - band.measure_start + 1
            homr = score(want, homr_read, band.measure_start, bars, shifts)
            flat = score(want, flat_read, band.measure_start, bars, shifts)
            totals.update({f"homr_{k}": v for k, v in homr.items()})
            totals.update({f"flat_{k}": v for k, v in flat.items()})
            totals["systems"] += 1
            print(f"{slug}-s{index:<30} {_pc(homr):>6}% {_pc(flat):>8}%   "
                  f"{flat['off_beat']:>5} {flat['missing']:>5}  "
                  f"{len(want)}->{len(flat_read)}")

    if totals["systems"]:
        print(f"\n{totals['systems']} systems, {totals['homr_notes']} notes on the page")
        print(f"  as homr wrote it   {_share(totals['homr_right'], totals['homr_notes'])}%")
        print(f"  after flattening   {_share(totals['flat_right'], totals['flat_notes'])}%"
              f"   ({totals['flat_off_beat']} on the wrong beat, "
              f"{totals['flat_missing']} not found at all)")


def _pc(tally: Counter) -> str:
    return _share(tally["right"], tally["notes"])


def _share(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}" if whole else "n/a"


if __name__ == "__main__":
    main()
