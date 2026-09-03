#!/usr/bin/env python3
"""What homr read off each page, beside what the page actually holds.

The reference is the cleaned score imploded back to the shape of the print, so
the two are comparable system by system: both are cut at the same printed bands,
so system 7 means the same thing on both sides.  No score is given here.  What is
counted is what can be counted without one being agreed -- how many staves the
system prints and how many came back, how many bars, how many notes -- so the
numbers can be looked at before anybody decides which of them matters.

    .venv/bin/python scripts/scan_vs_reference.py

Reads `scan-eval/<slug>/` (written by scripts/scan_references.py) and the real
song's cleaned score.  Writes nothing but a table.
"""

import glob
import json
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.implode_report import drop_rests_for, override_for  # noqa: E402
from src.clean_score.implode import implode  # noqa: E402
from src.clean_score.utils import per_system  # noqa: E402
from src.song_app import pdf_systems  # noqa: E402

MANIFEST = Path("fixtures/omr-songs.json")
SCRATCH = Path("scan-eval")


def read_with(argv: list[str]) -> tuple[str, list[str]]:
    """Pull `--engine <key>` out of the arguments; results live under it."""
    if "--engine" not in argv:
        return "default", argv
    at = argv.index("--engine")
    return argv[at + 1], argv[:at] + argv[at + 2:]


def printed_staves(slug: str, bands) -> list[int] | None:
    """How many staves each printed system has, off the score's own line breaks.

    The imploded reference cannot answer this for a per-system score: it has one
    staff per printed part for the whole piece, so a page that prints 2, 2, 3, 4
    staves reads as 2 throughout, and homr getting it exactly right scores as
    four disagreements.  The converted input still has the breaks -- it is the
    same file `pdf_systems.label` takes the measure ranges from -- so where its
    systems line up with the bands, it is what the page prints.
    """
    xml = sorted(glob.glob(f"songs/{slug}/*.mscx"))
    xml = [p for p in xml if "_cleaned" not in p]
    if not xml:
        return None
    try:
        layout = per_system.layout_for_file(xml[0])
    except Exception:
        return None
    if len(layout) != len(bands):
        return None
    return [len(system.staves) for system in layout]


def reference_systems(slug: str, bands) -> list[dict]:
    """The imploded reference, cut at the printed bands.

    A staff counts as printed in a system when it has a note there: a part that
    rests through a system is not engraved, which is the whole reason a page can
    be 2-3-2-3-3 staves.
    """
    cleaned = sorted(glob.glob(f"songs/{slug}/*_cleaned.mscx"))[0]
    root = etree.parse(cleaned).getroot()
    implode(root, override_for(slug), drop_rests_for(slug))
    staves = root.find("Score").findall("Staff")
    out = []
    for band in bands:
        start, end = band.measure_start, band.measure_end
        if not start:
            out.append({})
            continue
        printed, notes, voices = 0, 0, 0
        for staff in staves:
            bars = staff.findall("Measure")[start - 1:end]
            # The lines a singer could follow on this staff: an imploded staff
            # carrying two parts has two <voice> elements in a bar, and one
            # carrying a chord written for two singers has one. Counting the
            # most any bar of the system has is what the page shows a reader.
            voices += max((len(bar.findall("voice")) for bar in bars), default=0)
            # Noteheads, not chords: MusicXML writes one <note> per notehead, so
            # counting `Chord` here would call a two-part chord one note and make
            # every divisi bar look like the scan had invented notes.
            here = sum(len(bar.findall(".//Chord/Note")) for bar in bars)
            notes += here
            printed += 1 if here else 0
        out.append({"staves": printed, "bars": end - start + 1, "notes": notes,
                    "voices": voices})
    return out


def scanned_systems(slug: str, root: Path = SCRATCH) -> dict[int, dict]:
    state = json.loads((root / slug / ".song.json").read_text())
    found = {}
    for entry in (state.get("scan") or {}).get("systems", {}).values():
        index = int(entry["index"])
        if entry.get("error"):
            found[index] = {"error": entry["error"]}
            continue
        path = root / slug / entry["musicxml"]
        notes = 0
        if path.exists():
            # A <note> holding a <rest> is silence, and the reference counts
            # noteheads: leaving rests in would score a resting bar as full.
            notes = sum(1 for n in etree.parse(str(path)).getroot().iter("note")
                        if n.find("rest") is None)
        # The part has to be in the key: homr writes a system it could not group
        # as several one-staff parts, each numbering its own staff 1 voice 1, so
        # a four-staff system counted as one line without it.
        seen = set()
        if path.exists():
            for part in etree.parse(str(path)).getroot().iter("part"):
                for note in part.iter("note"):
                    seen.add((part.get("id"), note.findtext("staff") or "1",
                              note.findtext("voice") or "1"))
        found[index] = {"staves": entry.get("staves"), "bars": entry.get("bars"),
                        "notes": notes, "voices": len(seen)}
    return found


def main() -> None:
    listed = json.loads(MANIFEST.read_text())["songs"]
    key, argv = read_with(sys.argv[1:])
    root = SCRATCH / key
    slugs = argv or [n for n, e in listed.items()
                             if e["review"]["status"] != "excluded"]
    totals = {"systems": 0, "staves_agree": 0, "bars_agree": 0, "holes": 0,
              "notes_exact": 0, "notes_want": 0, "notes_got": 0,
              "voices_agree": 0}
    print(f"read with: {key}")
    for slug in slugs:
        if not (root / slug / ".song.json").exists():
            print(f"{slug}: not scanned yet"); continue
        bands = pdf_systems.load_bounds(f"songs/{slug}")
        reference = reference_systems(slug, bands)
        printed = printed_staves(slug, bands)
        if printed:
            for want, count in zip(reference, printed):
                if want:
                    want["staves"] = count
        scanned = scanned_systems(slug, root)
        print(f"\n== {slug}   (staves the page prints: "
              f"{'the score\'s own line breaks' if printed else 'the imploded reference'})")
        print("  sys  page | page: staves voices  bars notes | read: staves voices  bars notes")
        for band, want in zip(bands, reference):
            got = scanned.get(band.index, {})
            if got.get("error"):
                print(f"  {band.index:3}  p{band.page}    | "
                      f"{want.get('staves','?'):3} {want.get('bars','?'):11} "
                      f"{want.get('notes','?'):6} | HOLE: {got['error'][:50]}")
                totals["holes"] += 1
                totals["systems"] += 1
                continue
            flag = ""
            if want.get("staves") != got.get("staves"):
                flag += " staves"
            if want.get("bars") != got.get("bars"):
                flag += " bars"
            if want.get("voices") != got.get("voices"):
                flag += " voices"
            print(f"  {band.index:3}  p{band.page}    | "
                  f"{want.get('staves','?'):11} {want.get('voices','?'):6} "
                  f"{want.get('bars','?'):5} {want.get('notes','?'):5} | "
                  f"{got.get('staves','?'):11} {got.get('voices','?'):6} "
                  f"{got.get('bars','?'):5} {got.get('notes','?'):5}  {flag}")
            totals["systems"] += 1
            totals["staves_agree"] += want.get("staves") == got.get("staves")
            totals["bars_agree"] += want.get("bars") == got.get("bars")
            totals["notes_exact"] += want.get("notes") == got.get("notes")
            totals["voices_agree"] += want.get("voices") == got.get("voices")
            totals["notes_want"] += want.get("notes") or 0
            totals["notes_got"] += got.get("notes") or 0
    print(f"\n{totals['systems']} systems: "
          f"{totals['staves_agree']} with the staves the page prints, "
          f"{totals['bars_agree']} with its bars, {totals['holes']} unread, "
          f"{totals['voices_agree']} with its voices, "
          f"{totals['notes_exact']} with its exact notehead count "
          f"({totals['notes_got']} read against {totals['notes_want']} printed)")


if __name__ == "__main__":
    main()
