"""The `dropnote` and `addnote` fixes (#358): one note off a chord, or one on.

A page often prints an optional note in brackets — a low octave for the basses, a
divisi — and the scan reads it as a real chord note, so the practice track sings
both. Before these kinds the only record was a sentence nobody could replay. The
score below is shaped like Love Me Tender's lower bass (Eb2 with an optional Eb3, in
A flat): what these pin is that the note goes and nothing else does — the chord's
length, its other notes and its words stay — that a tie on it goes both ways, that
an added note is spelt the way the page spells it, and that both are as strict about
`from` as every other kind.
"""
import json
import os

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import (FixError, _measure, _spell, apply_fixes,
                                               bar_tokens)

# Eb is spelt tpc 11, Ab 10, C 14 in A flat major.
_TPC = {39: 11, 51: 11, 48: 14, 44: 10}

TIE_OUT = ('<Spanner type="Tie"><Tie></Tie><next><location><measures>1</measures>'
           '<fractions>-1/2</fractions></location></next></Spanner>')
TIE_IN = ('<Spanner type="Tie"><prev><location><measures>-1</measures>'
          '<fractions>1/2</fractions></location></prev></Spanner>')


def _note(pitch, tie=""):
    return f"<Note>{tie}<pitch>{pitch}</pitch><tpc>{_TPC[pitch]}</tpc></Note>"


def _chord(dur, *notes, words=""):
    lyric = f"<Lyrics><text>{words}</text></Lyrics>" if words else ""
    return f"<Chord><durationType>{dur}</durationType>{lyric}{''.join(notes)}</Chord>"


HEAD = ("<Clef><concertClefType>F</concertClefType></Clef>"
        "<KeySig><accidental>-4</accidental></KeySig>"
        "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>")

BARS = [
    HEAD + _chord("quarter", _note(39), _note(51), words="love")
    + _chord("quarter", _note(48), words="me")
    + _chord("half", _note(39), _note(51, TIE_OUT), words="ten"),
    _chord("half", _note(39), _note(51, TIE_IN))
    + _chord("half", _note(44), words="der"),
]


def _score():
    measures = "".join(f"<Measure><voice>{bar}</voice></Measure>" for bar in BARS)
    return etree.fromstring(
        "<museScore version='3.02'><Score>"
        '<Part><trackName>B2</trackName><Staff id="1"/></Part>'
        f'<Staff id="1">{measures}</Staff></Score></museScore>')


@pytest.fixture
def root():
    return _score()


def _fix(root, kind, measure, **more):
    return {"kind": kind, "staff": 1, "measure": measure,
            "from": bar_tokens(root, 1, measure), "why": "read against the page", **more}


def _chords(root, measure):
    return _measure(root, 1, measure).findall("voice/Chord")


def _pitches(chord):
    return [int(n.findtext("pitch")) for n in chord.findall("Note")]


def _ties(root):
    return root.findall(".//Note/Spanner[@type='Tie']")


# --- dropnote -------------------------------------------------------------------

def test_dropnote_takes_one_note_off_and_keeps_the_chord(root):
    slots = lyric_txt.slot_counts(root)
    apply_fixes(root, [_fix(root, "dropnote", 1, index=0, pitch=51)])
    chord = _chords(root, 1)[0]
    assert _pitches(chord) == [39]
    assert chord.findtext("durationType") == "quarter"
    assert chord.findtext("Lyrics/text") == "love"
    assert bar_tokens(root, 1, 1)[0] == "quarter:39"
    assert lyric_txt.slot_counts(root) == slots
    assert len(_ties(root)) == 2  # the tie elsewhere is not touched


def test_dropnote_takes_the_tie_out_of_the_note_both_halves(root):
    apply_fixes(root, [_fix(root, "dropnote", 1, index=2, pitch=51)])
    assert _pitches(_chords(root, 1)[2]) == [39]
    assert _ties(root) == []
    assert _pitches(_chords(root, 2)[0]) == [39, 51]  # only the tie went from the next bar


def test_dropnote_takes_the_tie_into_the_note_both_halves(root):
    lines = apply_fixes(root, [_fix(root, "dropnote", 2, index=0, pitch=51)])
    assert _pitches(_chords(root, 2)[0]) == [39]
    assert _ties(root) == []
    assert _pitches(_chords(root, 1)[2]) == [39, 51]
    assert "Eb3" in lines[0] and "tie into it" in lines[0]


@pytest.mark.parametrize("measure,index,pitch,says", [
    (1, 1, 48, "only note"),
    (1, 0, 52, "no note at pitch 52"),
    (1, 3, 51, "no index 3"),
])
def test_dropnote_refuses(root, measure, index, pitch, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, "dropnote", measure, index=index, pitch=pitch)])


def test_dropnote_is_strict_about_from(root):
    stale = _fix(root, "dropnote", 1, index=0, pitch=51)
    stale["from"] = stale["from"][1:]
    with pytest.raises(FixError, match="bar reads"):
        apply_fixes(root, [stale])
    missing = _fix(root, "dropnote", 1, index=0, pitch=51)
    del missing["from"]
    with pytest.raises(FixError, match="from"):
        apply_fixes(root, [missing])


# --- addnote --------------------------------------------------------------------

def test_addnote_puts_the_note_in_pitch_order_and_copies_an_octave_s_spelling(root):
    apply_fixes(root, [_fix(root, "addnote", 1, index=0, pitch=27)])
    chord = _chords(root, 1)[0]
    assert _pitches(chord) == [27, 39, 51]
    assert chord.find("Note").findtext("tpc") == "11"
    assert chord.findtext("Lyrics/text") == "love"


def test_addnote_spells_by_the_key_not_with_sharps(root):
    apply_fixes(root, [_fix(root, "addnote", 2, index=1, pitch=51)])
    chord = _chords(root, 2)[1]
    assert _pitches(chord) == [44, 51]
    assert chord.findall("Note")[1].findtext("tpc") == "11"  # Eb, not D#


def test_addnote_takes_the_spelling_it_is_given(root):
    apply_fixes(root, [_fix(root, "addnote", 1, index=1, pitch=42, tpc=20)])
    assert _chords(root, 1)[1].findall("Note")[0].findtext("tpc") == "20"  # F#, not Gb


def test_addnote_ties_nothing_on_a_tied_chord(root):
    apply_fixes(root, [_fix(root, "addnote", 1, index=2, pitch=63)])
    chord = _chords(root, 1)[2]
    assert _pitches(chord) == [39, 51, 63]
    assert chord.findall("Note")[2].find("Spanner") is None
    assert len(_ties(root)) == 2


@pytest.mark.parametrize("measure,index,pitch,says", [
    (1, 0, 51, "already"),
    (2, 2, 51, "no index 2"),
])
def test_addnote_refuses(root, measure, index, pitch, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, "addnote", measure, index=index, pitch=pitch)])


def test_addnote_is_strict_about_from(root):
    stale = _fix(root, "addnote", 1, index=1, pitch=36)
    stale["from"] = ["whole:R"]
    with pytest.raises(FixError, match="bar reads"):
        apply_fixes(root, [stale])


@pytest.mark.parametrize("pitch,key,tpc", [
    (51, -4, 11),  # Eb in A flat
    (42, -4, 8),   # Gb: not in the key, a flat key spells it flat
    (42, 0, 20),   # F# in C
    (49, 2, 21),   # C# in D
    (44, 0, 22),   # G# in C: sharps by default
])
def test_spelling_follows_the_key(pitch, key, tpc):
    assert _spell(pitch, key) == tpc


# --- replay and MuseScore -------------------------------------------------------

def test_both_replay_on_a_rebuild(root, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    fixes = [_fix(root, "dropnote", 1, index=2, pitch=51),
             _fix(root, "addnote", 2, index=1, pitch=32)]
    (tmp_path / "fixes.json").write_text(json.dumps(fixes))
    score = tmp_path / "score_cleaned.mscx"
    for _ in range(2):
        etree.ElementTree(_score()).write(str(score))
        assert pipeline.apply_recorded_fixes(str(score), str(tmp_path)) == 2
        again = etree.parse(str(score)).getroot()
        assert _pitches(_chords(again, 1)[2]) == [39]
        assert _pitches(_chords(again, 2)[1]) == [32, 44]
        assert _ties(again) == []


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_opens_the_score_after_a_tied_note_is_dropped(root, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    apply_fixes(root, [_fix(root, "dropnote", 2, index=0, pitch=51)])
    apply_fixes(root, [_fix(root, "addnote", 2, index=1, pitch=32)])
    after = tmp_path / "after.mscx"
    etree.ElementTree(root).write(str(after))
    assert pipeline.musescore_check(str(after)) == []
