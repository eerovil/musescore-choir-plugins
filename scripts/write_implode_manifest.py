#!/usr/bin/env python3
"""Write down which songs can be OMR fixtures, and what a person said about them.

The songs themselves cannot be committed -- they are gitignored working folders
holding scans of in-copyright editions -- so what travels is this manifest: for
each song, the files a fixture is built from, a hash of each so a rebuild can
prove it used the same music, how its staves map back onto the printed page, and
the verdict of somebody who looked.

The verdicts are the part that cannot be regenerated.  Everything else this
script derives; a `review` block is only ever written by hand, and rerunning
keeps whatever is already there.

    .venv/bin/python scripts/write_implode_manifest.py
"""

import hashlib
import json
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.implode_report import override_for, songs  # noqa: E402
from src.clean_score.implode import grouping  # noqa: E402

MANIFEST = Path("fixtures/omr-songs.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main() -> None:
    existing = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    # Both of these are somebody's reading of the page, and nothing here can
    # derive either: keep whatever is already written down.
    kept = {
        name: (
            entry.get("review"),
            (entry.get("grouping") or {}).get("override"),
            entry.get("source_override"),
        )
        for name, entry in existing.get("songs", {}).items()
    }

    out = {}
    for name, pdf, cleaned, made in songs(with_excluded=True):
        found = grouping(etree.parse(str(cleaned)).getroot(), override_for(name))
        out[name] = {
            "pdf": pdf.name,
            "cleaned": cleaned.name,
            "pdf_sha256": digest(pdf),
            "cleaned_sha256": digest(cleaned),
            "video": made,
            "grouping": {
                "source": found.source,
                "inferred": found.inferred,
                "printed": [printed.staves for printed in found.printed],
                "labels": [printed.label for printed in found.printed],
            },
            # Written by hand after somebody has looked at the reference beside
            # the page.  Nothing here is derived, and nothing regenerates it.
            "review": kept.get(name, (None, None, None))[0] or {"status": "unreviewed", "notes": ""},
        }
        review, override, source = kept.get(name, (None, None, None))
        if override:
            out[name]["grouping"]["override"] = override
        if source:
            out[name]["source_override"] = source

    MANIFEST.parent.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps({"version": 1, "songs": out}, indent=2, ensure_ascii=False) + "\n")
    out_count = sum(1 for entry in out.values() if entry["review"]["status"] == "excluded")
    print(f"{MANIFEST}: {len(out)} songs, {out_count} excluded, {len(out) - out_count} in the set")


if __name__ == "__main__":
    main()
