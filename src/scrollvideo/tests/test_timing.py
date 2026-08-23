"""The clock: quarter-note positions -> seconds, following MuseScore's tempo map."""

import mido
import pytest

from src.scrollvideo.timing import DEFAULT_TEMPO, TempoMap, note_events, NoteEvent


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
