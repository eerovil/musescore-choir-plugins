"""Publish on MuseScore 4: the setup check, and a score MuseScore 4 saved.

The fork runs on MuseScore 4 (joyweshare_project.md 2.7). His Publish was written
for MuseScore 3. These run the real MuseScore named by MUSESCORE_CLI_PATH, and skip
without one; on MuseScore 3 they still hold.
"""

import os
import subprocess

import mido
import pytest

from src import musescore_cli
from src.score_export.tests.conftest import needs_musescore
from src.song_app import check, publish

FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "score_export", "tests",
                        "test_files")


@needs_musescore
def test_the_setup_check_accepts_the_musescore_on_this_machine():
    found = check.musescore()
    assert found.status == check.OK, found.detail


@needs_musescore
def test_a_score_saved_by_this_musescore_publishes_with_its_clock(tmp_path):
    """MuseScore saves the score (as MuseScore 4 saves a cleaned score after a hand
    correction), then Publish exports MusicXML, play order and MIDI from it, and
    the play order agrees with the MIDI's clock within Publish's 10 ms."""
    saved = str(tmp_path / "saved.mscx")
    musescore_cli.export(os.getenv("MUSESCORE_CLI_PATH") or "musescore3",
                         os.path.join(FIXTURES, "repeat.mscx"), saved, timeout=300)
    out = tmp_path / "bundle"
    out.mkdir()
    bundle = publish.build_bundle(saved, str(out))
    assert bundle.parts == ["S1", "A1"]
    assert os.path.exists(os.path.join(str(out), "score.mpos"))
    assert open(bundle.midi, "rb").read(4) == b"MThd"
    tracks = [t.name.rstrip("\x00") for t in mido.MidiFile(bundle.midi).tracks if t.name]
    assert tracks == ["S1", "A1"]
    starts = [p[1] for p in bundle.timing["points"] if p[2] == 0.0]
    assert starts == [0, 1, 0, 1], "the repeat is played, so the timing has it"
