"""Following a D.C./D.S. jump in MuseScore's played order.

The first half is the rebuild on hand-written timemaps, so each rule is pinned
without MuseScore. The second half is the property that matters, on two small
committed scores: every highlight lands on a note MuseScore actually plays, and
the scroll lands at each jump instead of sweeping across the page.
"""

import os
import tempfile

import mido
import numpy as np
import pytest
from lxml import etree

from src.scrollvideo import build
from src.scrollvideo.playorder import has_jumps, read_mpos, unrolled_timemap
from src.scrollvideo.timing import TempoMap, note_events
from .conftest import FILES, needs_musescore

BARS = ("m0", "m1", "m2", "m3")


def _timemap():
    """Four 4/4 bars of one quarter-note voice each, as verovio would time them
    straight through (no jump followed)."""
    entries = []
    for bar in range(4):
        for beat in range(4):
            q = bar * 4 + beat
            entry = {"qstamp": float(q), "on": [f"n{q}"]}
            if q:
                entry["off"] = [f"n{q - 1}"]
            if beat == 0:
                entry["measureOn"] = BARS[bar]
            entries.append(entry)
    entries.append({"qstamp": 16.0, "off": ["n15"]})
    return entries


DRAWN = {f"n{q}": f"n{q}" for q in range(16)}
PRINTED = {bar: bar for bar in BARS}


def _played(timemap, drawn):
    """(drawn note, on, off) in quarters, in the order they sound."""
    events = note_events(timemap, TempoMap([(0.0, 1_000_000)]), drawn)
    return [(e.note_id, e.on, e.off) for e in events]


def test_bars_are_laid_out_in_musescores_order():
    """D.C. al Fine after bar 4, Fine at the end of bar 2: 0 1 2 3 0 1."""
    timemap, drawn, cuts = unrolled_timemap(_timemap(), DRAWN, BARS, PRINTED, 4,
                                            [0, 1, 2, 3, 0, 1])
    played = _played(timemap, drawn)
    assert [p[0] for p in played] == [f"n{q}" for q in [*range(16), *range(8)]]
    assert [p[1] for p in played] == [float(q) for q in range(24)]
    assert cuts == [16.0]


def test_a_dal_segno_al_coda_cuts_back_and_then_forward():
    _, _, cuts = unrolled_timemap(_timemap(), DRAWN, BARS, PRINTED, 4,
                                  [0, 1, 2, 1, 3])
    assert cuts == [12.0, 16.0]


def test_a_note_held_into_a_bar_the_jump_skips_stops_at_the_barline():
    timemap = _timemap()
    # n7 (the last beat of bar 2) is held through bar 3's first beat.
    timemap[8]["off"] = []
    timemap[9]["off"] = ["n8", "n7"]
    timemap, drawn, _ = unrolled_timemap(timemap, DRAWN, BARS, PRINTED, 4, [0, 1, 3])
    held = [p for p in _played(timemap, drawn) if p[0] == "n7"]
    assert held == [("n7", 7.0, 8.0)]


def test_a_bar_played_twice_lights_the_note_on_the_page_twice():
    timemap, drawn, _ = unrolled_timemap(_timemap(), DRAWN, BARS, PRINTED, 4,
                                         [0, 0])
    played = _played(timemap, drawn)
    assert [p[0] for p in played] == ["n0", "n1", "n2", "n3"] * 2


def test_a_bar_first_timed_in_a_repeat_pass_maps_back_to_the_page():
    timemap = _timemap()
    timemap[4]["measureOn"] = "m1-rend2"
    timemap, drawn, _ = unrolled_timemap(timemap, DRAWN, BARS,
                                         {**PRINTED, "m1-rend2": "m1"}, 4, [0, 1])
    assert len(_played(timemap, drawn)) == 8


def test_a_different_bar_count_is_refused():
    with pytest.raises(NotImplementedError, match="5 bars"):
        unrolled_timemap(_timemap(), DRAWN, BARS, PRINTED, 5, [0, 1])


def test_a_bar_the_engraving_never_timed_is_refused():
    timemap = [e for e in _timemap() if e.get("measureOn") != "m3"]
    with pytest.raises(NotImplementedError, match="bar\\(s\\) 4"):
        unrolled_timemap(timemap, DRAWN, BARS, PRINTED, 4, [0, 1, 2, 3])


def test_only_a_jump_chooses_musescores_order():
    """A Marker alone is a label; repeats and voltas stay verovio's to expand."""
    assert has_jumps(etree.fromstring(
        "<museScore><Measure><Jump><jumpTo>start</jumpTo></Jump></Measure></museScore>"))
    for plain in ("<museScore><Measure><Marker><label>fine</label></Marker></Measure>"
                  "</museScore>",
                  "<museScore><Measure><startRepeat/></Measure></museScore>"):
        assert not has_jumps(etree.fromstring(plain))


def test_the_mpos_export_is_read_in_played_order(tmp_path):
    path = tmp_path / "s.mpos"
    path.write_text(
        "<score><elements><element id='0'/><element id='1'/><element id='2'/></elements>"
        "<events><event elid='0' position='0'/><event elid='1' position='1000'/>"
        "<event elid='0' position='2000'/><event elid='2' position='3000'/></events>"
        "</score>")
    assert read_mpos(str(path)) == (3, [0, 1, 0, 2])


# --- the real thing, through MuseScore -------------------------------------

# Five bars made from fermata.mscx: D.S. al Coda plays 0 1 2 3 1 2 4, and
# D.C. al Fine plays 0 1 2 3 4 0 1 (MuseScore's own .mpos says so).
SCORES = {"dal_segno.mscx": 2, "da_capo.mscx": 1}


@pytest.fixture(scope="module", params=sorted(SCORES))
def jumped(request):
    with tempfile.TemporaryDirectory() as tmp:
        ready = build.prepare(os.path.join(FILES, request.param), tmp, fps=60)
        midi = mido.MidiFile(ready.midi)
        played, now = [], 0.0
        for message in midi:
            now += message.time
            if message.type == "note_on" and message.velocity > 0:
                played.append(now)
        yield request.param, ready, played, midi.length


@needs_musescore
def test_every_highlight_lands_on_a_note_musescore_plays(jumped):
    _, ready, played, _ = jumped
    for event in ready.notes:
        nearest = min(abs(event.on - p) for p in played)
        assert nearest < 0.02, f"highlight at {event.on:.3f}s is {nearest * 1000:.0f}ms off"


@needs_musescore
def test_the_last_highlight_ends_with_the_audio(jumped):
    _, ready, _, length = jumped
    assert max(e.off for e in ready.notes) == pytest.approx(length, abs=0.5)


@needs_musescore
def test_the_scroll_lands_at_each_jump_instead_of_sliding(jumped):
    """Each jump is one step within a frame; nothing sweeps across the bars between."""
    name, ready, _, _ = jumped
    assert len(ready.cuts) == SCORES[name]
    times, xs = (np.asarray(a) for a in ready.anchors)
    assert np.all(np.diff(times) > 0), "time must never step backwards"
    page = ready.layout.width
    big = [i for i, step in enumerate(np.diff(xs)) if abs(step) > 0.1 * page]
    assert len(big) == SCORES[name]
    for i in big:
        assert times[i + 1] - times[i] < 1 / 60
        assert any(abs(times[i + 1] - cut) < 1 / 60 for cut in ready.cuts)
