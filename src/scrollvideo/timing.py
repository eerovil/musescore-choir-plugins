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
import numpy as np

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


def _timed_events(timemap: Sequence[dict], tempo: TempoMap,
                  drawn_id: Mapping[str, str] | None, *,
                  on_key: str, off_key: str) -> List[NoteEvent]:
    """Pair one kind of Verovio on/off event on MuseScore's clock."""
    known = dict(drawn_id) if drawn_id is not None else None
    started: dict = {}
    events: List[NoteEvent] = []
    last_q = 0.0

    for entry in timemap:
        q = float(entry.get("qstamp", 0.0))
        last_q = max(last_q, q)
        for nid in entry.get(off_key, []):
            if nid in started:
                events.append(NoteEvent(known[nid] if known else nid,
                                        tempo.seconds(started.pop(nid)), tempo.seconds(q)))
        for nid in entry.get(on_key, []):
            if known is None or nid in known:
                started[nid] = q

    for nid, q_on in started.items():
        events.append(NoteEvent(known[nid] if known else nid,
                                tempo.seconds(q_on), tempo.seconds(last_q)))

    events.sort(key=lambda e: (e.on, e.off))
    return events


def note_events(timemap: Sequence[dict], tempo: TempoMap,
                drawn_id: Mapping[str, str] | None = None) -> List[NoteEvent]:
    """Verovio note on/off events in seconds on MuseScore's tempo map.

    `drawn_id` maps a sounding id to the note engraved on the page; inside a
    repeated section those differ, and the same drawn note gets one event per
    pass. Sounding ids it does not cover have nothing to highlight and are
    dropped. Notes still sounding at the end (no ``off`` event) are closed at
    the last timestamp, so a final fermata stays lit instead of blinking out.
    """
    return _timed_events(timemap, tempo, drawn_id, on_key="on", off_key="off")


def rest_events(timemap: Sequence[dict], tempo: TempoMap,
                drawn_id: Mapping[str, str] | None = None) -> List[NoteEvent]:
    """Verovio rest on/off events in seconds on MuseScore's tempo map."""
    return _timed_events(timemap, tempo, drawn_id,
                         on_key="restsOn", off_key="restsOff")


# How long a window the scroll speed is averaged over. Long enough to even out
# per-measure spacing, short enough that the sung note stays put on screen.
SMOOTH_SECONDS = 2.0
# A backward jump this large is a repeat returning to an earlier part of the page,
# not spacing noise; smoothing across it would slide the scroll through the jump.
JUMP_FRACTION = 0.25


def smooth_scroll(times: Sequence[float], xs: Sequence[float], *, fps: int,
                  seconds: float = SMOOTH_SECONDS,
                  page_width: float = 0.0) -> Tuple[List[float], List[float]]:
    """Even out the scroll speed without letting the sung note wander off station.

    The engraving decides where a note sits, so following note positions exactly
    makes the scroll speed track how densely each measure happens to be engraved.
    Averaging over a couple of seconds removes that jitter while keeping the curve
    anchored to the music: on the Käyttäytymisohjeita fixture it takes the speed's
    coefficient of variation from 0.30 to 0.15 while the sung note moves less than
    2% of a screen. Scrolling at a dead constant speed would instead drift by
    nearly half a screen, which is why this smooths rather than straightens.

    It is the **speed** that is averaged, and the result integrated back into
    positions. Averaging positions directly would flatten the curve at both ends
    (the pad has no slope to continue), so a perfectly even scroll would come out
    ramping up at the start and down at the finish.

    Repeats stay sharp: a jump back to a repeated section is real motion, and each
    stretch between jumps is smoothed on its own. Jumps are found in the anchors,
    before resampling smears them across frames.
    """
    if len(times) < 2 or seconds <= 0:
        return list(times), list(xs)

    at = np.asarray(times, dtype=float)
    ax = np.asarray(xs, dtype=float)
    threshold = JUMP_FRACTION * page_width if page_width > 0 else float("inf")
    starts = [0] + [i + 1 for i, step in enumerate(np.diff(ax)) if step < -threshold]
    bounds = starts + [len(at)]

    width = max(1, int(round(seconds * fps)) | 1)
    out_times: List[float] = []
    out_xs: List[float] = []

    for first, last in zip(bounds, bounds[1:]):
        seg_t, seg_x = at[first:last], ax[first:last]
        if len(seg_t) < 2:
            out_times.extend(seg_t.tolist())
            out_xs.extend(seg_x.tolist())
            continue

        grid = np.arange(seg_t[0], seg_t[-1] + 0.5 / fps, 1.0 / fps)
        curve = np.interp(grid, seg_t, seg_x)
        if len(curve) > width + 1:
            speed = np.diff(curve)
            padded = np.pad(speed, width // 2, mode="edge")
            eased = np.convolve(padded, np.ones(width) / width, mode="valid")
            travelled = np.cumsum(eased)
            # Averaging does not conserve the total exactly; rescale so the segment
            # still ends where the music does, which keeps the notes in step.
            if travelled[-1] > 0:
                travelled *= (curve[-1] - curve[0]) / travelled[-1]
            curve = np.concatenate([[curve[0]], curve[0] + travelled])
        out_times.extend(grid.tolist())
        out_xs.extend(curve.tolist())

    return out_times, out_xs


def scroll_anchors(timemap: Sequence[dict], tempo: TempoMap, layout,
                   drawn_id: Mapping[str, str] | None = None,
                   staff_limit: int | None = None) -> Tuple[List[float], List[float]]:
    """(seconds, x-in-units) pairs: where the music is on the page at each moment.

    Taken from the active notes' and rests' own x positions, so the scroll follows
    the engraving's spacing — a fermata's held note simply sits still for its whole
    duration, and a repeat walks back to where the repeated section is drawn.
    """
    resolve = dict(drawn_id) if drawn_id else {}
    seen: dict = {}
    for entry in timemap:
        element_ids = (*entry.get("on", []), *entry.get("restsOn", []))
        geometries = [layout.playing(resolve.get(n, n)) for n in element_ids]
        xs = [geom.x for geom in geometries if geom is not None and
              not getattr(geom, "measure_rest", False) and
              (staff_limit is None or layout.staff_index(geom) < staff_limit)]
        if xs:
            seen.setdefault(tempo.seconds(float(entry.get("qstamp", 0.0))), min(xs))
    if not seen:
        raise ValueError("No notes with both timing and geometry — cannot scroll.")
    times = sorted(seen)
    return times, [seen[t] for t in times]
