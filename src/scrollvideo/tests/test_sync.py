"""The whole point: the highlight runs on the same clock as the audio.

Verovio's timemap ignores MuseScore's fermata `timeStretch`, so a video driven
by verovio's own tstamps drifts against the exported audio — on the Hanget soi
score by 14 seconds over 4 minutes. These tests pin the fix: musical position
from verovio, seconds from MuseScore's MIDI tempo map.
"""

import os
import tempfile

import mido
import pytest

from src.scrollvideo import audio as audio_mod
from src.scrollvideo.engrave import engrave
from src.scrollvideo.timing import TempoMap, note_events
from .conftest import needs_musescore

pytestmark = needs_musescore


@pytest.fixture(scope="module")
def rendered(request):
    """Engrave the fermata fixture and export its MIDI, once."""
    score = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "test_files", "fermata.mscx")
    with tempfile.TemporaryDirectory() as tmp:
        musicxml = audio_mod.run_musescore(score, os.path.join(tmp, "s.musicxml"))
        midi_path = audio_mod.run_musescore(score, os.path.join(tmp, "s.mid"))
        eng = engrave(musicxml)
        tempo = TempoMap.from_midi(midi_path)
        events = note_events(eng.timemap, tempo, eng.layout.notes)
        onsets = sorted({round(t, 3) for t in (e.on for e in events)})
        midi = mido.MidiFile(midi_path)
        played, now = set(), 0.0
        for msg in midi:
            now += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                played.add(round(now, 3))
        yield {"engraving": eng, "tempo": tempo, "events": events,
               "onsets": onsets, "played": sorted(played), "midi_length": midi.length}


def test_every_highlight_lands_on_a_note_musescore_actually_plays(rendered):
    played = rendered["played"]
    for onset in rendered["onsets"]:
        nearest = min(abs(onset - p) for p in played)
        assert nearest < 0.02, f"highlight at {onset}s is {nearest * 1000:.0f}ms off the audio"


def test_the_last_note_ends_with_the_audio(rendered):
    assert rendered["onsets"][-1] <= rendered["midi_length"]
    assert max(e.off for e in rendered["events"]) == pytest.approx(
        rendered["midi_length"], abs=0.5)


def test_verovios_own_clock_would_have_drifted(rendered):
    """Guards the design decision: if someone swaps the MIDI tempo map back out
    for verovio's tstamps, this is what they would be shipping."""
    verovio_end = max(e["tstamp"] for e in rendered["engraving"].timemap) / 1000
    ours_end = max(e.off for e in rendered["events"])
    assert ours_end - verovio_end == pytest.approx(1.0, abs=0.05), (
        "the fixture's 3x fermata should stretch a quarter note from 0.5s to 1.5s, "
        "which verovio's own clock does not do")


def test_the_fermata_note_is_held_three_times_as_long(rendered):
    """The stretched note's own duration, not just the notes after it."""
    durations = sorted(e.off - e.on for e in rendered["events"])
    assert durations[-1] == pytest.approx(1.5, abs=0.05)
