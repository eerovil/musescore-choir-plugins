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


# ---- English words given their sounds by hand (eeron-omat-taskit#143) ----
FAKE_G2P_EN = '''
WORDS = {"wind": "W AY1 N D", "harvest": "HH AA1 R V AH0 S T", "the": "DH AH0"}
class G2p:
    def __call__(self, text):
        return WORDS[text].split()
'''
EN = ["en/w", "en/ay", "en/ih", "en/n", "en/d", "en/hh", "en/aa", "en/r", "en/v", "en/ax", "en/s", "en/t",
      "en/er", "en/dh"]


def sing_en(tmp_path, words, *maps):
    """g2p.py en over `words` (one note each), with a stand-in g2p_en and PRONOUNCE naming `maps`."""
    fake = tmp_path / "fake"
    (fake / "g2p_en").mkdir(parents=True, exist_ok=True)
    (fake / "g2p_en" / "__init__.py").write_text(FAKE_G2P_EN)
    files = []
    for i, m in enumerate(maps):
        files.append(tmp_path / f"map{i}.json")
        files[-1].write_text(json.dumps(m))
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps([{"pitch": 60, "beats": 1, "sec": 0.5, "syl": w, "syllabic": "single"} for w in words]))
    # not -I: the stand-in g2p_en comes from PYTHONPATH
    r = subprocess.run([sys.executable, str(G2P), "en", str(notes)], capture_output=True, text=True,
                       env=dict(os.environ, UFR_DIR=str(pack(tmp_path, EN)), PYTHONPATH=str(fake),
                                PRONOUNCE=os.pathsep.join(map(str, files))))
    return r, (json.loads(notes.read_text()) if r.returncode == 0 else None)


def test_without_a_map_the_dictionary_gives_the_sounds(tmp_path):
    r, notes = sing_en(tmp_path, ["wind"])
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"] == {"on": ["en/w"], "vs": ["en/ay"], "co": ["en/n", "en/d"]}


def test_the_song_map_wins_over_the_choir_map_and_the_dictionary(tmp_path):
    choir = {"pronounce": {"wind": "W AY1 N D", "harvest": "HH AA1 R V IH0 S T"}}
    song = {"Wind": "W IH1 N D"}   # the bare map works too, and case does not matter
    r, notes = sing_en(tmp_path, ["wind", "harvest", "the"], choir, song)
    assert r.returncode == 0, r.stderr
    assert notes[0]["ph"]["vs"] == ["en/ih"]                     # the song's
    assert notes[1]["ph"]["vs"] == ["en/aa", "en/ih"]            # the choir's
    assert notes[2]["ph"] == {"on": ["en/dh"], "vs": ["en/ax"], "co": []}   # the dictionary's


def test_a_sound_the_voice_lacks_names_the_word_and_file(tmp_path):
    r, _ = sing_en(tmp_path, ["wind"], {"wind": "W UH1 N D"})
    assert r.returncode != 0 and "'wind'" in r.stderr and "map0.json" in r.stderr and "UH1" in r.stderr


def test_sounds_without_a_stressed_vowel_are_refused(tmp_path):
    r, _ = sing_en(tmp_path, ["wind"], {"wind": "W IH N D"})
    assert r.returncode != 0 and "stress" in r.stderr
