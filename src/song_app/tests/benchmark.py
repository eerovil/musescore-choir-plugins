"""The committed slice of the OMR benchmark, as data the tests can point at.

Not a test module. ``fixtures/omr-benchmark/pages.json`` is the file of record —
this only reads it, turns the bounds into :class:`SystemBounds` and the truth
table into rows, so no test has to know the layout of either.

Why it exists at all: everything #80 concluded rested on ``~/omr-benchmark/``,
which is host state. The pages that can be committed are committed, and this is
how they are reached.
"""
import csv
import json
import os
from dataclasses import dataclass
from typing import List, Optional

from src.song_app.pdf_systems import SystemBounds

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
BENCHMARK_DIR = os.path.join(REPO_ROOT, "fixtures", "omr-benchmark")
MANIFEST = os.path.join(BENCHMARK_DIR, "pages.json")


@dataclass(frozen=True)
class TruthRow:
    """One bar of one voice of one staff, as the hand transcription has it."""

    measure: int
    staff: int
    part: str
    chords: int
    notes: int
    chords_with_2plus_noteheads: int
    rests: int


@dataclass(frozen=True)
class BenchmarkPage:
    id: str
    title: str
    pdf: str                       # absolute
    page: int
    staves: int
    voices_per_staff: int
    systems: List[SystemBounds]
    truth_path: Optional[str] = None
    transcription_path: Optional[str] = None

    @property
    def bars(self) -> int:
        return sum(b.measure_end - b.measure_start + 1 for b in self.systems)

    def truth(self) -> List[TruthRow]:
        """The per-bar, per-voice note counts. Empty when the page has none."""
        if not self.truth_path:
            return []
        with open(self.truth_path, encoding="utf-8") as f:
            return [
                TruthRow(
                    measure=int(row["measure"]),
                    staff=int(row["staff"]),
                    part=row["part"],
                    chords=int(row["chords"]),
                    notes=int(row["notes"]),
                    chords_with_2plus_noteheads=int(row["chords_with_2plus_noteheads"]),
                    rests=int(row["rests"]),
                )
                for row in csv.DictReader(f)
            ]


def pages() -> List[BenchmarkPage]:
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    return [_page(entry) for entry in manifest["pages"]]


def page(page_id: str) -> BenchmarkPage:
    for candidate in pages():
        if candidate.id == page_id:
            return candidate
    raise KeyError(f"No benchmark page {page_id!r} in {MANIFEST}")


def _page(entry: dict) -> BenchmarkPage:
    return BenchmarkPage(
        id=entry["id"],
        title=entry["title"],
        pdf=os.path.join(REPO_ROOT, entry["pdf"]),
        page=entry["page"],
        staves=entry["staves"],
        voices_per_staff=entry["voices_per_staff"],
        systems=[SystemBounds(**s) for s in entry["systems"]],
        truth_path=_abs(entry.get("truth")),
        transcription_path=_abs(entry.get("transcription")),
    )


def _abs(path: Optional[str]) -> Optional[str]:
    return os.path.join(REPO_ROOT, path) if path else None
