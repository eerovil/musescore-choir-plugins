"""MuseScore's own clock: its play order (.mpos) and its MIDI tempo map."""

import os

import mido
import pytest
from lxml import etree

from src.score_export.clock import DEFAULT_TEMPO, TempoMap, read_mpos
from src.score_export.musescore import run_musescore
from src.score_export.score import prepare
from .conftest import FILES, needs_musescore


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
    assert tempo.seconds(2) == pytest.approx(1.0)
    assert tempo.seconds(3) == pytest.approx(2.5)
    assert tempo.seconds(4) == pytest.approx(3.0)
    assert tempo.changes == [0.0, 2.0, 3.0]


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


def test_the_mpos_export_is_read_in_played_order(tmp_path):
    path = tmp_path / "s.mpos"
    path.write_text(
        "<score><elements><element id='0'/><element id='1'/><element id='2'/></elements>"
        "<events><event elid='0' position='0'/><event elid='1' position='1000'/>"
        "<event elid='0' position='2000'/><event elid='2' position='3000'/></events>"
        "</score>")
    assert read_mpos(str(path)) == (3, [0, 1, 0, 2])


def test_an_mpos_with_no_play_order_is_refused(tmp_path):
    path = tmp_path / "s.mpos"
    path.write_text("<score><elements><element id='0'/></elements><events/></score>")
    with pytest.raises(NotImplementedError):
        read_mpos(str(path))


# --- the real thing, through MuseScore -------------------------------------

# Five bars made from fermata.mscx: D.S. al Coda plays 0 1 2 3 1 2 4, and D.C. al
# Fine plays 0 1 2 3 4 0 1. voltas.mscx is Kristallen den fina's shape (#374): a
# "2." ending written as a bracket that opens and never closes, which MuseScore
# still plays as printed.
ORDERS = {"dal_segno.mscx": [0, 1, 2, 3, 1, 2, 4],
          "da_capo.mscx": [0, 1, 2, 3, 4, 0, 1],
          "voltas.mscx": [0, 1, 2, 3, 4, 1, 2, 5, 6]}


@needs_musescore
@pytest.mark.parametrize("name", sorted(ORDERS))
def test_musescore_says_which_bars_it_plays_in_order(name, tmp_path):
    mpos = run_musescore(os.path.join(FILES, name), str(tmp_path / "s.mpos"))
    count, order = read_mpos(mpos)
    assert order == ORDERS[name]
    assert count == max(order) + 1


@needs_musescore
def test_a_supplied_opening_bpm_reaches_musescores_clock(fermata_mscx, tmp_path):
    source, _ = prepare(fermata_mscx, str(tmp_path), initial_bpm=80)
    tempo = TempoMap.from_midi(run_musescore(source, str(tmp_path / "at-80.mid")))
    assert tempo.seconds(1) == pytest.approx(60 / 80, abs=0.01)
    assert len(tempo.changes) > 1, "MuseScore's later fermata tempo changes must survive"


@needs_musescore
def test_a_fermata_plays_one_beat_longer_than_written(fermata_mscx, tmp_path):
    """#380: the fixture's quarter fermata was written 3x (4.00s -> 5.00s at 120
    bpm). Prepared, it holds one beat more: half a second, not a whole one."""
    source, _ = prepare(fermata_mscx, str(tmp_path))
    midi = mido.MidiFile(run_musescore(source, str(tmp_path / "s.mid")))
    assert midi.length == pytest.approx(4.5, abs=0.01)


@needs_musescore
def test_a_tempo_change_reaches_musescores_clock(fermata_mscx, tmp_path):
    """#387: the bar a change names plays at its BPM."""
    source, _ = prepare(fermata_mscx, str(tmp_path), initial_bpm=80,
                        tempo_changes=[{"measure": 2, "bpm": 140}])
    tempo = TempoMap.from_midi(run_musescore(source, str(tmp_path / "c.mid")))

    def bpm_at(quarter):
        return 60 * 0.5 / (tempo.seconds(quarter + 0.5) - tempo.seconds(quarter))

    assert round(bpm_at(0)) == 80
    assert round(bpm_at(4)) == 140
    assert round(bpm_at(7)) == 140


@needs_musescore
def test_a_worded_tempo_mark_plays_at_the_recorded_speed(fermata_mscx, tmp_path):
    """#404: "Adagio ♩ = c. 70" keeps its words and plays at the recorded 140,
    not at the 70 its own text says."""
    root = etree.parse(fermata_mscx).getroot()
    voice = root.find("./Score/Staff/Measure[2]/voice")
    voice.insert(0, etree.fromstring(
        "<Tempo><tempo>1.16666666667</tempo><followText>1</followText>"
        "<text>Adagio <sym>metNoteQuarterUp</sym> = c. 70</text></Tempo>"))
    worded = tmp_path / "worded.mscx"
    worded.write_bytes(etree.tostring(root))
    source, _ = prepare(str(worded), str(tmp_path), initial_bpm=80,
                        tempo_changes=[{"measure": 2, "bpm": 140}])

    words = etree.parse(source).getroot().find("./Score/Staff/Measure[2]/voice/Tempo/text")
    assert "Adagio" in "".join(words.itertext())
    tempo = TempoMap.from_midi(run_musescore(source, str(tmp_path / "c.mid")))
    assert round(60 * 0.5 / (tempo.seconds(4.5) - tempo.seconds(4))) == 140
