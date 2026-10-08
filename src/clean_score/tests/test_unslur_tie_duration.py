"""The `unslur`, `tie` and `duration` fixes (#340), on Annin laulu's own bars.

An agent fixing Annin laulu from its page could not record three things, and faking
them damaged the score: a slur the scan pinned on the wrong voice (B1 bars 1-2, the
arc is B2's tie), a tie (recorded as a slur, so the practice track sings the held note
again), and a double dot read as a single one (bars 9, 10 and 21 came out 11/16 under
3/4, and MuseScore's check then reset the voices the page prints correctly).

The score below is those bars as cleaning writes them from the scan, decorations
taken off: bars 1-2 are bars 1-2 of the song, bars 3-4 are its bars 10-11. What these
pin: each kind does what it says and nothing else, ties and slurs keep their notes,
the bar gets its printed length back on every staff, and every kind is as strict
about `from` as the others.
"""
import os

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import (FixError, _measure, apply_fixes, bar_tokens,
                                               read_bar)


def _chord(dur, *pitches, dots=0, inside=""):
    dot = f"<dots>{dots}</dots>" if dots else ""
    notes = "".join(f"<Note><pitch>{p}</pitch><tpc>{14}</tpc></Note>" for p in pitches)
    return f"<Chord>{dot}<durationType>{dur}</durationType>{inside}{notes}</Chord>"


def _rest(dur):
    return f"<Rest><durationType>{dur}</durationType></Rest>"


SLUR_OUT = ('<Spanner type="Slur"><Slur></Slur><next><location><measures>1</measures>'
            '<fractions>-1/2</fractions></location></next></Spanner>')
SLUR_IN = ('<Spanner type="Slur"><prev><location><measures>-1</measures>'
           '<fractions>1/2</fractions></location></prev></Spanner>')
HEAD = ("<Clef><concertClefType>F</concertClefType></Clef>"
        "<KeySig><accidental>-4</accidental></KeySig>"
        "<TimeSig><sigN>3</sigN><sigD>4</sigD></TimeSig>")
MEASURE_REST = "<Rest><durationType>measure</durationType><duration>11/16</duration></Rest>"

# staff id -> its four bars' voice content
BARS = {
    # T1: bar 10 is the one MuseScore's check reset, because the bar was 11/16.
    1: [HEAD + _chord("quarter", 60) + _chord("quarter", 58) + _chord("eighth", 56)
        + _chord("eighth", 56),
        _chord("eighth", 55) + _chord("eighth", 56) + _chord("quarter", 58)
        + _chord("quarter", 58),
        MEASURE_REST,
        _chord("half", 68) + _rest("quarter")],
    # T2: bar 10 read with one dot where the page prints two.
    2: [HEAD + _chord("quarter", 56) + _chord("quarter", 51) + _chord("eighth", 51)
        + _chord("eighth", 51),
        _chord("eighth", 51) + _chord("eighth", 51) + _chord("quarter", 53)
        + _chord("quarter", 53),
        _chord("quarter", 61, dots=1) + _chord("16th", 61) + _chord("quarter", 60),
        _chord("half", 60) + _rest("quarter")],
    # B1: the slur B2's tie was pinned on, from bar 1 into bar 2; bar 10 is as printed.
    3: [HEAD + _chord("quarter", 51) + _chord("quarter", 49)
        + _chord("eighth", 48, inside=SLUR_OUT) + _chord("eighth", 48),
        _chord("eighth", 46, inside=SLUR_IN) + _chord("eighth", 48) + _chord("quarter", 49)
        + _chord("quarter", 49),
        _chord("eighth", 56, dots=1) + _chord("16th", 53) + _chord("eighth", 56, dots=1)
        + _chord("16th", 53) + _chord("quarter", 51),
        _chord("half", 51) + _rest("quarter")],
}


def _score():
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "T1"), (2, "T2"), (3, "B1")))
    staves = ""
    for sid, bars in BARS.items():
        measures = "".join(
            f'<Measure{" len=" + chr(34) + "11/16" + chr(34) if i == 2 else ""}>'
            f"<voice>{bar}</voice></Measure>" for i, bar in enumerate(bars))
        staves += f'<Staff id="{sid}">{measures}</Staff>'
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


@pytest.fixture
def root():
    return _score()


def _fix(root, kind, staff, measure, **more):
    return {"kind": kind, "staff": staff, "measure": measure,
            "from": bar_tokens(root, staff, measure), "why": "read against the page", **more}


def _slurs(root, staff):
    return [sp for m in (1, 2) for sp in _measure(root, staff, m).iter("Spanner")
            if sp.get("type") == "Slur"]


def _ties(root, staff, measure):
    return _measure(root, staff, measure).findall(".//Note/Spanner[@type='Tie']")


# --- unslur ---------------------------------------------------------------------

def test_unslur_takes_out_both_halves_and_gives_the_syllables_back(root):
    assert lyric_txt.slot_counts(root)[3][1] == 3
    done = apply_fixes(root, [_fix(root, "unslur", 3, 1, index=2)])
    assert "took out the slur from chord 2" in done[0]
    assert "no end" not in done[0]
    assert _slurs(root, 3) == []
    assert lyric_txt.slot_counts(root)[3][1] == 4
    assert bar_tokens(root, 3, 1) == ["quarter:51", "quarter:49", "eighth:48", "eighth:48"]


def test_unslur_finds_a_half_written_in_the_voice_ahead_of_its_chord(root):
    # MuseScore can keep a slur half as a voice child just before the chord.
    voice = _measure(root, 3, 1).find("voice")
    chord = [el for el in voice if el.tag == "Chord"][2]
    half = chord.find("Spanner")
    chord.addprevious(half)
    apply_fixes(root, [_fix(root, "unslur", 3, 1, index=2)])
    assert _slurs(root, 3) == []


def test_unslur_refuses_a_chord_no_slur_starts_on(root):
    with pytest.raises(FixError, match="no slur starts on chord 1"):
        apply_fixes(root, [_fix(root, "unslur", 3, 1, index=1)])
    # The end of a slur is not where one starts either.
    with pytest.raises(FixError, match="no slur starts"):
        apply_fixes(root, [_fix(root, "unslur", 3, 2, index=0)])


def test_unslur_is_strict_about_from(root):
    fix = _fix(root, "unslur", 3, 1, index=2)
    fix["from"] = ["quarter:51", "quarter:49", "quarter:48"]
    with pytest.raises(FixError, match="staff 3 m1 .unslur.: bar reads"):
        apply_fixes(root, [fix])
    del fix["from"]
    with pytest.raises(FixError, match="'from'"):
        apply_fixes(root, [fix])
    assert len(_slurs(root, 3)) == 2


def test_a_bar_rewrite_now_cuts_a_slur_half_kept_on_a_chord_in_another_bar(root):
    # Annin laulu's workaround: B1 bar 1 rewritten as a dotted half. The slur's end
    # half sits on a chord in bar 2 and was left behind, pointing at nothing.
    fix = _fix(root, "bar", 3, 1, to=[{"value": "note_2.", "pitches": [51], "tpcs": [11]}])
    apply_fixes(root, [fix])
    assert _slurs(root, 3) == []


# --- tie ------------------------------------------------------------------------

def test_tie_joins_a_note_to_the_same_pitch_in_the_next_bar(root):
    done = apply_fixes(root, [_fix(root, "tie", 2, 4 - 1, index=2, pitch=60)])
    assert "the first chord of m4" in done[0]
    out, = _ties(root, 2, 3)
    into, = _ties(root, 2, 4)
    assert out.find("Tie") is not None
    # The quarter starts at 7/16 of the 11/16 bar; its partner at the head of bar 4.
    assert (out.findtext("next/location/measures"),
            out.findtext("next/location/fractions")) == ("1", "-7/16")
    assert (into.findtext("prev/location/measures"),
            into.findtext("prev/location/fractions")) == ("-1", "7/16")
    # A held note takes no syllable of its own.
    assert lyric_txt.slot_counts(root)[2][4] == 0
    # MuseScore writes a note's spanners ahead of its pitch.
    assert [el.tag for el in out.getparent()][:2] == ["Spanner", "pitch"]


def test_tie_joins_two_notes_in_one_bar(root):
    apply_fixes(root, [_fix(root, "tie", 2, 3, index=0, pitch=61)])
    out, into = _ties(root, 2, 3)
    assert out.find("next/location/measures") is None
    assert out.findtext("next/location/fractions") == "3/8"
    assert into.findtext("prev/location/fractions") == "-3/8"


@pytest.mark.parametrize("staff,measure,index,pitch,says", [
    (2, 4, 0, 60, "a rest follows"),
    (2, 3, 1, 61, "the next chord has no note at pitch 61"),
    (2, 3, 2, 62, "chord 2 has no note at pitch 62"),
])
def test_tie_refuses_what_a_tie_cannot_join(root, staff, measure, index, pitch, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, "tie", staff, measure, index=index, pitch=pitch)])


def test_tie_refuses_a_note_already_tied(root):
    fix = _fix(root, "tie", 2, 3, index=2, pitch=60)
    apply_fixes(root, [fix])
    fix["from"] = bar_tokens(root, 2, 3)
    with pytest.raises(FixError, match="tied already"):
        apply_fixes(root, [fix])


# --- duration -------------------------------------------------------------------

def test_a_double_dot_gives_the_bar_its_printed_length_on_every_staff(root):
    done = apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to="quarter..")])
    assert "the bar is 3/4 again on every staff" in done[0]
    assert bar_tokens(root, 2, 3) == ["quarter..:61", "16th:61", "quarter:60"]
    for staff in (1, 2, 3):
        assert _measure(root, staff, 3).get("len") is None
    # The voice MuseScore had reset rests through the bar as long as it now is.
    assert _measure(root, 1, 3).findtext(".//Rest/duration") == "3/4"
    # MuseScore's own order inside a chord: dots, then the duration type.
    chord = _measure(root, 2, 3).find("voice/Chord")
    assert [el.tag for el in chord][:2] == ["dots", "durationType"]


def test_a_second_voice_fixed_in_the_same_bar_leaves_the_length_alone(root):
    # Annin laulu bar 9 needs the dot back in two voices; the second finds the bar
    # already 3/4 and fills it.
    _measure(root, 3, 3).find("voice").clear()
    _measure(root, 3, 3).find("voice").append(etree.fromstring(_chord("quarter", 56, dots=1)))
    _measure(root, 3, 3).find("voice").append(etree.fromstring(_chord("16th", 56)))
    _measure(root, 3, 3).find("voice").append(etree.fromstring(_chord("quarter", 56)))
    done = apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to="quarter.."),
                              _fix(root, "duration", 3, 3, index=0, to="quarter..")])
    assert "every staff" not in done[1]
    assert bar_tokens(root, 3, 3) == ["quarter..:56", "16th:56", "quarter:56"]


def test_a_tie_out_of_the_bar_keeps_its_note_when_an_earlier_note_lengthens(root):
    # The order an agent would meet them in: tie first, then the double dot. The
    # tie's own note moves from 7/16 to 1/2, so it must still reach bar 4's head.
    apply_fixes(root, [_fix(root, "tie", 2, 3, index=2, pitch=60)])
    apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to="quarter..")])
    out, = _ties(root, 2, 3)
    into, = _ties(root, 2, 4)
    # The shape the real song's own ties into bar 11 have.
    assert out.findtext("next/location/fractions") == "-1/2"
    assert into.findtext("prev/location/fractions") == "1/2"


def test_a_tie_into_the_bar_after_the_changed_note_moves_with_it(root):
    apply_fixes(root, [_fix(root, "tie", 2, 3, index=0, pitch=61)])
    apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to="quarter..")])
    out, into = _ties(root, 2, 3)
    assert out.findtext("next/location/fractions") == "7/16"
    assert into.findtext("prev/location/fractions") == "-7/16"


def test_duration_refuses_a_bar_no_time_signature_prints(root):
    with pytest.raises(FixError, match="would last 25/32 .* bar is 11/16 and the time "
                                       "signature 3/4"):
        apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to="quarter...")])
    assert _measure(root, 2, 3).get("len") == "11/16"


@pytest.mark.parametrize("to,says", [
    ("quarter.", "is quarter. already"),
    ("crotchet", "not a length"),
    ("quarter.:61", "a length only"),
])
def test_duration_refuses_what_is_not_a_change_of_length(root, to, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to=to)])


def test_duration_is_strict_about_from(root):
    fix = _fix(root, "duration", 2, 3, index=0, to="quarter..")
    fix["from"] = ["quarter..:61", "16th:61", "quarter:60"]
    with pytest.raises(FixError, match="bar reads"):
        apply_fixes(root, [fix])


def test_read_bar_still_counts_chords_only_and_tokens_carry_the_rests(root):
    assert [c["index"] for c in read_bar(root, 2, 4)] == [0]
    assert bar_tokens(root, 2, 4) == ["half:60", "quarter:R"]


# --- MuseScore ------------------------------------------------------------------

def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_opens_the_bar_the_double_dot_was_missing_from(root, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    before = tmp_path / "before.mscx"
    etree.ElementTree(root).write(str(before))
    rejected = pipeline.musescore_check(str(before))
    assert [(r["measure"], r["staff"]) for r in rejected] == [(3, 3)]

    apply_fixes(root, [_fix(root, "duration", 2, 3, index=0, to="quarter..")])
    apply_fixes(root, [_fix(root, "tie", 2, 3, index=2, pitch=60),
                       _fix(root, "unslur", 3, 1, index=2)])
    after = tmp_path / "after.mscx"
    etree.ElementTree(root).write(str(after))
    assert pipeline.musescore_check(str(after)) == []
