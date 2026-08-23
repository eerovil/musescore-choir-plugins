"""Frames: highlights land on the right pixels and only on sounding notes."""

import numpy as np
import pytest

from src.scrollvideo.geometry import Layout, NoteGeom
from src.scrollvideo.timing import NoteEvent
from src.scrollvideo.video import HIGHLIGHT, mux, place, render
from .conftest import needs_ffmpeg


def _layout():
    return Layout(width=1000.0, height=500.0,
                  notes={"top": NoteGeom(100.0, 120.0, 100.0, 25.0),
                         "low": NoteGeom(300.0, 320.0, 300.0, 25.0)},
                  staff_tops=[100.0, 300.0])


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
def test_only_sounding_notes_are_lit(tmp_path):
    """The highlight appears while the note sounds and is gone after it stops."""
    import subprocess
    strip = np.full((120, 800, 3), 255, dtype=np.uint8)
    placed = place([NoteEvent("top", 0.0, 1.0)], _layout(), px_per_unit=0.24)
    out = tmp_path / "v.mp4"
    render(strip, placed, ([0.0, 3.0], [0.0, 0.0]), str(out),
           px_per_unit=0.24, width=320, fps=10, duration=3.0, crf=0)

    def frame_at(seconds):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(out), "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(120, 320, 3)

    def amber_pixels(frame):
        # the blend is towards HIGHLIGHT: red stays high while blue drops
        return int(((frame[:, :, 0].astype(int) - frame[:, :, 2].astype(int)) > 30).sum())

    assert amber_pixels(frame_at(0.5)) > 0, "note not highlighted while sounding"
    assert amber_pixels(frame_at(2.5)) == 0, "highlight outlived the note"


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
