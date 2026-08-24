"""Frames: highlights land on the right pixels and only on sounding notes."""

import numpy as np
import pytest

from src.scrollvideo.geometry import Layout, NoteGeom, RestGeom
from src.scrollvideo.timing import NoteEvent
from src.scrollvideo.video import (HIGHLIGHT, Placed, blend_beat_marker,
                                   latest_onset_index, mux, place, render)
from .conftest import needs_ffmpeg


def _layout():
    return Layout(width=1000.0, height=500.0,
                  notes={"top": NoteGeom(100.0, 120.0, 100.0, 25.0),
                         "low": NoteGeom(300.0, 320.0, 300.0, 25.0)},
                  staff_tops=[100.0, 300.0],
                  rests={"rest": RestGeom(300.0, 120.0, 100.0, 25.0)})


def test_place_maps_notes_to_their_own_staff():
    placed = {p.staff: p for p in place([NoteEvent("top", 0, 1), NoteEvent("low", 0, 1)],
                                        _layout(), px_per_unit=1.0)}
    assert set(placed) == {0, 1}
    assert placed[0].y0 < placed[0].y1 <= placed[1].y0


def test_place_scales_with_the_output_size():
    (small,) = place([NoteEvent("top", 0, 1)], _layout(), px_per_unit=0.5)
    (big,) = place([NoteEvent("top", 0, 1)], _layout(), px_per_unit=2.0)
    assert big.x0 == pytest.approx(small.x0 * 4, abs=2)


def test_place_skips_notes_without_geometry():
    assert place([NoteEvent("ghost", 0, 1)], _layout(), px_per_unit=1.0) == []


def test_place_maps_rests_and_can_leave_out_the_spacer_staff():
    layout = _layout()
    events = [NoteEvent("rest", 0, 1), NoteEvent("low", 0, 1)]
    placed = place(events, layout, px_per_unit=1.0, staff_limit=1)
    assert len(placed) == 1
    assert placed[0].staff == 0
    assert (placed[0].x0 + placed[0].x1) / 2 == pytest.approx(315.0)


@pytest.mark.parametrize(("time", "expected"), [
    (0.49, None),
    (0.5, 0),
    (1.99, 0),
    (2.0, 1),
    (9.0, 1),
])
def test_beat_marker_uses_the_last_onset_at_or_before_the_frame(time, expected):
    assert latest_onset_index([0.5, 2.0], time) == expected


def test_beat_marker_blends_full_height_two_noteheads_wide_and_centred():
    frame = np.full((6, 50, 3), 255, dtype=np.uint8)
    blend_beat_marker(frame, Placed(0, 1, 20, 30, 2, 4, 0), left=0)

    assert np.all(frame[:, :15] == 255)
    assert np.all(frame[:, 35:] == 255)
    assert np.all(frame[:, 15:35] == frame[0, 15])
    assert np.all(frame[:, 15:35] > 200), "the engraving must remain readable through the wash"


@needs_ffmpeg
def test_render_writes_a_playable_video_of_the_expected_length(tmp_path):
    import subprocess
    strip = np.full((120, 800, 3), 255, dtype=np.uint8)
    placed = place([NoteEvent("top", 0.0, 1.0)], _layout(), px_per_unit=0.24)
    out = tmp_path / "v.mp4"
    render(strip, placed, ([0.0, 2.0], [0.0, 1000.0]), str(out),
           px_per_unit=0.24, width=320, fps=10, duration=2.0)
    assert out.exists()
    seconds = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True).stdout.strip())
    assert seconds == pytest.approx(2.0, abs=0.2)


@needs_ffmpeg
def test_render_is_byte_deterministic_with_the_beat_marker(tmp_path):
    strip = np.full((60, 160, 3), 255, dtype=np.uint8)
    placed = [Placed(0.0, 0.5, 15, 25, 10, 20, 0),
              Placed(0.5, 1.0, 75, 85, 30, 40, 1)]
    outputs = [tmp_path / "first.mp4", tmp_path / "second.mp4"]

    for out in outputs:
        render(strip, placed, ([0.0, 1.0], [0.0, 0.0]), str(out), px_per_unit=1.0,
               width=160, fps=10, duration=1.0, crf=0, playhead=0.0,
               coverage=np.zeros((60, 160), dtype=np.uint8))

    assert outputs[0].read_bytes() == outputs[1].read_bytes()


def _frame_reader(path, height, width):
    import subprocess

    def frame_at(seconds):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(path), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
    return frame_at


def _blue_pixels(frame):
    return int(((frame[:, :, 2].astype(int) - frame[:, :, 0].astype(int)) > 40).sum())


@needs_ffmpeg
def test_only_sounding_notes_are_lit(tmp_path):
    """The highlight appears while the note sounds and is gone after it stops."""
    strip = np.full((120, 800, 3), 255, dtype=np.uint8)
    coverage = np.zeros((120, 800), dtype=np.uint8)
    coverage[20:40, 20:40] = 255                    # a "note glyph" to recolour
    strip[20:40, 20:40] = 0
    placed = place([NoteEvent("top", 0.0, 1.0)], _layout(), px_per_unit=0.24)
    out = tmp_path / "v.mp4"
    render(strip, placed, ([0.0, 3.0], [0.0, 0.0]), str(out), px_per_unit=0.24,
           width=320, fps=10, duration=3.0, crf=0, coverage=coverage)

    frame_at = _frame_reader(out, 120, 320)
    assert _blue_pixels(frame_at(0.5)) > 0, "note not highlighted while sounding"
    assert _blue_pixels(frame_at(2.5)) == 0, "highlight outlived the note"


@needs_ffmpeg
def test_playing_rest_turns_blue_and_moves_the_marker(tmp_path):
    strip = np.full((120, 800, 3), 255, dtype=np.uint8)
    coverage = np.zeros((120, 800), dtype=np.uint8)
    coverage[24:32, 24:32] = 255
    coverage[24:32, 72:80] = 255
    strip[24:32, 24:32] = 0
    strip[24:32, 72:80] = 0
    placed = place([NoteEvent("top", 0.0, 1.0), NoteEvent("rest", 1.0, 2.0)],
                   _layout(), px_per_unit=0.24)
    out = tmp_path / "rest.mp4"
    render(strip, placed, ([0.0, 2.0], [0.0, 0.0]), str(out), px_per_unit=0.24,
           width=320, fps=10, duration=2.0, crf=0, coverage=coverage, playhead=0.0)

    frame = _frame_reader(out, 120, 320)(1.5)
    assert _blue_pixels(frame[24:32, 72:80]) > 0, "rest glyph did not turn blue"
    assert _blue_pixels(frame[24:32, 24:32]) == 0, "finished note stayed highlighted"
    assert frame[0, 76].max() < 250, "beat marker did not move to the rest"
    assert frame[0, 28].min() > 250, "beat marker stayed on the finished note"


@needs_ffmpeg
def test_the_note_itself_turns_blue_rather_than_a_box_over_it(tmp_path):
    """Only pixels the glyph covers change; the white around it stays white."""
    strip = np.full((120, 800, 3), 255, dtype=np.uint8)
    coverage = np.zeros((120, 800), dtype=np.uint8)
    coverage[24:32, 24:32] = 255                    # glyph: a small square
    strip[24:32, 24:32] = 0
    placed = place([NoteEvent("top", 0.0, 2.0)], _layout(), px_per_unit=0.24)
    out = tmp_path / "v.mp4"
    render(strip, placed, ([0.0, 2.0], [0.0, 0.0]), str(out), px_per_unit=0.24,
           width=320, fps=10, duration=1.0, crf=0, coverage=coverage, playhead=0.0)

    # playhead=0 keeps strip and frame columns aligned, so the glyph is where we put it
    frame = _frame_reader(out, 120, 320)(0.5)
    glyph = frame[24:32, 24:32]
    assert _blue_pixels(glyph) > 0, "the glyph should be blue"
    # the note's box is much larger than the glyph; the rest of it must be untouched
    box = placed[0]
    around = frame[max(box.y0, 0):box.y1, max(box.x0, 0):box.x1].copy()
    around[24 - max(box.y0, 0):32 - max(box.y0, 0), :] = 255
    assert _blue_pixels(around) == 0, "colour leaked outside the glyph — that is a box"


@needs_ffmpeg
def test_beat_marker_is_full_height_translucent_and_centred_on_latest_note(tmp_path):
    strip = np.full((60, 160, 3), 255, dtype=np.uint8)
    placed = [
        # Both notes have ten-pixel heads; the marker should jump from x=20 to x=80.
        # Their lifetimes do not control the marker: only the latest onset does.
        Placed(0.0, 0.1, 15, 25, 10, 20, 0),
        Placed(1.0, 1.1, 75, 85, 30, 40, 1),
    ]
    out = tmp_path / "marker.mp4"
    render(strip, placed, ([0.0, 2.0], [0.0, 0.0]), str(out), px_per_unit=1.0,
           width=160, fps=10, duration=2.0, crf=0, playhead=0.0,
           coverage=np.zeros((60, 160), dtype=np.uint8))

    frame_at = _frame_reader(out, 60, 160)
    first = frame_at(0.5)
    second = frame_at(1.5)
    assert first[:, 10:30].max() < 250
    assert first[:, 70:90].min() > 250
    assert second[:, 10:30].min() > 250
    assert second[:, 70:90].max() < 250
    assert second[:, 70:90].min() > 200, "the engraving must remain readable through the wash"


@needs_ffmpeg
def test_mux_puts_audio_on_a_video_without_touching_the_picture(tmp_path):
    """Every voice shares one encode; only the audio differs. The video stream
    must come through byte-identical, or we are paying to re-encode it."""
    import subprocess
    strip = np.full((120, 800, 3), 255, dtype=np.uint8)
    placed = place([NoteEvent("top", 0.0, 1.0)], _layout(), px_per_unit=0.24)
    silent = tmp_path / "silent.mp4"
    render(strip, placed, ([0.0, 2.0], [0.0, 1000.0]), str(silent),
           px_per_unit=0.24, width=320, fps=10, duration=2.0)

    tone = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2", str(tone)], check=True)
    out = tmp_path / "with_audio.mp4"
    mux(str(silent), str(tone), str(out))

    def video_stream_md5(path):
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy",
             "-f", "md5", "-"], capture_output=True, text=True).stdout.strip()

    assert video_stream_md5(out) == video_stream_md5(silent)
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
         str(out)], capture_output=True, text=True).stdout.split()
    assert "audio" in " ".join(streams) and "video" in " ".join(streams)
