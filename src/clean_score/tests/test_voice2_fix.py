"""The `voice2` fix (#431): a second line written into one bar of a part.

A part that splits on the page for a bar or two -- Butterfly's Sopraano 1 holds an
E flat over "Love me" in bar 61 -- has to keep both lines. Cleaning gives a staff whose
voice 2 appears anywhere a part of its own for the whole song, so the line is written
into the cleaned part instead, and replayed on every clean. What these pin: the line
lands as voice 2 beside voice 1, which is left alone; it is written whole (its silences
are rests, it fills the bar), so whatever reads the voice alone finds every note where
the page puts it; its words come in through the lyric editor's voice-2 box; health does
not call it a split that went wrong; and it is as strict about `from` as every kind.
"""
import json
import os

import pytest
from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils.score_fixes import (FixError, _measure, apply_fixes, bar_tokens,
                                               voice2_tokens)

HEAD = "<KeySig><accidental>-3</accidental></KeySig><TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>"


def _chord(dur, pitch, tpc, words=""):
    lyric = f"<Lyrics><text>{words}</text></Lyrics>" if words else ""
    return (f"<Chord><durationType>{dur}</durationType>{lyric}"
            f"<Note><pitch>{pitch}</pitch><tpc>{tpc}</tpc></Note></Chord>")


BARS = [
    HEAD + _chord("half", 67, 15, "love") + _chord("half", 72, 14, "me"),
    _chord("whole", 70, 12, "now"),
]


def _score():
    measures = "".join(f"<Measure><voice>{bar}</voice></Measure>" for bar in BARS)
    return etree.fromstring(
        "<museScore version='3.02'><Score>"
        '<Part><trackName>S1</trackName><Staff id="1"/></Part>'
        f'<Staff id="1">{measures}</Staff></Score></museScore>')


@pytest.fixture
def root():
    return _score()


HELD = [{"value": "rest_4"}, {"value": "note_2.", "pitches": [75], "tpcs": [11]}]


def _fix(root, measure=1, to=HELD, **more):
    return {"kind": "voice2", "staff": 1, "measure": measure,
            "from": voice2_tokens(root, 1, measure), "to": to,
            "why": "the page prints a held Eb5 over 'love me'", **more}


def _voices(root, measure=1):
    return _measure(root, 1, measure).findall("voice")


def test_the_line_lands_as_voice_2_and_voice_1_is_left_alone(root):
    before = bar_tokens(root, 1, 1)
    done = apply_fixes(root, [_fix(root)])
    assert "wrote voice 2 as ['quarter:R', 'half.:75']" in done[0]
    voices = _voices(root)
    assert len(voices) == 2
    assert bar_tokens(root, 1, 1) == before
    assert [l.findtext("text") for l in voices[0].iter("Lyrics")] == ["love", "me"]
    assert voice2_tokens(root, 1, 1) == ["quarter:R", "half.:75"]
    # The line's silence is a written rest, not a gap: nothing reading voice 2 on its
    # own can start its note early.
    assert [el.tag for el in voices[1]] == ["Rest", "Chord"]
    assert voices[1].find("location") is None
    assert voices[1].find("Chord/Note/tpc").text == "11"
    # The other bar keeps its one voice.
    assert len(_voices(root, 2)) == 1


def test_a_triplet_in_the_line_gets_its_bracket(root):
    to = [{"value": "note_6", "pitches": [p], "tpcs": [t]} for p, t in ((75, 11), (74, 16), (72, 14))]
    apply_fixes(root, [_fix(root, to=to + [{"value": "rest_2"}])])
    assert voice2_tokens(root, 1, 1) == ["[tuplet", "quarter:75", "quarter:74", "quarter:72",
                                         "tuplet]", "half:R"]


def test_writing_it_again_replaces_the_line_and_keeps_its_words(root):
    apply_fixes(root, [_fix(root)])
    _voices(root)[1].find("Chord").insert(0, etree.fromstring("<Lyrics><text>breeze</text></Lyrics>"))
    to = [{"value": "note_1", "pitches": [75], "tpcs": [11]}]
    done = apply_fixes(root, [_fix(root, to=to)])
    assert "wrote voice 2 afresh as ['whole:75']" in done[0]
    assert len(_voices(root)) == 2
    assert _voices(root)[1].findtext("Chord/Lyrics/text") == "breeze"


def test_rewriting_keeps_each_chord_s_words_in_place_every_verse(root):
    two = [{"value": "note_2", "pitches": [75], "tpcs": [11]},
           {"value": "note_2", "pitches": [74], "tpcs": [16]}]
    apply_fixes(root, [_fix(root, to=two)])
    second = _voices(root)[1].findall("Chord")[1]
    for verse, text in ((None, "breeze"), ("1", "wind")):
        lyric = etree.fromstring(f"<Lyrics>{f'<no>{verse}</no>' if verse else ''}"
                                 f"<text>{text}</text></Lyrics>")
        second.insert(0, lyric)
    again = [{"value": "note_2", "pitches": [75], "tpcs": [11]},
             {"value": "note_2", "pitches": [72], "tpcs": [14]}]
    apply_fixes(root, [_fix(root, to=again)])
    first, second = _voices(root)[1].findall("Chord")
    assert first.find("Lyrics") is None
    assert sorted(l.findtext("text") for l in second.findall("Lyrics")) == ["breeze", "wind"]
    assert list(second)[-1].tag == "Note"


@pytest.mark.parametrize("to, says", [
    ([{"value": "note_2", "pitches": [75], "tpcs": [11]}], "write its silences out as rests"),
    ([{"value": "rest_1"}], "has no notes"),
    ([], "names no notes"),
])
def test_it_refuses_a_line_that_is_not_a_whole_bar_of_music(root, to, says):
    with pytest.raises(FixError, match=says):
        apply_fixes(root, [_fix(root, to=to)])
    assert len(_voices(root)) == 1


def test_it_is_strict_about_from(root):
    fix = _fix(root)
    del fix["from"]
    with pytest.raises(FixError, match="give a 'from'"):
        apply_fixes(root, [fix])
    with pytest.raises(FixError, match=r"voice 2 reads \[\] now"):
        apply_fixes(root, [_fix(root, **{"from": ["half:75"]})])
    apply_fixes(root, [_fix(root)])
    # Replayed against the bar it already wrote, the same entry no longer matches.
    with pytest.raises(FixError, match="voice 2 reads"):
        apply_fixes(root, [_fix(_score())])


def test_its_words_go_in_through_the_voice_2_box(root):
    apply_fixes(root, [_fix(root)])
    grid = lyric_txt.editor_grid(root)
    system = grid.systems[0]
    assert "1@2" in [box.key for box in grid.boxes[system.index]]
    blocks = lyric_txt.blocks_from_boxes(grid, 1, {system.index: {"1@2": "breeze"}})
    result = lyric_txt.place_lyrics(root, blocks, fmt="json")
    assert result.mismatches == []
    assert _voices(root)[1].findtext("Chord/Lyrics/text") == "breeze"
    assert [l.findtext("text") for l in _voices(root)[0].iter("Lyrics")] == ["love", "me"]


def test_it_replays_on_a_rebuild_and_health_lets_the_line_be(root, tmp_path):
    from src.song_app import health, pipeline  # noqa: PLC0415 - only this test needs the app

    (tmp_path / "fixes.json").write_text(json.dumps([_fix(root)]))
    score = tmp_path / "score_cleaned.mscx"
    for _ in range(2):
        etree.ElementTree(_score()).write(str(score))
        assert pipeline.apply_recorded_fixes(str(score), str(tmp_path)) == 1
        assert voice2_tokens(etree.parse(str(score)).getroot(), 1, 1) == ["quarter:R", "half.:75"]
    assert [i for i in health.scan(str(score)) if i["kind"] == "extra-voices"] == []
    assert pipeline.bar_for_fix(str(score), 1, 1)["voice2"] == ["quarter:R", "half.:75"]
    # Without the entry the same score is a split that went wrong.
    (tmp_path / "fixes.json").write_text("[]")
    assert [i["id"] for i in health.scan(str(score)) if i["kind"] == "extra-voices"] == \
        ["extra-voices-m1-s1"]


def test_health_follows_the_bar_past_a_later_delbar(root, tmp_path):
    from src.song_app import health  # noqa: PLC0415 - only this test needs the app

    apply_fixes(root, [_fix(root, measure=2, to=[{"value": "note_1", "pitches": [63], "tpcs": [11]}])])
    # Bar 1 taken out afterwards: the line now stands in bar 1.
    staff = root.find(".//Score/Staff")
    staff.remove(staff.find("Measure"))
    score = tmp_path / "score_cleaned.mscx"
    etree.ElementTree(root).write(str(score))
    (tmp_path / "fixes.json").write_text(json.dumps([
        {"kind": "voice2", "staff": 1, "measure": 2, "from": [], "to": []},
        {"kind": "delbar", "measure": 1, "from": []}]))
    assert [i for i in health.scan(str(score)) if i["kind"] == "extra-voices"] == []


def _musescore() -> bool:
    cli = os.getenv("MUSESCORE_CLI_PATH", "")
    return bool(cli) and os.path.exists(cli)


@pytest.mark.skipif(not _musescore(), reason="MUSESCORE_CLI_PATH is not set to a real binary")
def test_musescore_opens_the_score_with_the_second_line(root, tmp_path):
    from src.song_app import pipeline  # noqa: PLC0415 - only this test needs the app

    apply_fixes(root, [_fix(root)])
    after = tmp_path / "after.mscx"
    etree.ElementTree(root).write(str(after))
    found = pipeline.musescore_check(str(after))
    if found is None:
        pytest.skip("this MuseScore did not write the check file: "
                    "MuseScore 3 does, MuseScore 4 does not")
    assert found == []
