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
