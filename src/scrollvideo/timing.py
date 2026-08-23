"""When each note sounds — on MuseScore's clock, not verovio's.

Verovio's timemap ignores MuseScore playback properties, above all the
``timeStretch=3`` that `clean_score` puts on fermatas: on the Hanget soi fixture
verovio says 48.0s where MuseScore renders 59.6s. MuseScore writes that stretch
into its MIDI export as tempo changes (120 -> 40 bpm for a fermata), so the MIDI
tempo map is the same clock the audio is rendered on.

So: musical position (quarter notes) comes from verovio, seconds come from the
MIDI tempo map, and audio and video cannot drift.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import List, Mapping, Sequence, Tuple

import mido

DEFAULT_TEMPO = 500_000  # us per quarter note = 120 bpm


@dataclass(frozen=True)
class NoteEvent:
    note_id: str
    on: float     # seconds
    off: float    # seconds


class TempoMap:
    """Quarter-note position -> seconds, following a MIDI file's tempo changes."""

    def __init__(self, changes: Sequence[Tuple[float, int]]):
        """`changes` is [(quarter position, microseconds per quarter), ...]."""
        changes = sorted(changes)
        if not changes or changes[0][0] > 0:
            changes = [(0.0, DEFAULT_TEMPO), *changes]

        self._q: List[float] = []
        self._secs: List[float] = []
        self._us: List[int] = []
        elapsed = 0.0
        for i, (q, us) in enumerate(changes):
            if i:
                elapsed += (q - self._q[-1]) * self._us[-1] / 1e6
            self._q.append(q)
            self._secs.append(elapsed)
            self._us.append(us)

    @classmethod
    def from_midi(cls, midi_path: str) -> "TempoMap":
        midi = mido.MidiFile(midi_path)
        ticks_per_beat = midi.ticks_per_beat
        changes, tick = [], 0
        for msg in mido.merge_tracks(midi.tracks):
            tick += msg.time
            if msg.type == "set_tempo":
                changes.append((tick / ticks_per_beat, msg.tempo))
        return cls(changes)

    def seconds(self, qstamp: float) -> float:
        i = max(0, bisect_right(self._q, qstamp) - 1)
        return self._secs[i] + (qstamp - self._q[i]) * self._us[i] / 1e6


def note_events(timemap: Sequence[dict], tempo: TempoMap,
                drawn_id: Mapping[str, str] | None = None) -> List[NoteEvent]:
    """Verovio timemap -> note on/off in seconds on the tempo map's clock.

    `drawn_id` maps a sounding id to the note engraved on the page; inside a
    repeated section those differ, and the same drawn note gets one event per
    pass. Sounding ids it does not cover have nothing to highlight and are
    dropped. Notes still sounding at the end (no ``off`` event) are closed at
    the last timestamp, so a final fermata stays lit instead of blinking out.
    """
    known = dict(drawn_id) if drawn_id is not None else None
    started: dict = {}
    events: List[NoteEvent] = []
    last_q = 0.0

    for entry in timemap:
        q = float(entry.get("qstamp", 0.0))
        last_q = max(last_q, q)
        for nid in entry.get("off", []):
            if nid in started:
                events.append(NoteEvent(known[nid] if known else nid,
                                        tempo.seconds(started.pop(nid)), tempo.seconds(q)))
        for nid in entry.get("on", []):
            if known is None or nid in known:
                started[nid] = q

    for nid, q_on in started.items():
        events.append(NoteEvent(known[nid] if known else nid,
                                tempo.seconds(q_on), tempo.seconds(last_q)))

    events.sort(key=lambda e: (e.on, e.off))
    return events


def scroll_anchors(timemap: Sequence[dict], tempo: TempoMap, layout,
                   drawn_id: Mapping[str, str] | None = None) -> Tuple[List[float], List[float]]:
    """(seconds, x-in-units) pairs: where the music is on the page at each moment.

    Taken from the notes' own x positions, so the scroll follows the engraving's
    spacing — a fermata's held note simply sits still for its whole duration, and
    a repeat walks back to where the repeated section is drawn.
    """
    resolve = dict(drawn_id) if drawn_id else {}
    seen: dict = {}
    for entry in timemap:
        xs = [layout.notes[resolve.get(n, n)].x for n in entry.get("on", [])
              if resolve.get(n, n) in layout.notes]
        if xs:
            seen.setdefault(tempo.seconds(float(entry.get("qstamp", 0.0))), min(xs))
    if not seen:
        raise ValueError("No notes with both timing and geometry — cannot scroll.")
    times = sorted(seen)
    return times, [seen[t] for t in times]
