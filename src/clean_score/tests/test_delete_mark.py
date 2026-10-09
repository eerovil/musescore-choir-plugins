"""The `delete` fix (#352): take a mark the scan invented off one chord.

The scan puts fermatas where the page prints none — Mieslaulu bar 13 has them on T1's
F and B1's B flat where the page prints staccato dots, and Annin laulu's B1 bar 19 has
one on its third eighth — and nothing in `fixes.json` could take one out. The score
below is those bars in the shape cleaning writes them (bar 1 is Mieslaulu's bar 13,
bar 2 Annin laulu's bar 19 padded to 4/4), plus a bar carrying every other kind of
mark `delete` takes. What these pin: the named mark goes and nothing else does, what
`delete` will not take is refused naming the kind that does, a fermata another staff
still holds is said, and the entry is as strict about `from` as every other kind.
"""
import os

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import (FixError, _measure, apply_fixes, bar_tokens,
                                               read_bar)

FERMATA = "<Fermata><subtype>fermataAbove</subtype><timeStretch>3</timeStretch></Fermata>"
FERMATA_BELOW = "<Fermata><subtype>fermataBelow</subtype><timeStretch>3</timeStretch></Fermata>"
STACCATO = "<Articulation><subtype>articStaccatoAbove</subtype></Articulation>"
ACCENT = "<Articulation><subtype>articAccentAbove</subtype></Articulation>"
ARPEGGIO = "<Arpeggio><subtype>0</subtype></Arpeggio>"
BREATH = "<Breath><symbol>breathMarkComma</symbol></Breath>"
DOLCE = "<StaffText><text>dolce</text></StaffText>"
RED = ('<StaffText><color r="255" g="0" b="0" a="255"/>'
       "<text>&#9888; rhythm?</text></StaffText>")
TEMPO = "<Tempo><tempo>2</tempo><followText>1</followText><text>= 120</text></Tempo>"
REHEARSAL = "<RehearsalMark><text>A</text></RehearsalMark>"
HEAD = "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"


def _chord(dur, *pitches, dots=0, inside="", lyric=""):
    dot = f"<dots>{dots}</dots>" if dots else ""
    words = f"<Lyrics><text>{lyric}</text></Lyrics>" if lyric else ""
    notes = "".join(f"<Note><pitch>{p}</pitch><tpc>14</tpc></Note>" for p in pitches)
    return f"<Chord>{dot}<durationType>{dur}</durationType>{inside}{words}{notes}</Chord>"


def _rest(dur):
    return f"<Rest><durationType>{dur}</durationType></Rest>"


BARS = {
    # T1: Mieslaulu bar 13's invented fermata on the F; Annin laulu's T1 holds one at
    # the beat B1's invented one stands on.
    1: [HEAD + _chord("quarter", 62) + _chord("quarter", 58) + FERMATA
        + _chord("quarter", 65, dots=1, lyric="la") + _chord("eighth", 65),
        # Trinklied B1 bar 25 (#366): a printed sharp read as an arpeggio on chord 0.
        _chord("eighth", 60, inside=ARPEGGIO) + _chord("eighth", 58) + FERMATA
        + _chord("half", 63) + _rest("quarter"),
        TEMPO + REHEARSAL + _chord("quarter", 60) + _chord("quarter", 60)
        + _chord("half", 60)],
    2: [HEAD + _chord("quarter", 58) + _chord("quarter", 58) + _chord("quarter", 62)
        + _chord("quarter", 62),
        _chord("quarter", 56) + _chord("eighth", 55) + _chord("eighth", 56)
        + _chord("half", 58),
        RED + _chord("quarter", 55, inside=STACCATO + ACCENT) + _chord("quarter", 55)
        + BREATH + DOLCE + _chord("quarter", 55) + _chord("quarter", 55)],
    # B1: Mieslaulu's fermata on the B flat, and Annin laulu's two in bar 19 — the
    # one on the third eighth is the scan's.
    3: [HEAD + _chord("quarter", 53) + _chord("quarter", 46) + FERMATA
        + _chord("quarter", 58) + _chord("quarter", 59),
        _chord("eighth", 51) + _chord("eighth", 53) + FERMATA + _chord("eighth", 51)
        + _chord("eighth", 53) + FERMATA + _chord("quarter", 55) + _rest("quarter"),
        _chord("whole", 48) + BREATH],
}


def _score():
    parts = "".join(f'<Part><trackName>{n}</trackName><Staff id="{i}"/></Part>'
                    for i, n in ((1, "T1"), (2, "T2"), (3, "B1")))
    staves = "".join(
        f'<Staff id="{sid}">'
        + "".join(f"<Measure><voice>{bar}</voice></Measure>" for bar in bars)
        + "</Staff>" for sid, bars in BARS.items())
    return etree.fromstring(f"<museScore version='3.02'><Score>{parts}{staves}</Score></museScore>")


@pytest.fixture
def root():
    return _score()


def _fix(root, staff, measure, index, what, **more):
    return {"kind": "delete", "what": what, "staff": staff, "measure": measure,
            "index": index, "from": bar_tokens(root, staff, measure),
            "why": "read against the page", **more}


def _count(root, staff, measure, tag):
    return len(list(_measure(root, staff, measure).iter(tag)))


def test_the_fermata_on_the_named_chord_goes_and_the_chord_stays(root):
    tokens = bar_tokens(root, 1, 1)
    slots = lyric_txt.slot_counts(root)[1][1]
    done = apply_fixes(root, [_fix(root, 1, 1, 2, "fermata")])
    assert "took out 1 fermata(s) on chord 2" in done[0]
    assert _count(root, 1, 1, "Fermata") == 0
    assert bar_tokens(root, 1, 1) == tokens
    assert lyric_txt.slot_counts(root)[1][1] == slots
    assert _measure(root, 1, 1).findtext(".//Lyrics/text") == "la"
    # Another staff's fermata in the same bar is that staff's own entry.
    assert _count(root, 3, 1, "Fermata") == 1


def test_only_the_named_chords_fermata_goes_when_a_bar_has_two(root):
    apply_fixes(root, [_fix(root, 3, 2, 2, "fermata")])
    voice = _measure(root, 3, 2).find("voice")
    kept = [el for el in voice if el.tag == "Fermata"]
    assert len(kept) == 1
    assert kept[0].getnext().findtext("durationType") == "quarter"


def test_a_fermata_above_and_below_both_go(root):
    chord = [el for el in _measure(root, 3, 1).find("voice") if el.tag == "Chord"][2]
    chord.addprevious(etree.fromstring(FERMATA_BELOW))
    done = apply_fixes(root, [_fix(root, 3, 1, 2, "fermata")])
    assert "took out 2 fermata(s)" in done[0]
    assert _count(root, 3, 1, "Fermata") == 0


def test_subtype_narrows_what_goes(root):
    apply_fixes(root, [_fix(root, 2, 3, 0, "articulation", subtype="articAccentAbove")])
    left = [a.findtext("subtype") for a in _measure(root, 2, 3).iter("Articulation")]
    assert left == ["articStaccatoAbove"]
    with pytest.raises(FixError, match=r"no fermata \(fermataBelow\) on chord 2"):
        apply_fixes(root, [_fix(root, 1, 1, 2, "fermata", subtype="fermataBelow")])


@pytest.mark.parametrize("staff, measure, index, what, tag", [
    (2, 3, 0, "articulation", "Articulation"),
    (1, 2, 0, "arpeggio", "Arpeggio"),
    (2, 3, 1, "breath", "Breath"),
    (3, 3, 0, "breath", "Breath"),
    (2, 3, 2, "staff text", "StaffText"),
    (1, 3, 0, "tempo", "Tempo"),
    (1, 3, 0, "rehearsal mark", "RehearsalMark"),
])
def test_each_listed_kind_is_taken_off(root, staff, measure, index, what, tag):
    before = _count(root, staff, measure, tag)
    tokens = bar_tokens(root, staff, measure)
    apply_fixes(root, [_fix(root, staff, measure, index, what)])
    gone = 2 if tag == "Articulation" else 1
    assert _count(root, staff, measure, tag) == before - gone
    assert bar_tokens(root, staff, measure) == tokens


@pytest.mark.parametrize("what, says", [
    ("slur", "unslur"), ("tie", "untie"), ("note", "'bar'"), ("red mark", "unmark"),
    ("lyrics", "lyric editor"), ("clef", "changes the bar"),
    ("time signature", "changes the bar"), ("tuplet", "triplet"),
    ("dynamic", "one of fermata"),
])
def test_what_delete_will_not_take_names_the_right_tool(root, what, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, 1, 1, 2, what)])


def test_a_red_mark_is_refused_and_pointed_at_unmark(root):
    with pytest.raises(FixError, match="red mark; use 'unmark'"):
        apply_fixes(root, [_fix(root, 2, 3, 0, "staff text")])
    assert _count(root, 2, 3, "StaffText") == 2


def test_a_breath_is_read_off_the_chord_it_follows_not_the_next_one(root):
    with pytest.raises(FixError, match="no breath on chord 2"):
        apply_fixes(root, [_fix(root, 2, 3, 2, "breath")])
    assert _count(root, 2, 3, "Breath") == 1


@pytest.mark.parametrize("staff, measure, index, says", [
    (2, 1, 0, "no fermata on chord 0"),
    (1, 1, 9, "has 4 chords, no index 9"),
])
def test_nothing_there_refuses(root, staff, measure, index, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, staff, measure, index, "fermata")])


def test_delete_is_strict_about_from(root):
    fix = _fix(root, 1, 1, 2, "fermata")
    fix["from"] = ["quarter:62", "quarter:58", "quarter:65"]
    with pytest.raises(FixError, match="staff 1 m1 .delete.: bar reads"):
        apply_fixes(root, [fix])
    del fix["from"]
    with pytest.raises(FixError, match="'from'"):
        apply_fixes(root, [fix])
    assert _count(root, 1, 1, "Fermata") == 1


def test_a_fermata_another_staff_holds_at_that_beat_is_said(root):
    done = apply_fixes(root, [_fix(root, 1, 1, 2, "fermata"), _fix(root, 3, 1, 2, "fermata")])
    assert "staff 3 has one too" in done[0]
    assert "playback still" not in done[1]
    # Annin laulu bar 19: T1's fermata stands on the beat of B1's third eighth.
    done = apply_fixes(root, [_fix(root, 3, 2, 2, "fermata")])
    assert "playback still holds this beat: staff 1 has one too" in done[0]


def test_read_bar_lists_each_chords_marks(root):
    bar = read_bar(root, 2, 3)
    assert bar[0]["marks"] == ["articulation:articStaccatoAbove",
                               "articulation:articAccentAbove"]
    # MuseScore writes a breath mark after the chord it follows (#352 review).
    assert bar[1]["marks"] == ["breath:breathMarkComma"]
    assert bar[2]["marks"] == ["staff text:dolce"]
    assert "marks" not in bar[3]
    assert read_bar(root, 1, 1)[2]["marks"] == ["fermata:fermataAbove"]
    # One at the end of a bar belongs to its last chord.
    assert read_bar(root, 3, 3)[0]["marks"] == ["breath:breathMarkComma"]
    assert read_bar(root, 1, 3)[0]["marks"] == ["tempo", "rehearsal mark:A"]
    assert read_bar(root, 1, 2)[0]["marks"] == ["arpeggio:0"]


def test_an_arpeggio_goes_and_the_chord_keeps_its_notes_and_words(root):
    chord = [el for el in _measure(root, 1, 2).find("voice") if el.tag == "Chord"][0]
    chord.append(etree.fromstring("<Lyrics><text>vii</text></Lyrics>"))
    tokens = bar_tokens(root, 1, 2)
    done = apply_fixes(root, [_fix(root, 1, 2, 0, "arpeggio")])
    assert "took out 1 arpeggio(s) on chord 0" in done[0]
    assert _count(root, 1, 2, "Arpeggio") == 0
    assert bar_tokens(root, 1, 2) == tokens
    assert chord.findtext("Lyrics/text") == "vii"
    # The fermata on the next chord is not an arpeggio and stays.
    assert _count(root, 1, 2, "Fermata") == 1
    with pytest.raises(FixError, match="no arpeggio on chord 0"):
        apply_fixes(root, [_fix(root, 1, 2, 0, "arpeggio")])


def test_delete_replays_on_a_rebuild(root, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app
    import json

    fixes = [_fix(root, 1, 1, 2, "fermata"), _fix(root, 3, 1, 2, "fermata"),
             _fix(root, 1, 2, 0, "arpeggio")]
    (tmp_path / "fixes.json").write_text(json.dumps(fixes))
    score = tmp_path / "score_cleaned.mscx"
    for _ in range(2):
        etree.ElementTree(root).write(str(score))
        pipeline.apply_recorded_fixes(str(score), str(tmp_path))
        again = etree.parse(str(score)).getroot()
        assert _count(again, 1, 1, "Fermata") == 0 and _count(again, 3, 1, "Fermata") == 0
        assert _count(again, 1, 2, "Arpeggio") == 0


def _musescore():
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_playback_stops_holding_the_beat_once_both_fermatas_are_out(root, tmp_path):
    import mido  # noqa: PLC0415

    from src.scrollvideo.audio import run_musescore  # noqa: PLC0415

    def seconds(name):
        score, midi = tmp_path / f"{name}.mscx", tmp_path / f"{name}.mid"
        etree.ElementTree(root).write(str(score))
        run_musescore(str(score), str(midi))
        return mido.MidiFile(str(midi)).length

    before = seconds("before")
    apply_fixes(root, [_fix(root, 1, 1, 2, "fermata")])
    one_left = seconds("one")
    apply_fixes(root, [_fix(root, 3, 1, 2, "fermata")])
    after = seconds("after")
    # MuseScore holds a beat for any staff's fermata, so one entry alone changes nothing.
    assert one_left == pytest.approx(before, abs=0.01)
    assert after < before - 0.5
