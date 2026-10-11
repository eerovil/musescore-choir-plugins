"""MuseScore's own playback clock: which bars it plays, and when.

Two MuseScore exports answer it. The ``.mpos`` export lists the bars in the
order playback visits them, following repeats, voltas and D.C./D.S. jumps. The
MIDI export carries the tempo map, including the stretch `clean_score` puts on a
fermata, written as tempo changes. Publishing reads both and checks them against
each other (`song_app.publish.timing`).
"""

from __future__ import annotations

from bisect import bisect_right
from typing import List, Sequence, Tuple

import mido
from lxml import etree

DEFAULT_TEMPO = 500_000  # us per quarter note = 120 bpm


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

    @property
    def changes(self) -> List[float]:
        """The quarter positions where the tempo changes, the start included."""
        return list(self._q)

    def seconds(self, qstamp: float) -> float:
        i = max(0, bisect_right(self._q, qstamp) - 1)
        return self._secs[i] + (qstamp - self._q[i]) * self._us[i] / 1e6


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
