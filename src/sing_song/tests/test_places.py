import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.sing_song import places, readings

HERE = Path(__file__).resolve().parents[1]


def score(tmp_path, parts):
    """A cleaned score stand-in: {part name: [[(syllable, syllabic, verse) or None (a rest)...]
    per bar]}, every note and rest a quarter."""
    staffs, defs = [], []
    for i, (name, bars) in enumerate(parts.items(), 1):
        defs.append(f'<Part><Staff id="{i}"/><trackName>{name}</trackName></Part>')
        ms = []
        for bar in bars:
            chords = "".join(
                "<Rest><durationType>quarter</durationType></Rest>" if x is None else
                f"<Chord><durationType>quarter</durationType><Lyrics>"
                + (f"<no>{x[2]}</no>" if x[2] else "") + f"<syllabic>{x[1]}</syllabic><text>{x[0]}</text></Lyrics>"
                f"<Note><pitch>60</pitch></Note></Chord>" for x in bar)
            ms.append(f"<Measure><voice>{chords}</voice></Measure>")
        staffs.append(f'<Staff id="{i}">{"".join(ms)}</Staff>')
    p = tmp_path / "song.mscx"
    p.write_text(f'<museScore version="3.02"><Score>{"".join(defs)}{"".join(staffs)}</Score></museScore>')
    return str(p)


def w(*words):
    return [(x, "single", 0) for x in words]


WIND = {"Soprano": [w("the", "wind"), w("blows"), [], [], w("wind", "the"), w("clock")],
        "Bass": [w("the", "wind"), w("blows"), [], [], w("wind", "the"), w("clock")]}


def test_parts_singing_a_word_together_share_its_place(tmp_path):
    got = {(p["bar"], p["beat"], p["word"]): p for p in places.places(score(tmp_path, WIND))}
    assert got[(1, 1.0, "wind")]["parts"] == ["Soprano", "Bass"] and got[(1, 1.0, "wind")]["n"] == 1
    assert got[(5, 0.0, "wind")]["line"] == "the wind blows wind the clock"
    assert (5, 1.0, "the") in got and (1, 0.0, "the") in got


def test_a_part_entering_later_on_the_same_word_has_a_place_of_its_own(tmp_path):
    s = score(tmp_path, {"Soprano": [w("wind", "blows", "the", "wind")], "Bass": [[None, None] + w("wind", "blows")]})
    got = [(p["beat"], p["word"], p["n"], p["parts"]) for p in places.places(s) if p["word"] == "wind"]
    assert got == [(0.0, "wind", 1, ["Soprano"]), (2.0, "wind", 2, ["Bass"]), (3.0, "wind", 3, ["Soprano"])]


def test_a_word_split_over_notes_and_bars_starts_where_its_first_syllable_is(tmp_path):
    s = score(tmp_path, {"S": [[("win", "begin", 0)], [("dy", "end", 0), ("wind", "single", 0), ("wind", "single", 0)]]})
    assert [(p["bar"], p["beat"], p["word"], p["n"]) for p in places.places(s)] == [
        (1, 0.0, "windy", 1), (2, 1.0, "wind", 1), (2, 2.0, "wind", 2)]


def test_a_choice_whose_place_is_gone_is_reported(tmp_path):
    s = score(tmp_path, WIND)
    choices = {"places": [{"bar": 1, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D"},
                          {"bar": 1, "beat": 2.0, "word": "wind", "sounds": "W IH1 N D"}]}
    assert places.stale(choices, s) == [choices["places"][1]]


def test_choices_are_kept_with_the_song(tmp_path):
    assert places.load(str(tmp_path)) == {"places": [], "everywhere": {}}
    c = {"places": [{"bar": 1, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D"}], "everywhere": {"read": "R EH1 D"}}
    places.save(str(tmp_path), c)
    assert places.load(str(tmp_path)) == c and (tmp_path / "pronounce.json").exists()


def test_the_singer_finds_the_place_the_page_chose_in_every_part_and_pass(tmp_path):
    # score to singer: the soprano's "wind" on beat 1 and the bass's later one on beat 3 of the
    # same bar are two places; bar 1 is played twice in a row (a one-bar repeat)
    from src.sing_song.tests.test_g2p import EN, FAKE_G2P_EN, pack
    s = score(tmp_path, {"Soprano": [w("wind", "the", "clock", "blows")], "Bass": [[None, None] + w("wind", "blows")]})
    bass_wind = next(p for p in places.places(s) if p["word"] == "wind" and p["parts"] == ["Bass"])
    (tmp_path / "pronounce.json").write_text(json.dumps(
        {"places": [{"bar": 1, "beat": bass_wind["beat"], "word": "wind", "sounds": "W IH1 N D"}]}))
    (tmp_path / "t.json").write_text(json.dumps(
        {"points": [[0, 0, 0], [2, 0, 2], [4, 0, 0], [6, 0, 2]], "duration": 8, "measures": 1}))
    fake = tmp_path / "fake" / "g2p_en"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(FAKE_G2P_EN)
    sung = {}
    for staff in ("1", "2"):
        out = tmp_path / f"p{staff}.json"
        subprocess.run([sys.executable, "-I", str(HERE / "unroll.py"), s, staff, str(tmp_path / "t.json"), str(out)],
                       check=True, capture_output=True)
        r = subprocess.run([sys.executable, str(HERE / "g2p.py"), "en", str(out)], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=str(tmp_path / "fake"), UFR_DIR=str(pack(tmp_path / staff, EN)),
                                    SONG_PRONOUNCE=str(tmp_path / "pronounce.json")))
        assert r.returncode == 0, r.stderr
        sung[staff] = [n["ph"]["vs"] for n in json.loads(out.read_text()) if n.get("syl") == "wind"]
    assert sung == {"1": [["en/ay"], ["en/ay"]], "2": [["en/ih"], ["en/ih"]]}


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
