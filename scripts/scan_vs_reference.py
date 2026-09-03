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
from src.song_app import pdf_systems  # noqa: E402

MANIFEST = Path("fixtures/omr-songs.json")
SCRATCH = Path("scan-eval")


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
        printed, notes = 0, 0
        for staff in staves:
            bars = staff.findall("Measure")[start - 1:end]
            here = sum(len(bar.findall(".//Chord")) for bar in bars)
            notes += here
            printed += 1 if here else 0
        out.append({"staves": printed, "bars": end - start + 1, "notes": notes})
    return out


def scanned_systems(slug: str) -> dict[int, dict]:
    state = json.loads((SCRATCH / slug / ".song.json").read_text())
    found = {}
    for entry in (state.get("scan") or {}).get("systems", {}).values():
        index = int(entry["index"])
        if entry.get("error"):
            found[index] = {"error": entry["error"]}
            continue
        path = SCRATCH / slug / entry["musicxml"]
        notes = len(etree.parse(str(path)).getroot().findall(".//note")) if path.exists() else 0
        found[index] = {"staves": entry.get("staves"), "bars": entry.get("bars"),
                        "notes": notes}
    return found


def main() -> None:
    listed = json.loads(MANIFEST.read_text())["songs"]
    slugs = sys.argv[1:] or [n for n, e in listed.items()
                             if e["review"]["status"] != "excluded"]
    totals = {"systems": 0, "staves_agree": 0, "bars_agree": 0, "holes": 0}
    for slug in slugs:
        if not (SCRATCH / slug / ".song.json").exists():
            print(f"{slug}: not scanned yet"); continue
        bands = pdf_systems.load_bounds(f"songs/{slug}")
        reference = reference_systems(slug, bands)
        scanned = scanned_systems(slug)
        print(f"\n== {slug}")
        print("  sys  page | printed staves  bars  notes | read staves  bars  notes")
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
            print(f"  {band.index:3}  p{band.page}    | "
                  f"{want.get('staves','?'):3} {want.get('bars','?'):11} "
                  f"{want.get('notes','?'):6} | "
                  f"{got.get('staves','?'):8} {got.get('bars','?'):6} "
                  f"{got.get('notes','?'):6}  {flag}")
            totals["systems"] += 1
            totals["staves_agree"] += want.get("staves") == got.get("staves")
            totals["bars_agree"] += want.get("bars") == got.get("bars")
    print(f"\n{totals['systems']} systems: "
          f"{totals['staves_agree']} with the staves the page prints, "
          f"{totals['bars_agree']} with its bars, {totals['holes']} unread")


if __name__ == "__main__":
    main()
