import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.sing_song import places, readings

HERE = Path(__file__).resolve().parents[1]


def score(tmp_path, parts):
    """A cleaned score stand-in: {part name: [[(syllable, syllabic, verse)...] per bar]}."""
    staffs, defs = [], []
    for i, (name, bars) in enumerate(parts.items(), 1):
        defs.append(f'<Part><Staff id="{i}"/><trackName>{name}</trackName></Part>')
        ms = []
        for bar in bars:
            chords = "".join(
                f"<Chord><durationType>quarter</durationType><Lyrics>"
                + (f"<no>{v}</no>" if v else "") + f"<syllabic>{sy}</syllabic><text>{t}</text></Lyrics>"
                f"<Note><pitch>60</pitch></Note></Chord>" for t, sy, v in bar)
            ms.append(f"<Measure><voice>{chords}</voice></Measure>")
        staffs.append(f'<Staff id="{i}">{"".join(ms)}</Staff>')
    p = tmp_path / "song.mscx"
    p.write_text(f'<museScore version="3.02"><Score>{"".join(defs)}{"".join(staffs)}</Score></museScore>')
    return str(p)


def w(*words):
    return [(x, "single", 0) for x in words]


WIND = {"Soprano": [w("the", "wind"), w("blows"), [], [], w("wind", "the"), w("clock")],
        "Bass": [w("the", "wind"), w("blows"), [], [], w("wind", "the"), w("clock")]}


def test_places_are_bar_word_and_the_time_in_the_bar_for_every_part(tmp_path):
    got = {(p["bar"], p["word"], p["n"]): p for p in places.places(score(tmp_path, WIND))}
    assert got[(1, "wind", 1)]["parts"] == ["Soprano", "Bass"]
    assert got[(5, "wind", 1)]["line"] == "the wind blows wind the clock"
    assert (5, "the", 1) in got and (1, "the", 1) in got


def test_a_word_split_over_notes_and_bars_starts_where_its_first_syllable_is(tmp_path):
    s = score(tmp_path, {"S": [[("win", "begin", 0)], [("dy", "end", 0), ("wind", "single", 0), ("wind", "single", 0)]]})
    assert [(p["bar"], p["word"], p["n"]) for p in places.places(s)] == [(1, "windy", 1), (2, "wind", 1), (2, "wind", 2)]


def test_a_choice_whose_place_is_gone_is_reported(tmp_path):
    s = score(tmp_path, WIND)
    choices = {"places": [{"bar": 1, "word": "wind", "n": 1, "sounds": "W IH1 N D"},
                          {"bar": 2, "word": "wind", "n": 1, "sounds": "W IH1 N D"}]}
    assert places.stale(choices, s) == [choices["places"][1]]


def test_choices_are_kept_with_the_song(tmp_path):
    assert places.load(str(tmp_path)) == {"places": [], "everywhere": {}}
    c = {"places": [{"bar": 1, "word": "wind", "n": 1, "sounds": "W IH1 N D"}], "everywhere": {"read": "R EH1 D"}}
    places.save(str(tmp_path), c)
    assert places.load(str(tmp_path)) == c and (tmp_path / "pronounce.json").exists()


def test_word_key_is_g2ps():
    src = (HERE / "g2p.py").read_text()
    body = src[src.index("def word_key"):src.index("\n\n", src.index("def word_key"))]
    ns = {}
    exec("import re, unicodedata\n" + body, ns)
    for word in ("Wind,", "don’t", "'Tis", "Ääni!"):
        assert ns["word_key"](word) == places.word_key(word)


# the CMU dictionary's readings, as g2p_en has them
CMU = {"wind": [["W", "AY1", "N", "D"], ["W", "IH1", "N", "D"]], "read": [["R", "EH1", "D"], ["R", "IY1", "D"]],
       "live": [["L", "AY1", "V"], ["L", "IH1", "V"]], "tear": [["T", "EH1", "R"], ["T", "IH1", "R"]],
       "bow": [["B", "AW1"], ["B", "OW1"]], "lead": [["L", "EH1", "D"], ["L", "IY1", "D"]],
       "the": [["DH", "AH0"], ["DH", "AH1"], ["DH", "IY0"]], "a": [["AH0"], ["EY1"]],
       "and": [["AH0", "N", "D"], ["AE1", "N", "D"]], "to": [["T", "UW1"], ["T", "IH0"], ["T", "AH0"]],
       "blows": [["B", "L", "OW1", "Z"]]}
LISTED = ["bow", "lead", "live", "read", "tear", "wind"]


def test_only_words_whose_readings_sound_different_are_listed():
    got = readings.readings(list(CMU), CMU, lambda w: CMU[w][0])
    assert sorted(got) == LISTED


def test_the_reading_sung_by_default_comes_first_and_each_is_spelled_out():
    got = readings.readings(["wind"], CMU, lambda w: ["W", "IH1", "N", "D"])
    assert got["wind"] == [{"sounds": "W IH1 N D", "say": "wind", "as_in": "sit"},
                           {"sounds": "W AY1 N D", "say": "wynd", "as_in": "find"}]


SINGER = Path(os.path.expanduser(os.environ.get("SING_VENV") or "~/.local/share/musescore-choir-plugins/sing-venv")) / "bin" / "python"


@pytest.mark.skipif(not SINGER.exists(), reason="no singer venv with g2p_en")
def test_the_real_dictionary_lists_the_homographs_and_not_the_little_words():
    words = ["the", "a", "and", "to", "wind", "read", "live", "tear", "bow", "lead", "blows"]
    r = subprocess.run([str(SINGER), "-I", str(HERE / "readings.py")], input=json.dumps(words),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert sorted(json.loads(r.stdout)) == LISTED


def test_weak_forms_and_accent_variants_are_not_listed():
    cmu = {"for": [["F", "AO1", "R"], ["F", "ER0"], ["F", "R", "ER0"]], "are": [["AA1", "R"], ["ER0"]],
           "where": [["W", "EH1", "R"], ["HH", "W", "EH1", "R"]], "your": [["Y", "AO1", "R"], ["Y", "UH1", "R"]],
           "don't": [["D", "OW1", "N", "T"], ["D", "OW1", "N"]], "new": [["N", "UW1"], ["N", "Y", "UW1"]],
           "rather": [["R", "AE1", "DH", "ER0"], ["R", "AH1", "DH", "ER0"]],
           "again": [["AH0", "G", "EH1", "N"], ["AH0", "G", "EY1", "N"]],
           # and real ones stay
           "close": [["K", "L", "OW1", "S"], ["K", "L", "OW1", "Z"]], "dove": [["D", "AH1", "V"], ["D", "OW1", "V"]],
           "bass": [["B", "AE1", "S"], ["B", "EY1", "S"]], "wound": [["W", "AW1", "N", "D"], ["W", "UW1", "N", "D"]]}
    assert sorted(readings.readings(list(cmu), cmu, lambda w: cmu[w][0])) == ["bass", "close", "dove", "wound"]
