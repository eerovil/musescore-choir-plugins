"""Lay the engraved bars out in the order MuseScore plays them.

Verovio expands section repeats and voltas in its timemap, but a D.C. or D.S.
jump it follows only sometimes: it gets *Illan viimeinen tango*'s D.S. al Coda
right and plays *Jouluriemua*'s two D.C.s as two passes where MuseScore plays
three. The audio and the clock both come from MuseScore, so the timeline has to
follow MuseScore's order whatever verovio made of it.

Section repeats go wrong the same way when the score is not tidy (#374): the
scan writes a "2." ending as a bracket that opens and never closes, MuseScore
plays the repeat as printed, and verovio does not expand the repeat at all. So
a score with any repeat sign is checked too: when verovio's bar order is not
MuseScore's, MuseScore's is followed.

MuseScore says what that order is. Its ``.mpos`` export ("measure positions",
meant for score-following players) lists every bar in the order it is played,
because it is written off the same repeat list playback uses. Asking for it
costs one more CLI call and means there is no second copy of MuseScore's rules
for segno, coda and Fine here to disagree with the audio one day.

The timeline is then rebuilt from verovio's own timing of each printed bar —
taken from the first time its timemap plays that bar — laid end to end in
MuseScore's order. Everything downstream (note events, the scroll, the alignment
check against the MIDI) reads the rebuilt timemap exactly as it would verovio's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from lxml import etree

from . import audio as audio_mod

# The kinds of thing a timemap entry starts and stops, as (on key, off key).
_KINDS = (("on", "off"), ("restsOn", "restsOff"))


def has_jumps(root: etree._Element) -> bool:
    """Whether the score carries a D.C./D.S. jump.

    A Marker (segno, coda, fine) on its own is only a label and changes nothing,
    and section repeats and voltas are verovio's to expand.
    """
    return root.find(".//Jump") is not None


def has_repeats(root: etree._Element) -> bool:
    """Whether the score carries a section repeat or a volta bracket."""
    return any(root.find(f".//{tag}") is not None
               for tag in ("startRepeat", "endRepeat", "Volta"))


def verovio_order(timemap: Sequence[dict], measures: Sequence[str],
                  measure_of: Mapping[str, str]) -> List[int]:
    """The 0-based printed bars in the order verovio's timemap plays them."""
    index = {bar: i for i, bar in enumerate(measures)}
    order = []
    for entry in timemap:
        timed = entry.get("measureOn")
        if timed:
            order.append(index.get(measure_of.get(timed, timed), -1))
    return order


def read_mpos(path: str) -> Tuple[int, List[int]]:
    """(how many bars MuseScore counts, the 0-based bars in played order)."""
    root = etree.parse(path).getroot()
    count = len(root.findall(".//elements/element"))
    order = [int(event.get("elid")) for event in root.findall(".//events/event")]
    if not order:
        raise NotImplementedError(
            "MuseScore did not say in which order it plays this score's bars, so the "
            "repeats and jumps cannot be followed.")
    return count, order


def played_measures(source: str, tmp: str) -> Tuple[int, List[int]]:
    """Ask MuseScore which bars it plays, in order (one CLI call)."""
    return read_mpos(audio_mod.run_musescore(source, os.path.join(tmp, "score.mpos")))


@dataclass(frozen=True)
class _Timed:
    kind: int        # index into _KINDS
    timed_id: str
    on: float        # quarters from the bar's start
    off: float


def _bar_templates(timemap: Sequence[dict], measure_of: Mapping[str, str]
                   ) -> Dict[str, Tuple[float, List[_Timed]]]:
    """Each printed bar's length and what sounds in it, from its first timing.

    Verovio's timemap is walked once: events are paired by id, and each is
    filed under the bar it starts in. A note still sounding when its bar ends
    stops at the barline — the next bar played may not be the next one printed.
    """
    visits: List[Tuple[float, str]] = []   # (start, printed bar id)
    started: Dict[Tuple[int, str], float] = {}
    spans: List[Tuple[int, str, float, float]] = []
    last_q = 0.0
    for entry in timemap:
        q = float(entry.get("qstamp", 0.0))
        last_q = max(last_q, q)
        timed = entry.get("measureOn")
        if timed:
            visits.append((q, measure_of.get(timed, timed)))
        for kind, (on_key, off_key) in enumerate(_KINDS):
            for nid in entry.get(off_key, []):
                if (kind, nid) in started:
                    spans.append((kind, nid, started.pop((kind, nid)), q))
            for nid in entry.get(on_key, []):
                started[(kind, nid)] = q
    for (kind, nid), q_on in started.items():
        spans.append((kind, nid, q_on, last_q))
    if not visits:
        raise NotImplementedError(
            "The engraving's timeline names no bars, so MuseScore's play order cannot "
            "be followed.")

    ends = [start for start, _ in visits[1:]] + [max(last_q, visits[-1][0])]
    templates: Dict[str, Tuple[float, List[_Timed]]] = {}
    first_visit: Dict[int, str] = {}
    for index, ((start, bar), end) in enumerate(zip(visits, ends)):
        if bar not in templates:
            templates[bar] = (end - start, [])
            first_visit[index] = bar

    starts = [start for start, _ in visits]
    for kind, nid, on, off in spans:
        index = _visit_at(starts, on)
        bar = first_visit.get(index)
        if bar is None:
            continue
        length, events = templates[bar]
        begin = starts[index]
        events.append(_Timed(kind, nid, on - begin, min(off, begin + length) - begin))
    return templates


def _visit_at(starts: Sequence[float], q: float) -> int:
    """The bar visit that `q` falls in (the last one starting at or before it)."""
    lo, hi = 0, len(starts)
    while lo < hi:
        mid = (lo + hi) // 2
        if starts[mid] <= q:
            lo = mid + 1
        else:
            hi = mid
    return max(0, lo - 1)


def unrolled_timemap(timemap: Sequence[dict], drawn_id: Mapping[str, str],
                     measures: Sequence[str], measure_of: Mapping[str, str],
                     count: int, order: Sequence[int]
                     ) -> Tuple[List[dict], Dict[str, str], List[float]]:
    """Verovio's timemap rebuilt in MuseScore's played bar order.

    Returns the new timemap, the matching timed-id -> drawn-id map, and `cuts`:
    the quarter-note positions where the next bar played is not the next bar
    printed, which is where the scroll has to land rather than slide.

    Refuses rather than guesses when the two programs do not agree on the bars:
    a different bar count, or a bar MuseScore plays that verovio never timed.
    """
    if count != len(measures):
        raise NotImplementedError(
            f"MuseScore counts {count} bars and the engraving has {len(measures)}, "
            "so MuseScore's play order cannot be matched to the page.")
    templates = _bar_templates(timemap, measure_of)
    missing = sorted({index + 1 for index in order
                      if not 0 <= index < len(measures) or measures[index] not in templates})
    if missing:
        raise NotImplementedError(
            "The engraving has no timing for bar(s) "
            + ", ".join(str(m) for m in missing[:8])
            + " that MuseScore plays, so MuseScore's play order cannot be followed.")

    entries: Dict[float, dict] = {}

    def entry_at(q: float) -> dict:
        key = round(q, 9)
        return entries.setdefault(key, {"qstamp": key})

    drawn: Dict[str, str] = {}
    cuts: List[float] = []
    base = 0.0
    for visit, index in enumerate(order):
        bar = measures[index]
        if visit and index != order[visit - 1] + 1:
            cuts.append(base)
        entry_at(base)["measureOn"] = f"{bar}~{visit}"
        length, events = templates[bar]
        for event in events:
            if event.timed_id not in drawn_id or event.off <= event.on:
                continue
            uid = f"{event.timed_id}~{visit}"
            drawn[uid] = drawn_id[event.timed_id]
            on_key, off_key = _KINDS[event.kind]
            entry_at(base + event.on).setdefault(on_key, []).append(uid)
            entry_at(base + event.off).setdefault(off_key, []).append(uid)
        base += length
    entry_at(base)
    return [entries[q] for q in sorted(entries)], drawn, cuts
