"""A repeat that jumps back across a meter change keeps the audio's clock (#313).

When a part rests through the bar the repeat jumps back to, verovio times that
whole-bar rest in the meter in force *at* the jump: a 7/4 bar repeating to a 4/4
one gets its 4/4 bar replayed seven quarters long, and every highlight after it
comes three quarters late. `retime_repeats` puts each bar back at its MusicXML
length.
"""

import os

from src.scrollvideo import audio as audio_mod
from src.scrollvideo.build import ALIGNMENT_REQUIRED, alignment
from src.scrollvideo.engrave import engrave, retime_repeats
from src.scrollvideo.timing import TempoMap, note_events
from .conftest import needs_musescore


def _bar(number, beats, notes, attributes="", barline=""):
    """One bar of quarter notes, or a whole-bar rest when `notes` is empty."""
    first = f"<attributes>{attributes}<time><beats>{beats}</beats><beat-type>4</beat-type></time></attributes>"
    body = "".join(
        f"<note><pitch><step>{step}</step><octave>4</octave></pitch>"
        f"<duration>1</duration><type>quarter</type></note>" for step in notes)
    if not notes:
        body = f'<note><rest measure="yes"/><duration>{beats}</duration></note>'
    return f'<measure number="{number}">{first}{body}{barline}</measure>'


BACKWARD = ('<barline location="right"><bar-style>light-heavy</bar-style>'
            '<repeat direction="backward"/></barline>')
OPENING = ("<divisions>1</divisions><key><fifths>0</fifths></key>")


def _score(tmp_path, *parts):
    """One part per sequence of bars."""
    names = "".join(f'<score-part id="P{i}"><part-name>P{i}</part-name></score-part>'
                    for i in range(1, len(parts) + 1))
    music = "".join(f'<part id="P{i}">{"".join(bars)}</part>'
                    for i, bars in enumerate(parts, 1))
    xml = ('<?xml version="1.0"?><score-partwise version="3.1">'
           f"<part-list>{names}</part-list>{music}</score-partwise>")
    path = tmp_path / "score.musicxml"
    path.write_text(xml)
    return str(path)


def _meter_across_a_repeat(tmp_path):
    """4/4, then 7/4 repeating back to the start, then 4/4; the lower part rests in bar 1."""
    return _score(tmp_path,
                  [_bar(1, 4, "CDEF", OPENING),
                   _bar(2, 7, "GABCDEF", barline=BACKWARD),
                   _bar(3, 4, "GABC")],
                  [_bar(1, 4, "", OPENING),
                   _bar(2, 7, "GABCDEF", barline=BACKWARD),
                   _bar(3, 4, "GABC")])


def _bar_starts(timemap):
    return [(e["measureOn"], e["qstamp"]) for e in timemap if "measureOn" in e]


def test_the_bar_after_the_jump_is_played_at_its_own_length(tmp_path):
    starts = [q for _, q in _bar_starts(engrave(_meter_across_a_repeat(tmp_path)).timemap)]
    # bar 1, bar 2, bar 1 again, bar 2 again, bar 3 — verovio alone says 18 and 25.
    assert starts == [0, 4, 11, 15, 22]


def test_the_first_pass_is_what_verovio_said(tmp_path):
    eng = engrave(_meter_across_a_repeat(tmp_path))
    assert [q for _, q in _bar_starts(eng.timemap)][:3] == [0, 4, 11]


def test_a_score_without_repeats_is_untouched(fermata_musicxml):
    eng = engrave(fermata_musicxml)
    import verovio
    from src.scrollvideo.engrave import OPTIONS, RESOURCE_PATH
    tk = verovio.toolkit(False)
    tk.setResourcePath(RESOURCE_PATH)
    tk.setOptions(OPTIONS)
    tk.loadFile(fermata_musicxml)
    tk.renderToSVG(1)
    assert eng.timemap == tk.renderToTimemap({"includeMeasures": True, "includeRests": True})


def test_a_bar_count_that_does_not_agree_leaves_the_timemap_alone():
    timemap = [{"qstamp": 0, "measureOn": "a"}, {"qstamp": 4, "measureOn": "b"},
               {"qstamp": 11, "measureOn": "a-rend2"}, {"qstamp": 18, "measureOn": "b-rend2"}]
    assert retime_repeats(timemap, [4, 7, 4]) is timemap
    assert retime_repeats(timemap, None) is timemap


def test_notes_inside_a_bar_move_with_it():
    timemap = [{"qstamp": 0, "measureOn": "a", "on": ["n1"]},
               {"qstamp": 4, "measureOn": "b", "on": ["n2"]},
               {"qstamp": 11, "measureOn": "a-rend2", "on": ["n1-rend2"]},
               {"qstamp": 18, "measureOn": "b-rend2", "on": ["n2-rend2"]},
               {"qstamp": 20, "on": ["n3-rend2"]}]
    out = retime_repeats(timemap, [4, 7])
    assert [e["qstamp"] for e in out] == [0, 4, 11, 15, 17]


@needs_musescore
def test_the_repeated_score_lines_up_with_musescores_audio(tmp_path):
    musicxml = _meter_across_a_repeat(tmp_path)
    midi = audio_mod.run_musescore(musicxml, os.path.join(tmp_path, "s.mid"))
    eng = engrave(musicxml)
    notes = note_events(eng.timemap, TempoMap.from_midi(midi), eng.drawn_id)
    assert alignment(notes, midi) >= ALIGNMENT_REQUIRED
