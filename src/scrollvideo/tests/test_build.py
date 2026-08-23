"""Orchestration guards: what we refuse, and how we check we got it right."""

import mido
import pytest
from lxml import etree

from src.scrollvideo.build import (ALIGNMENT_TOLERANCE, alignment, build_videos,
                                   midi_onsets, unsupported_repeats)
from src.scrollvideo.timing import NoteEvent

PLAIN = "<museScore><Score><Staff id='1'><Measure/></Staff></Score></museScore>"
WITH_VOLTA = ("<museScore><Score><Staff id='1'><Measure>"
              "<Volta><endings>1</endings></Volta></Measure></Staff></Score></museScore>")
WITH_REPEAT = ("<museScore><Score><Staff id='1'><Measure>"
               "<startRepeat/></Measure></Staff></Score></museScore>")
WITH_JUMP = ("<museScore><Score><Staff id='1'><Measure>"
             "<Jump><jumpTo>start</jumpTo></Jump></Measure></Staff></Score></museScore>")


def test_a_plain_score_has_nothing_to_refuse():
    assert unsupported_repeats(etree.fromstring(PLAIN)) == []


@pytest.mark.parametrize("xml", [WITH_VOLTA, WITH_REPEAT])
def test_section_repeats_and_voltas_are_supported(xml):
    """Verovio expands these itself, so they render — the repeat-pass notes are
    mapped back to the notes drawn on the page."""
    assert unsupported_repeats(etree.fromstring(xml)) == []


WITH_MARKER = ("<museScore><Score><Staff id='1'><Measure>"
               "<Marker><label>fine</label></Marker></Measure></Staff></Score></museScore>")


def test_a_marker_without_a_jump_changes_nothing():
    """Segno/coda/fine labels alone don't alter playback, so they don't block."""
    assert unsupported_repeats(etree.fromstring(WITH_MARKER)) == []


def test_a_dc_jump_is_refused(tmp_path):
    """Verovio does not follow D.C./D.S., so the video would drift."""
    assert unsupported_repeats(etree.fromstring(WITH_JUMP)) == ["Jump"]
    score = tmp_path / "score.mscx"
    score.write_text(WITH_JUMP)
    with pytest.raises(NotImplementedError, match="Jump"):
        build_videos(str(score), str(tmp_path / "out"))


def _midi(tmp_path, onsets_in_beats):
    """A 120bpm MIDI striking a note at each given beat."""
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    previous = 0
    for beat in onsets_in_beats:
        tick = int(beat * 480)
        track.append(mido.Message("note_on", note=60, velocity=64, time=tick - previous))
        track.append(mido.Message("note_off", note=60, velocity=0, time=0))
        previous = tick
    path = tmp_path / "a.mid"
    midi.save(path)
    return str(path)


def test_midi_onsets_are_read_in_seconds(tmp_path):
    assert midi_onsets(_midi(tmp_path, [0, 2, 4])) == pytest.approx([0.0, 1.0, 2.0])


def test_alignment_is_full_when_every_played_note_is_highlighted(tmp_path):
    path = _midi(tmp_path, [0, 2, 4])
    events = [NoteEvent("a", 0.0, 1.0), NoteEvent("b", 1.0, 2.0), NoteEvent("c", 2.0, 3.0)]
    assert alignment(events, path) == 1.0


def test_alignment_falls_when_the_audio_plays_notes_nothing_lights_up_for(tmp_path):
    path = _midi(tmp_path, [0, 2, 4])
    missing_the_last = [NoteEvent("a", 0.0, 1.0), NoteEvent("b", 1.0, 2.0)]
    assert alignment(missing_the_last, path) == pytest.approx(2 / 3)


def test_extra_highlights_do_not_count_against_alignment(tmp_path):
    """A tie continuation lights the tied notehead but has no note-on of its own;
    that is correct, not drift."""
    path = _midi(tmp_path, [0, 2, 4])
    with_a_tie = [NoteEvent("a", 0.0, 1.0), NoteEvent("a-tied", 0.5, 1.0),
                  NoteEvent("b", 1.0, 2.0), NoteEvent("c", 2.0, 3.0)]
    assert alignment(with_a_tie, path) == 1.0


def test_a_wholesale_drift_is_still_caught(tmp_path):
    path = _midi(tmp_path, [0, 2, 4])
    late = [NoteEvent(n, t + 10 * ALIGNMENT_TOLERANCE, t + 1)
            for n, t in (("a", 0.0), ("b", 1.0), ("c", 2.0))]
    assert alignment(late, path) < 0.5
