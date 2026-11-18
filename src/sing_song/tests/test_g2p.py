import json
import os
import subprocess
import sys
from pathlib import Path

G2P = Path(__file__).resolve().parents[1] / "g2p.py"


def pack(tmp_path, phonemes):
    """A stand-in voice pack: only core/phonemes.json, which is all g2p.py reads."""
    core = tmp_path / "pack" / "core"
    core.mkdir(parents=True)
    (core / "phonemes.json").write_text(json.dumps({p: i for i, p in enumerate(phonemes)}))
    return core.parent


def run(tmp_path, ufr_dir):
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps([{"pitch": 60, "beats": 1, "sec": 0.5, "syl": "ta", "syllabic": "single"}]))
    r = subprocess.run([sys.executable, "-I", str(G2P), "et", str(notes)], capture_output=True, text=True,
                       env=dict(os.environ, UFR_DIR=str(ufr_dir)))
    return r, json.loads(notes.read_text())


def test_the_sounds_come_from_the_pack_ufr_dir_names(tmp_path):
    r, notes = run(tmp_path, pack(tmp_path, ["it/t", "it/a"]))
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == {"on": ["it/t"], "vs": ["it/a"], "co": []}


def test_a_pack_without_the_sound_is_refused(tmp_path):
    # the same notes against a pack lacking it/a: proof that UFR_DIR's pack, not the default one, was read
    r, _ = run(tmp_path, pack(tmp_path, ["it/t"]))
    assert r.returncode != 0 and "it/a" in (r.stdout + r.stderr)


def test_notes_marked_with_another_language_are_left_to_it(tmp_path):
    # #67: a Finnish syllable in a Latin song keeps no "ph", so the singer reads it as Finnish
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps([
        {"pitch": 60, "beats": 1, "sec": 0.5, "syl": "ta", "syllabic": "single", "lang": "la"},
        {"pitch": 60, "beats": 1, "sec": 0.5, "syl": "pää", "syllabic": "single", "lang": "fi"}]))
    r = subprocess.run([sys.executable, "-I", str(G2P), "la", str(notes)], capture_output=True, text=True,
                       env=dict(os.environ, UFR_DIR=str(pack(tmp_path, ["it/t", "it/a"]))))
    assert r.returncode == 0, r.stderr
    out = json.loads(notes.read_text())
    assert out[0]["ph"] == {"on": ["it/t"], "vs": ["it/a"], "co": []}
    assert "ph" not in out[1]


# ---- English readings chosen on the song page or by a choir (eeron-omat-taskit#143, #144) ----
FAKE_G2P_EN = '''
WORDS = {"wind": "W AY1 N D", "harvest": "HH AA1 R V AH0 S T", "the": "DH AH0", "blows": "B L OW1 Z",
         "clock": "K L AA1 K", "now": "N AW1", "boy": "B OY1", "har": "HH AA1 R", "vist": "V IH1 S T", "vust": "V UH1 S T"}
class G2p:
    def __call__(self, text):
        return WORDS[text].split()
'''
EN = ["en/w", "en/ay", "en/ih", "en/n", "en/d", "en/hh", "en/aa", "en/r", "en/v", "en/ax", "en/s", "en/t",
      "en/er", "en/dh", "en/b", "en/l", "en/ow", "en/z", "en/k", "en/iy", "en/uw", "en/ao"]


def sing_en(tmp_path, words, song=None, choirs=(), sounds=EN):
    """g2p.py en over `words` ((bar, word) or (bar, beat, word), one note each; a pair's beat is
    its index), with a stand-in g2p_en, the
    song page's choices `song` and the choir files `choirs`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    fake = tmp_path / "fake"
    (fake / "g2p_en").mkdir(parents=True, exist_ok=True)
    (fake / "g2p_en" / "__init__.py").write_text(FAKE_G2P_EN)
    env = dict(os.environ, PYTHONPATH=str(fake))
    if song is not None:
        (tmp_path / "pronounce.json").write_text(json.dumps(song))
        env["SONG_PRONOUNCE"] = str(tmp_path / "pronounce.json")
    files = []
    for i, m in enumerate(choirs):
        files.append(tmp_path / f"choir{i}.json")
        files[-1].write_text(json.dumps(m))
    env["CHOIR_WORDS"] = os.pathsep.join(map(str, files))
    notes = tmp_path / "notes.json"
    words = [x if len(x) == 3 else (x[0], float(i), x[1]) for i, x in enumerate(words)]
    notes.write_text(json.dumps([{"pitch": 60, "beats": 1, "sec": 0.5, "syl": w, "syllabic": "single", "bar": b, "beat": t}
                                 for b, t, w in words]))
    env["UFR_DIR"] = str(pack(tmp_path, sounds))
    # not -I: the stand-in g2p_en comes from PYTHONPATH
    r = subprocess.run([sys.executable, str(G2P), "en", str(notes)], capture_output=True, text=True, env=env)
    return r, (json.loads(notes.read_text()) if r.returncode == 0 else None)


WIND_SONG = [(1, 0.0, "the"), (1, 1.0, "wind"), (2, 0.0, "blows"), (5, 0.0, "wind"), (5, 1.0, "the"), (6, 0.0, "clock")]


def test_without_a_choice_the_dictionary_gives_the_sounds(tmp_path):
    r, notes = sing_en(tmp_path, WIND_SONG)
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"] == notes[3]["ph"] == {"on": ["en/w"], "vs": ["en/aa", "en/iy"], "co": ["en/n", "en/d"]}


def test_a_reading_chosen_at_one_place_is_sung_there_only(tmp_path):
    song = {"places": [{"bar": 1, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D"}]}
    r, notes = sing_en(tmp_path, WIND_SONG, song)
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"]["vs"] == ["en/ih"]     # the wind blows
    assert notes[3]["ph"]["vs"] == ["en/aa", "en/iy"]     # wind the clock


def test_the_place_is_the_bar_and_beat_on_every_pass_through_it(tmp_path):
    song = {"places": [{"bar": 1, "beat": 2.0, "word": "wind", "sounds": "W IH1 N D"}]}
    # bar 1 sung twice in a row (a one-bar repeat): the "wind" on beat 2 is the chosen one each time
    words = [(1, 0.0, "wind"), (1, 2.0, "wind"), (1, 0.0, "wind"), (1, 2.0, "wind"), (2, 0.0, "the")]
    r, notes = sing_en(tmp_path, words, song)
    assert r.returncode == 0, r.stderr
    assert [n["ph"]["vs"] for n in notes] == [["en/aa", "en/iy"], ["en/ih"], ["en/aa", "en/iy"], ["en/ih"], ["en/ax"]]


def test_everywhere_in_the_song_and_a_place_wins_over_it(tmp_path):
    song = {"everywhere": {"wind": "W IH1 N D"},
            "places": [{"bar": 5, "beat": 0.0, "word": "wind", "sounds": "W AY1 N D"}]}
    r, notes = sing_en(tmp_path, WIND_SONG, song)
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"]["vs"] == ["en/ih"] and notes[3]["ph"]["vs"] == ["en/aa", "en/iy"]


def test_a_choirs_sound_alike_spelling_and_the_song_page_wins_over_it(tmp_path):
    song = {"places": [{"bar": 5, "beat": 0.0, "word": "wind", "sounds": "W AY1 N D"}]}
    r, notes = sing_en(tmp_path, [(1, 0.0, "harvest"), (5, 0.0, "wind")], song, [{"harvest": "har-vist"}])
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"]["vs"] == ["en/aa", "en/ih"]          # har-vist, not HH AA1 R V AH0 S T
    assert notes[1]["ph"]["vs"] == ["en/aa", "en/iy"]


def test_a_spelling_with_a_sound_the_voice_lacks_names_the_word_and_choir_file(tmp_path):
    r, _ = sing_en(tmp_path, [(1, "harvest")], None, [{"pronounce": {"harvest": "har-vust"}}])
    assert r.returncode != 0 and "'harvest'" in r.stderr and "choir0.json" in r.stderr and "UH1" in r.stderr


def test_two_part_vowels_are_sung_as_their_two_vowels(tmp_path):
    # #484: AY, AW and OY as the voice has them do not move; a user wrote "child" as AA then IY
    r, notes = sing_en(tmp_path, [(1, "wind"), (2, "now"), (3, "boy"), (4, "blows")])
    assert r.returncode == 0, r.stderr
    assert [n["ph"]["vs"] for n in notes] == [["en/aa", "en/iy"], ["en/aa", "en/uw"], ["en/ao", "en/iy"], ["en/ow"]]


# ---- readings in every language, and Latin as German choirs sing it (#484) ----
LA = ["it/a", "it/e", "it/i", "it/o", "it/u", "it/k", "it/s", "it/ch", "it/l", "it/d", "it/t", "it/w", "it/g",
      "it/n", "it/rr", "de/h", "de/ts"]


def sing(tmp_path, code, words, song=None, sounds=LA):
    """g2p.py <code> over `words` ((bar, beat, syllables, note lang or None), one note a syllable,
    "ex-cel-sis" a word of three); returns (result, notes, the readings it used)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    notes = tmp_path / "notes.json"
    out = []
    for bar, beat, w, lang in words:
        syls = w.split("-")
        for i, s in enumerate(syls):
            n = {"pitch": 60, "beats": 1, "sec": 0.5, "syl": s, "bar": bar, "beat": beat + i,
                 "syllabic": "single" if len(syls) == 1 else "begin" if i == 0 else "end" if i == len(syls) - 1 else "middle"}
            if lang: n["lang"] = lang
            out.append(n)
    notes.write_text(json.dumps(out))
    env = dict(os.environ, UFR_DIR=str(pack(tmp_path, sounds)))
    if song is not None:
        (tmp_path / "pronounce.json").write_text(json.dumps(song))
        env["SONG_PRONOUNCE"] = str(tmp_path / "pronounce.json")
    r = subprocess.run([sys.executable, "-I", str(G2P), code, str(notes)], capture_output=True, text=True, env=env)
    if r.returncode:
        return r, None, None
    return r, json.loads(notes.read_text()), json.loads((tmp_path / f"notes.json.{code}.used.json").read_text())


def test_latin_as_german_choirs_sing_it_has_its_h_and_ts(tmp_path):
    words = [(1, 0, "Ho-di-e", None), (2, 0, "ex-cel-sis", None)]
    r, de, _ = sing(tmp_path / "de", "la-de", words)
    assert r.returncode == 0, r.stderr
    assert de[0]["ph"]["on"] == ["de/h"] and de[4]["ph"]["on"] == ["de/ts"]
    r, la, _ = sing(tmp_path / "la", "la", words)
    assert r.returncode == 0, r.stderr
    assert la[0]["ph"]["on"] == [] and la[4]["ph"]["on"] == ["it/ch"]   # Latin itself is unchanged
    assert [n["ph"]["vs"] for n in de] == [n["ph"]["vs"] for n in la]


def test_a_saved_reading_is_dealt_over_the_words_syllables(tmp_path):
    song = {"everywhere": {"la": {"qua": "it/k it/w it/a", "sedet": "it/s it/e it/d it/e it/t"}}}
    r, notes, used = sing(tmp_path, "la", [(1, 0, "qua", None), (2, 0, "se-det", None)], song)
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == {"on": ["it/k", "it/w"], "vs": ["it/a"], "co": []}
    assert [n["ph"] for n in notes[1:]] == [{"on": ["it/s"], "vs": ["it/e"], "co": []},
                                           {"on": ["it/d"], "vs": ["it/e"], "co": ["it/t"]}]
    assert used == [{"lang": "la", "bar": None, "beat": None, "word": "qua"},
                    {"lang": "la", "bar": None, "beat": None, "word": "sedet"}]


def test_a_reading_on_fewer_notes_than_vowels_keeps_every_sound(tmp_path):
    # review of #503: "amen" sung on one note lost the m between its two vowels
    song = {"everywhere": {"la": {"amen": "it/a it/m it/e it/n", "mnmn": "it/m it/n"}}}
    r, notes, _ = sing(tmp_path, "la", [(1, 0, "amen", None), (2, 0, "mn-mn", None)], song,
                       sounds=LA + ["it/m"])
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == {"on": [], "vs": ["it/a", "it/m", "it/e"], "co": ["it/n"]}
    # a hum: the first note hums every sound, the next holds the last
    assert [n["ph"]["vs"] for n in notes[1:]] == [["it/m", "it/n"], ["it/n"]]


def test_a_saved_latin_reading_lands_at_its_place_only(tmp_path):
    song = {"places": [{"bar": 2, "beat": 0, "word": "sed", "sounds": "it/s it/a it/d", "lang": "la"}]}
    r, notes, used = sing(tmp_path, "la", [(1, 0, "sed", None), (2, 0, "sed", None)], song)
    assert r.returncode == 0, r.stderr
    assert [n["ph"]["vs"] for n in notes] == [["it/e"], ["it/a"]]
    assert used == [{"lang": "la", "bar": 2, "beat": 0.0, "word": "sed"}]


def test_an_arpabet_reading_for_latin_is_refused(tmp_path):
    song = {"places": [{"bar": 1, "beat": 0, "word": "sed", "sounds": "S EH1 D", "lang": "la"}]}
    r, _, _ = sing(tmp_path, "la", [(1, 0, "sed", None)], song)
    assert r.returncode != 0 and "ARPAbet is English only" in r.stderr and "'sed'" in r.stderr


def test_an_english_reading_in_voice_sounds_is_dealt_with_no_hum(tmp_path):
    # a user wrote "child" in the voice's sounds; it is sung as written, not run through ARPAbet
    song = {"everywhere": {"en": {"child": "en/ch en/aa en/iy en/l en/d"}}}
    r, notes = sing_en(tmp_path, [(1, "child")], song, sounds=EN + ["en/ch"])
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == {"on": ["en/ch"], "vs": ["en/aa", "en/iy"], "co": ["en/l", "en/d"]}


def test_each_run_uses_only_the_readings_in_its_own_language(tmp_path):
    # an English song with a Latin word: the same word read in English and in Latin, each by its own run
    song = {"places": [{"bar": 1, "beat": 0, "word": "wind", "sounds": "W IH1 N D"}],
            "everywhere": {"en": {"sed": "S EH1 D"}, "la": {"sed": "it/s it/a it/d"}}}
    (tmp_path / "pronounce.json").write_text(json.dumps(song))
    (tmp_path / "g2p_en").mkdir()
    (tmp_path / "g2p_en" / "__init__.py").write_text(FAKE_G2P_EN)
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps([
        {"pitch": 60, "beats": 1, "sec": 0.5, "syl": w, "syllabic": "single", "bar": b, "beat": 0.0, "lang": c}
        for b, w, c in [(1, "wind", "en"), (2, "sed", "la"), (3, "sed", "en")]]))
    env = dict(os.environ, UFR_DIR=str(pack(tmp_path, LA + EN + ["en/eh"])), PYTHONPATH=str(tmp_path),
               SONG_PRONOUNCE=str(tmp_path / "pronounce.json"))
    for code in ("en", "la"):   # as sing_song.py runs them over one line, English first
        r = subprocess.run([sys.executable, str(G2P), code, str(notes)], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
    out = json.loads(notes.read_text())
    assert [n["ph"]["vs"] for n in out] == [["en/ih"], ["it/a"], ["en/eh"]]
    used = lambda c: json.loads((tmp_path / f"notes.json.{c}.used.json").read_text())
    assert used("en") == [{"lang": "en", "bar": 1, "beat": 0.0, "word": "wind"},
                          {"lang": "en", "bar": None, "beat": None, "word": "sed"}]
    assert used("la") == [{"lang": "la", "bar": None, "beat": None, "word": "sed"}]


def test_a_finnish_song_with_a_latin_refrain_uses_its_latin_reading(tmp_path):
    song = {"everywhere": {"la": {"sed": "it/s it/a it/d"}}}
    r, notes, used = sing(tmp_path, "la", [(1, 0, "pää", "fi"), (2, 0, "sed", "la")], song)
    assert r.returncode == 0, r.stderr
    assert "ph" not in notes[0] and notes[1]["ph"]["vs"] == ["it/a"]
    assert used == [{"lang": "la", "bar": None, "beat": None, "word": "sed"}]
