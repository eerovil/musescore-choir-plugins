"""Orchestration guards: what we refuse, and how we check we got it right."""

import mido
import numpy as np
import pytest
from lxml import etree

from src.scrollvideo.build import (ALIGNMENT_TOLERANCE, MAX_MARGIN_PERCENT,
                                   MIN_MARGIN_PERCENT, _margin_viewport,
                                   _vertical_view, alignment, build_videos,
                                   midi_onsets, unsupported_repeats, video_encoder)
from src.scrollvideo.video import NVIDIA_ENCODER, SOFTWARE_ENCODER
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


def test_zero_margin_adjustments_are_exactly_the_old_view():
    raw = np.arange(24, dtype=np.uint8).reshape(6, 4)
    start, end = _margin_viewport(6.0, 0, 0)
    result = _vertical_view(raw, visible_height=6.0, output_height=4,
                            px_per_unit=1.0, start=start, end=end, fill_value=0)
    assert (start, end) == (0.0, 6.0)
    np.testing.assert_array_equal(result, raw[:4])


def test_top_and_bottom_margin_can_add_white_space_independently():
    # Ten source units at 2 px/unit. +20% top and +30% bottom creates a virtual
    # 15-unit viewport: 4 white rows, 20 source rows, 6 white rows.
    raw = np.arange(20, dtype=np.uint8).reshape(20, 1)
    start, end = _margin_viewport(10.0, 20, 30)
    result = _vertical_view(raw, visible_height=10.0, output_height=30,
                            px_per_unit=2.0, start=start, end=end, fill_value=255)
    assert (start, end) == pytest.approx((-2.0, 13.0))
    assert np.all(result[:4] == 255)
    np.testing.assert_array_equal(result[4:24], raw)
    assert np.all(result[24:] == 255)


def test_negative_margins_crop_the_requested_edges():
    raw = np.arange(20, dtype=np.uint8).reshape(20, 1)
    start, end = _margin_viewport(10.0, -20, -30)
    result = _vertical_view(raw, visible_height=10.0, output_height=10,
                            px_per_unit=2.0, start=start, end=end, fill_value=255)
    assert (start, end) == pytest.approx((2.0, 7.0))
    np.testing.assert_array_equal(result, raw[4:14])


@pytest.mark.parametrize("top,bottom", [
    (MIN_MARGIN_PERCENT - 1, 0),
    (0, MAX_MARGIN_PERCENT + 1),
])
def test_margin_adjustments_reject_values_outside_the_safe_range(top, bottom):
    with pytest.raises(ValueError, match="video margin"):
        _margin_viewport(100.0, top, bottom)


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


def test_hardware_encoding_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setattr("src.scrollvideo.build.preferred_encoder",
                        lambda width, height: NVIDIA_ENCODER)
    assert video_encoder(True, 1280, 720) == NVIDIA_ENCODER
    assert video_encoder(False, 1280, 720) == SOFTWARE_ENCODER


# --------------------------------------------------------------------------- #
# The every-voice mix
# --------------------------------------------------------------------------- #

def test_the_combined_mix_is_named_all_and_is_not_a_part():
    """ALL is a mix, not a voice: it must never be looked up among the part names."""
    from src.scrollvideo.build import COMBINED
    from src.scrollvideo.audio import part_names, set_mix, FOCUS_VOLUME
    from lxml import etree

    root = etree.fromstring(
        b"<museScore><Score>"
        b"<Part><trackName>T1</trackName><Instrument><Channel/></Instrument></Part>"
        b"<Part><trackName>B1</trackName><Instrument><Channel/></Instrument></Part>"
        b"</Score></museScore>")
    assert COMBINED not in part_names(root)
    # focus=None, which is what ALL renders with, leaves every voice equally loud.
    set_mix(root, None)
    volumes = {c.get("value") for c in root.iter("controller")}
    assert volumes == {str(FOCUS_VOLUME)}
