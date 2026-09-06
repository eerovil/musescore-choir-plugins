"""The operator answer a measurement was made with, frozen so it cannot move.

`scripts/answered_vs_reference.py` scores an assembled page with the per-system
grid answered, and the answer it uses is the thing the whole number turns on.
That answer used to be read live: the bands out of `songs/<slug>/.systems.json`
and the grouping out of the reviewed cleaned score, both of which are host state
and both of which have already moved under earlier measurements on this map.  A
committed script reading them would keep running and keep printing a number,
with nothing in the evidence saying the question had changed.

So this pull request proposes that the answer be **written down**.  A manifest
entry names, per page, each band's index, the measure range it covers and the
grouping the grid was answered with, plus a digest of the two files that were
read to get them.  The scoring path then reads the manifest and never the songs,
which is what makes a frozen run reproducible on a host that has no `songs/` at
all.

**Drift is refused, not absorbed.**  When the song files *are* present they are
compared against the entry, and any difference stops the run naming the band and
both readings.  Silently taking the live value would be the failure this file
exists to prevent; silently keeping the frozen one and saying nothing would be
the same failure wearing better manners.  Re-recording is a deliberate act
(`--record`), and it says what it changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: Committed beside the reviewed-song manifest, and for the same reason: it is
#: what a claim in `CLAUDE.md` rests on, so it travels with the checkout.
MANIFEST = Path("fixtures/answered-pages.json")


class Drifted(RuntimeError):
    """The song files no longer say what the frozen run was made against."""


class NotFrozen(RuntimeError):
    """Nothing has been written down for this page."""


@dataclass(frozen=True)
class FrozenBand:
    """One printed system, as the frozen run saw it."""

    index: int
    measure_start: int
    measure_end: int
    #: The printed staves of this system, each as the cleaned parts it carries --
    #: `[["T1", "T2"], ["B1"], ["B2"]]`. This is the grid answer.
    grouping: List[List[str]]

    def to_dict(self) -> dict:
        # One string per printed staff -- `["T1+T2", "B1", "B2"]` -- because the
        # manifest is read by people as well as by the script, and a nested list
        # of one name per line buries the answer in punctuation. Part names are
        # a voice letter and a number, so `+` cannot occur inside one.
        return {
            "index": self.index,
            "measure_start": self.measure_start,
            "measure_end": self.measure_end,
            "staves": ["+".join(staff) for staff in self.grouping],
        }

    @staticmethod
    def from_dict(raw: dict) -> "FrozenBand":
        return FrozenBand(
            index=int(raw["index"]),
            measure_start=int(raw["measure_start"]),
            measure_end=int(raw["measure_end"]),
            grouping=[staff.split("+") for staff in raw["staves"]],
        )


@dataclass(frozen=True)
class FrozenPage:
    """Everything a run needs to answer one page's grid without reading a song."""

    song: str
    page: int
    bands: List[FrozenBand]
    #: What the bounds and the cleaned score hashed to when this was recorded.
    #: Carried so a drift report can say *which* file moved, not merely that one
    #: did -- a bounds edit and a score edit want different responses.
    bounds_sha256: str = ""
    cleaned_sha256: str = ""

    @property
    def key(self) -> Tuple[str, int]:
        return (self.song, self.page)

    @property
    def name(self) -> str:
        return f"{self.song}-p{self.page}"

    @property
    def groupings(self) -> List[List[List[str]]]:
        return [band.grouping for band in self.bands]

    def to_dict(self) -> dict:
        return {
            "song": self.song,
            "page": self.page,
            "bounds_sha256": self.bounds_sha256,
            "cleaned_sha256": self.cleaned_sha256,
            "bands": [band.to_dict() for band in self.bands],
        }

    @staticmethod
    def from_dict(raw: dict) -> "FrozenPage":
        return FrozenPage(
            song=raw["song"],
            page=int(raw["page"]),
            bands=[FrozenBand.from_dict(b) for b in raw["bands"]],
            bounds_sha256=raw.get("bounds_sha256", ""),
            cleaned_sha256=raw.get("cleaned_sha256", ""),
        )


def digest(path: Path) -> str:
    """The short hash the manifest records, or `""` for a file that is not here."""
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load(path: Path = MANIFEST) -> Dict[Tuple[str, int], FrozenPage]:
    """Every page anybody has frozen, keyed by song and page."""
    if not Path(path).is_file():
        return {}
    raw = json.loads(Path(path).read_text())
    pages = [FrozenPage.from_dict(entry) for entry in raw.get("pages", [])]
    return {page.key: page for page in pages}


def frozen_page(
    song: str, page: int, path: Path = MANIFEST
) -> FrozenPage:
    """The entry for one page, or a refusal naming it.

    A page nobody has written down is an error and never a reason to go and read
    the song: the answer is the measurement's input, and inventing it from
    whatever the host holds today is exactly the substitution this refuses.
    """
    found = load(path).get((song, page))
    if found is None:
        raise NotFrozen(
            f"{song}-p{page} is not in {path}: no grid answer has been frozen for "
            f"it, and the live song is not a substitute. Record it first "
            f"(`scripts/answered_vs_reference.py --record {song}-p{page}`)."
        )
    return found


def save(pages: Sequence[FrozenPage], path: Path = MANIFEST, why: str = "") -> None:
    """Write the manifest out, pages in a stable order."""
    ordered = sorted(pages, key=lambda p: (p.song, p.page))
    body = {
        "why": why or (
            "The grid answers each measurement in scripts/answered_vs_reference.py "
            "was made with. Frozen so a later run of the same script on the same "
            "cached parses cannot quietly answer a different question."
        ),
        "pages": [page.to_dict() for page in ordered],
    }
    Path(path).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")


def compare(frozen: FrozenPage, live: FrozenPage) -> List[str]:
    """What the song says now that the frozen run did not, band by band.

    Returns sentences rather than raising, so a caller can report every
    difference at once. Digests are deliberately **not** compared: a cleaned
    score can be edited in ways that leave this page's grouping alone, and
    stopping a run over that would train somebody to re-record without reading.
    What is compared is what the measurement actually consumed.
    """
    said = []
    if [b.index for b in frozen.bands] != [b.index for b in live.bands]:
        said.append(
            f"{frozen.name}: frozen against systems "
            f"{[b.index for b in frozen.bands]}, the song now has "
            f"{[b.index for b in live.bands]}"
        )
        return said
    for was, now in zip(frozen.bands, live.bands):
        where = f"{frozen.name} s{was.index}"
        if (was.measure_start, was.measure_end) != (now.measure_start, now.measure_end):
            said.append(
                f"{where}: frozen over bars {was.measure_start}-{was.measure_end}, "
                f"the song now says {now.measure_start}-{now.measure_end}"
            )
        if was.grouping != now.grouping:
            said.append(
                f"{where}: frozen answering {_say(was.grouping)}, the song now "
                f"says {_say(now.grouping)}"
            )
    return said


def check(frozen: FrozenPage, live: Optional[FrozenPage]) -> None:
    """Refuse a run whose song has moved under it.

    `live` is `None` when the song is not on this host, which is not drift and
    not an error: the frozen entry is the whole input, so the run is exactly as
    reproducible without the song as with it.
    """
    if live is None:
        return
    said = compare(frozen, live)
    if said:
        raise Drifted(
            "\n".join(
                ["The song no longer says what this measurement was frozen against:"]
                + [f"  {line}" for line in said]
                + ["Re-record it deliberately (--record) if the song is right now; "
                   "do not score against a mixture of the two."]
            )
        )


def _say(grouping: Sequence[Sequence[str]]) -> str:
    return ", ".join("+".join(staff) for staff in grouping) or "nothing"
