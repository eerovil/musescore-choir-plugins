"""The clock: quarter-note positions -> seconds, following MuseScore's tempo map."""

import mido
import pytest

from src.scrollvideo.geometry import Layout, NoteGeom
from src.scrollvideo.timing import (DEFAULT_TEMPO, NoteEvent, TempoMap,
                                    note_events, rest_events, scroll_anchors)


def test_constant_tempo_is_linear():
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])          # 120 bpm
    assert tempo.seconds(0) == 0.0
    assert tempo.seconds(4) == pytest.approx(2.0)


def test_missing_initial_tempo_defaults_to_120():
    assert TempoMap([]).seconds(4) == pytest.approx(2.0)


def test_fermata_window_stretches_exactly_three_times():
    """A 120 -> 40 -> 120 bpm window over one quarter is a 3x fermata."""
    slow = mido.bpm2tempo(40)
    tempo = TempoMap([(0.0, DEFAULT_TEMPO), (2.0, slow), (3.0, DEFAULT_TEMPO)])
    assert tempo.seconds(2) == pytest.approx(1.0)     # unaffected before
    assert tempo.seconds(3) == pytest.approx(2.5)     # the stretched quarter: 1.5s not 0.5s
    assert tempo.seconds(4) == pytest.approx(3.0)     # back to normal afterwards


def test_from_midi_reads_the_tempo_changes(tmp_path):
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=DEFAULT_TEMPO, time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(40), time=960))
    track.append(mido.MetaMessage("set_tempo", tempo=DEFAULT_TEMPO, time=480))
    path = tmp_path / "t.mid"
    midi.save(path)

    tempo = TempoMap.from_midi(str(path))
    assert tempo.seconds(2) == pytest.approx(1.0)
    assert tempo.seconds(3) == pytest.approx(2.5)


def _timemap(*entries):
    return list(entries)


def test_note_events_convert_on_and_off_to_seconds():
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])
    events = note_events(_timemap({"qstamp": 0, "on": ["a"]},
                                  {"qstamp": 2, "off": ["a"], "on": ["b"]},
                                  {"qstamp": 4, "off": ["b"]}), tempo)
    assert events == [NoteEvent("a", 0.0, 1.0), NoteEvent("b", 1.0, 2.0)]


def test_rest_events_convert_rest_on_and_off_to_seconds():
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])
    events = rest_events(_timemap({"qstamp": 1, "restsOn": ["rest"]},
                                  {"qstamp": 3, "restsOff": ["rest"]}), tempo)
    assert events == [NoteEvent("rest", 0.5, 1.5)]


def test_rest_onset_moves_the_scroll_but_spacer_rest_does_not():
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])
    layout = Layout(1000, 500,
                    {"note": NoteGeom(100, 100, 100, 25)}, [100, 300],
                    {"rest": NoteGeom(300, 100, 100, 25),
                     "spacer": NoteGeom(900, 300, 300, 25)})
    timemap = _timemap(
        {"qstamp": 0, "on": ["note"]},
        {"qstamp": 1, "restsOn": ["rest", "spacer"]},
    )
    assert scroll_anchors(timemap, tempo, layout, staff_limit=1) == (
        [0.0, 0.5], [100, 300])


def test_note_still_sounding_at_the_end_is_closed_not_dropped():
    """A final fermata must stay lit, not blink out."""
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])
    events = note_events(_timemap({"qstamp": 0, "on": ["a"]}, {"qstamp": 4}), tempo)
    assert events == [NoteEvent("a", 0.0, 2.0)]


def test_notes_with_nothing_drawn_to_highlight_are_ignored():
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])
    events = note_events(_timemap({"qstamp": 0, "on": ["a", "ghost"]},
                                  {"qstamp": 2, "off": ["a", "ghost"]}),
                         tempo, drawn_id={"a": "a"})
    assert [e.note_id for e in events] == ["a"]


def test_a_repeated_note_is_reported_against_the_note_on_the_page():
    """Verovio sounds a repeated section again under a suffixed id; both passes
    must highlight the one note that is actually engraved."""
    tempo = TempoMap([(0.0, DEFAULT_TEMPO)])
    events = note_events(_timemap({"qstamp": 0, "on": ["a"]},
                                  {"qstamp": 2, "off": ["a"], "on": ["a-rend2"]},
                                  {"qstamp": 4, "off": ["a-rend2"]}),
                         tempo, drawn_id={"a": "a", "a-rend2": "a"})
    assert [e.note_id for e in events] == ["a", "a"]
    assert [e.on for e in events] == [0.0, 1.0]


def _speeds(times, xs, fps):
    import numpy as np
    grid = np.asarray(times)
    return np.diff(np.asarray(xs)) / np.diff(grid)


def test_smoothing_leaves_an_already_even_scroll_alone():
    import numpy as np
    from src.scrollvideo.timing import smooth_scroll
    times = [i / 10 for i in range(101)]
    xs = [t * 500 for t in times]
    out_t, out_x = smooth_scroll(times, xs, fps=10, seconds=2.0)
    assert np.allclose(np.diff(out_x), np.diff(out_x)[0], rtol=1e-6)


def test_smoothing_evens_out_uneven_spacing():
    """A scroll that lurches between wide and narrow measures comes out steadier."""
    import numpy as np
    from src.scrollvideo.timing import smooth_scroll
    fps = 30
    times, xs, x = [], [], 0.0
    for i in range(40):                       # alternate slow and fast half-seconds
        times.append(i * 0.5)
        xs.append(x)
        x += 100 if i % 2 else 900
    before = _speeds(times, xs, fps)
    _, smoothed = smooth_scroll(times, xs, fps=fps, seconds=2.0)
    after = np.diff(smoothed) * fps
    assert after.std() / after.mean() < before.std() / before.mean() / 3


def test_smoothing_never_scrolls_backwards_through_a_repeat():
    """The jump back to a repeated section is real motion, not jitter to average."""
    import numpy as np
    from src.scrollvideo.timing import smooth_scroll
    fps = 30
    times = [i * 0.1 for i in range(60)]
    xs = [i * 100.0 for i in range(30)] + [i * 100.0 for i in range(30)]   # jumps back
    _, smoothed = smooth_scroll(times, xs, fps=fps, seconds=1.0, page_width=3000.0)
    drops = [d for d in np.diff(smoothed) if d < -10]
    assert len(drops) == 1, "the repeat jump should stay a single clean jump"


def test_smoothing_can_be_turned_off():
    from src.scrollvideo.timing import smooth_scroll
    times, xs = [0.0, 1.0, 2.0], [0.0, 10.0, 100.0]
    assert smooth_scroll(times, xs, fps=30, seconds=0) == (times, xs)
