"""#484 left English and Latin sung byte for byte as before: every word of every song on this host
that does not look Finnish goes through g2p.py as it was before #484 (commit BEFORE) and as it is
now, in English and in Latin, and the sounds must be the same. English is checked again with a
song's own saved readings (the old flat shape). Host only: it needs songs/, the singer's venv
(g2p_en) and the voice pack, so CI skips it and the pull request reports this host's run."""
import glob
import json
import os
import subprocess

import pytest

from src.sing_song import language_check

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BEFORE = "08d14ca"   # origin/main when #484's second pull request started
SING_PY = os.path.expanduser("~/.local/share/musescore-choir-plugins/sing-venv/bin/python")
UFR = os.environ.get("UFR_DIR") or os.path.expanduser("~/.local/share/musescore-choir-plugins/ufr")
SONGS = os.path.join(ROOT, "songs")


def old_g2p(tmp_path):
    try:
        src = subprocess.run(["git", "-C", ROOT, "show", f"{BEFORE}:src/sing_song/g2p.py"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"no git history with {BEFORE}")
    d = tmp_path / "old"
    d.mkdir()
    (d / "g2p.py").write_text(src)
    return str(d / "g2p.py")


def foreign_words():
    """Each distinct word, as its syllables with their syllabic, of the songs that look foreign."""
    from lxml import etree
    out = {}
    for mscx in sorted(glob.glob(os.path.join(SONGS, "*", "*_cleaned.mscx"))):
        try:
            if not language_check.looks_foreign(language_check.score_words(mscx)):
                continue
            root = etree.parse(mscx).getroot()
        except (OSError, etree.XMLSyntaxError):
            continue
        for staff in root.iter("Staff"):
            word = []
            for lyrics in staff.iter("Lyrics"):
                text = "".join(lyrics.find("text").itertext()) if lyrics.find("text") is not None else ""
                kind = lyrics.findtext("syllabic") or "single"
                if kind in ("begin", "single"):
                    word = []
                word.append((text, kind))
                if kind in ("end", "single") and all(t.strip() for t, _ in word):
                    out[tuple(word)] = None
    return list(out)


def sounds(g2p, code, notes, tmp_path, name, env):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(notes, ensure_ascii=False))
    r = subprocess.run([SING_PY, "-I", g2p, code, str(path)], capture_output=True, text=True,
                       env=dict(os.environ, UFR_DIR=UFR, **env))
    assert r.returncode == 0, r.stderr[-2000:]
    return [n.get("ph") for n in json.loads(path.read_text())]


@pytest.mark.skipif(not os.path.isdir(SONGS) or not os.path.isfile(SING_PY)
                    or not os.path.isfile(os.path.join(UFR, "core", "phonemes.json")),
                    reason="needs this host's songs/, singer venv and voice pack")
@pytest.mark.parametrize("code,saved", [("la", None), ("en", None), ("en", "our-joyful-st-feast")])
def test_english_and_latin_are_sung_as_before(tmp_path, code, saved):
    words = foreign_words()
    if not words:
        pytest.skip("no foreign-looking song on this host")
    notes = [{"pitch": 60, "beats": 1, "sec": 0.5, "syl": syl, "syllabic": kind, "bar": i + 1, "beat": 0.0}
             for i, word in enumerate(words) for syl, kind in word]
    env = {"CHOIR_WORDS": "", "SONG_PRONOUNCE": ""}
    if saved:
        pron = os.path.join(SONGS, saved, "pronounce.json")
        if not os.path.isfile(pron):
            pytest.skip(f"no {saved}/pronounce.json")
        env["SONG_PRONOUNCE"] = pron
    before = sounds(old_g2p(tmp_path), code, notes, tmp_path, "before", env)
    now = sounds(os.path.join(ROOT, "src", "sing_song", "g2p.py"), code, notes, tmp_path, "now", env)
    differ = [(n["syl"], b, a) for n, b, a in zip(notes, before, now) if b != a]
    print(f"{code}: {len(words)} words, {len(notes)} syllables, {len(differ)} differ")
    assert differ == []
