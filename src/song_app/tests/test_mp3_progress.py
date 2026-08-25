"""The screen-recording renderer reports its otherwise quiet MP3 export phase."""

import os
from pathlib import Path

# create_video validates these at import time. Tests replace all external programs,
# so harmless temporary defaults are enough here.
os.environ.setdefault("MUSESCORE_EXPORT_PATH", "/tmp")
os.environ.setdefault("VIDEO_EXPORT_PATH", "/tmp")

from src.stemmanauha import create_video


def test_recording_pipeline_forwards_progress_to_mp3_export(tmp_path, monkeypatch):
    song = tmp_path / "demo"
    song.mkdir()
    media = song / "media"
    media.mkdir()
    mp3 = media / "demo ALL.mp3"
    mp3.write_bytes(b"audio")
    seen = {}
    progress_lines = []

    def fake_export(song_dir, redo=False, log=None, progress=None):
        seen.update(song_dir=song_dir, redo=redo, log=log, progress=progress)
        progress("Creating MP3 audio: 1/2")
        return mp3

    monkeypatch.setattr(create_video, "export_mp3_from_musescore", fake_export)
    monkeypatch.setattr(create_video, "record_video", lambda *args, **kwargs: None)
    monkeypatch.setattr(create_video, "merge_mp3_to_video", lambda *args, **kwargs: [])

    create_video.run(
        song_dir=str(song), redo_mp3=True,
        log=lambda _message: None, progress=progress_lines.append)

    assert seen["song_dir"] == str(song)
    assert seen["redo"] is True
    assert callable(seen["progress"])
    assert progress_lines == ["Creating MP3 audio: 1/2"]


def test_mp3_export_reports_start_wait_and_completion(tmp_path, monkeypatch):
    song = tmp_path / "demo"
    song.mkdir()
    exported = tmp_path / "exports"
    exported.mkdir()
    all_mp3 = exported / "demo ALL.mp3"
    all_mp3.write_bytes(b"audio")
    lines = []
    logs = []

    monkeypatch.setattr(create_video, "MUSESCORE_EXPORT_PATH", str(exported))
    monkeypatch.setattr(create_video, "export_path", exported)
    monkeypatch.setattr(create_video.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(create_video.time, "sleep", lambda _seconds: None)

    def fake_wait(export_dir, timeout=120, check_interval=1, progress=None):
        assert Path(export_dir) == exported
        progress("Creating MP3 audio: waiting 4s")
        return all_mp3

    monkeypatch.setattr(create_video, "wait_for_all_mp3", fake_wait)
    monkeypatch.setattr(create_video, "get_filtered_mp3_files", lambda _base: [all_mp3])

    result = create_video.export_mp3_from_musescore(
        str(song), redo=True, log=logs.append, progress=lines.append)

    assert Path(result) == song / "media" / "demo ALL.mp3"
    assert logs == ["Creating MP3 audio in MuseScore…", "MP3 audio ready."]
    assert lines == [
        "Creating MP3 audio: starting MuseScore export",
        "Creating MP3 audio: waiting 4s",
        "Creating MP3 audio: 100%",
    ]