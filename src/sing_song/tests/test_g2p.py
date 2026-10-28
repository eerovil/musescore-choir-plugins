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
         "clock": "K L AA1 K", "har": "HH AA1 R", "vist": "V IH1 S T", "vust": "V UH1 S T"}
class G2p:
    def __call__(self, text):
        return WORDS[text].split()
'''
EN = ["en/w", "en/ay", "en/ih", "en/n", "en/d", "en/hh", "en/aa", "en/r", "en/v", "en/ax", "en/s", "en/t",
      "en/er", "en/dh", "en/b", "en/l", "en/ow", "en/z", "en/k"]


def sing_en(tmp_path, words, song=None, choirs=()):
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
    env["UFR_DIR"] = str(pack(tmp_path, EN))
    # not -I: the stand-in g2p_en comes from PYTHONPATH
    r = subprocess.run([sys.executable, str(G2P), "en", str(notes)], capture_output=True, text=True, env=env)
    return r, (json.loads(notes.read_text()) if r.returncode == 0 else None)


WIND_SONG = [(1, 0.0, "the"), (1, 1.0, "wind"), (2, 0.0, "blows"), (5, 0.0, "wind"), (5, 1.0, "the"), (6, 0.0, "clock")]


def test_without_a_choice_the_dictionary_gives_the_sounds(tmp_path):
    r, notes = sing_en(tmp_path, WIND_SONG)
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"] == notes[3]["ph"] == {"on": ["en/w"], "vs": ["en/ay"], "co": ["en/n", "en/d"]}


def test_a_reading_chosen_at_one_place_is_sung_there_only(tmp_path):
    song = {"places": [{"bar": 1, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D"}]}
    r, notes = sing_en(tmp_path, WIND_SONG, song)
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"]["vs"] == ["en/ih"]     # the wind blows
    assert notes[3]["ph"]["vs"] == ["en/ay"]     # wind the clock


def test_the_place_is_the_bar_and_beat_on_every_pass_through_it(tmp_path):
    song = {"places": [{"bar": 1, "beat": 2.0, "word": "wind", "sounds": "W IH1 N D"}]}
    # bar 1 sung twice in a row (a one-bar repeat): the "wind" on beat 2 is the chosen one each time
    words = [(1, 0.0, "wind"), (1, 2.0, "wind"), (1, 0.0, "wind"), (1, 2.0, "wind"), (2, 0.0, "the")]
    r, notes = sing_en(tmp_path, words, song)
    assert r.returncode == 0, r.stderr
    assert [n["ph"]["vs"] for n in notes] == [["en/ay"], ["en/ih"], ["en/ay"], ["en/ih"], ["en/ax"]]


def test_everywhere_in_the_song_and_a_place_wins_over_it(tmp_path):
    song = {"everywhere": {"wind": "W IH1 N D"},
            "places": [{"bar": 5, "beat": 0.0, "word": "wind", "sounds": "W AY1 N D"}]}
    r, notes = sing_en(tmp_path, WIND_SONG, song)
    assert r.returncode == 0, r.stderr
    assert notes[1]["ph"]["vs"] == ["en/ih"] and notes[3]["ph"]["vs"] == ["en/ay"]


def test_a_choirs_sound_alike_spelling_and_the_song_page_wins_over_it(tmp_path):
    song = {"places": [{"bar": 5, "beat": 0.0, "word": "wind", "sounds": "W AY1 N D"}]}
    r, notes = sing_en(tmp_path, [(1, 0.0, "harvest"), (5, 0.0, "wind")], song, [{"harvest": "har-vist"}])
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"]["vs"] == ["en/aa", "en/ih"]          # har-vist, not HH AA1 R V AH0 S T
    assert notes[1]["ph"]["vs"] == ["en/ay"]


def test_a_spelling_with_a_sound_the_voice_lacks_names_the_word_and_choir_file(tmp_path):
    r, _ = sing_en(tmp_path, [(1, "harvest")], None, [{"pronounce": {"harvest": "har-vust"}}])
    assert r.returncode != 0 and "'harvest'" in r.stderr and "choir0.json" in r.stderr and "UH1" in r.stderr
