import json
import shutil
import subprocess
import sys

import pytest

from src.sing_song import main as publish_song

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")


def tone(path, source):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                    "-t", "2", "-ar", "44100", "-ac", "1", str(path)], check=True)


def mean_db(path):
    err = subprocess.run(["ffmpeg", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(err.split("mean_volume: ")[1].split(" dB")[0])


@needs_ffmpeg
def test_both_lines_of_a_split_part_reach_the_mp3(tmp_path):
    # the first line silent, the second a tone: only a real mix has the tone in it
    quiet, sung = tmp_path / "high.wav", tmp_path / "low.wav"
    tone(quiet, "anullsrc=r=44100:cl=mono")
    tone(sung, "sine=frequency=220:sample_rate=44100")
    mp3 = tmp_path / "part.mp3"
    subprocess.run(publish_song.part_mp3_command([str(quiet), str(sung)], str(mp3)), check=True)
    alone = tmp_path / "alone.mp3"
    subprocess.run(publish_song.part_mp3_command([str(sung)], str(alone)), check=True)
    # half volume is 6 dB down from the line sung alone
    assert mean_db(mp3) == pytest.approx(mean_db(alone) - 6.0, abs=1.0)
    assert mean_db(alone) > -30


@needs_ffmpeg
def test_the_lead_in_is_cut(tmp_path):
    sung = tmp_path / "low.wav"
    tone(sung, "sine=frequency=220:sample_rate=44100")
    mp3 = tmp_path / "part.mp3"
    subprocess.run(publish_song.part_mp3_command([str(sung)], str(mp3)), check=True)
    secs = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                 "csv=p=0", str(mp3)], capture_output=True, text=True).stdout)
    assert secs == pytest.approx(2 - publish_song.LEAD_IN_SAMPLES / 44100, abs=0.06)


STUB = '''
import json, os
CALLS = []
class Bundle:
    def __init__(self, out, parts):
        self.musicxml = os.path.join(out, "x.musicxml"); open(self.musicxml, "w").write("<x/>")
        open(os.path.join(out, "score.mid"), "wb").write(b"MThd")
        self.timing = {"duration": 12.5, "measures": 3}; self.parts = parts
DEFAULT_BPM = 80
def build_bundle(cleaned, out, *, initial_bpm=None, tempo_changes=None, part_names=None, staff_groups=None, log=None):
    CALLS.append(dict(initial_bpm=initial_bpm, tempo_changes=tempo_changes, part_names=part_names, staff_groups=staff_groups))
    return Bundle(out, part_names or ["Track 1", "Track 2"])
def score_credits(path):
    return {"composer": "From the score", "arranger": "Score arranger"}
'''


def stub_song_app(tmp_path, publish_settings, monkeypatch):
    """The app's publish, pipeline and state modules swapped for stubs; returns the app's folder."""
    import types
    import src.song_app as package
    app = tmp_path / "app"
    app.mkdir()
    cleaned = app / "cleaned.mscx"
    cleaned.write_text("<museScore/>")
    sources = {
        "publish": STUB,
        "pipeline": "def has_opening_tempo(path):\n    return False\n",
        "state": ("class Song:\n"
                  "    name = 'Laulu'\n"
                  f"    data = {{'publish': {publish_settings!r}}}\n"
                  f"    def cleaned_path(self):\n        return {str(cleaned)!r}\n"
                  "def load(slug):\n    return Song()\n"),
    }
    for name, source in sources.items():
        module = types.ModuleType(f"src.song_app.{name}")
        exec(source, module.__dict__)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(package, name, module, raising=False)
    return app


def test_the_bundle_takes_the_publish_panels_names_and_credits(tmp_path, monkeypatch):
    settings = {"bpm": 150, "tempo_changes": [{"measure": 2, "bpm": 60}], "part_names": ["T1", "T2"],
                "staff_groups": [["T1", "T2"]], "credits": {"composer": "Olli Hurskainen", "lyricist": "", "arranger": ""}}
    app = stub_song_app(tmp_path, settings, monkeypatch)
    out = tmp_path / "bundle"
    out.mkdir()
    manifest, _, _ = publish_song.build_bundle(str(app), "laulu", str(out))
    call = sys.modules["src.song_app.publish"].CALLS[-1]
    assert call == {"initial_bpm": 150, "tempo_changes": [{"measure": 2, "bpm": 60}],
                    "part_names": ["T1", "T2"], "staff_groups": [["T1", "T2"]]}
    assert [p["name"] for p in manifest["parts"]] == ["T1", "T2"]
    # the panel's credits win whole, as in the app's publish; empty ones are left out
    assert manifest["composer"] == "Olli Hurskainen"
    assert "arranger" not in manifest and "lyricist" not in manifest


def test_without_panel_credits_the_scores_own_are_used(tmp_path, monkeypatch):
    app = stub_song_app(tmp_path, {"bpm": 90}, monkeypatch)
    out = tmp_path / "bundle"
    out.mkdir()
    manifest, _, _ = publish_song.build_bundle(str(app), "laulu", str(out))
    assert manifest["composer"] == "From the score" and manifest["arranger"] == "Score arranger"
    assert sys.modules["src.song_app.publish"].CALLS[-1]["part_names"] is None


def test_saved_panel_names_follow_the_folded_parts(tmp_path, monkeypatch):
    from src.sing_song.tests.test_fold import chord, score
    settings = {"part_names": ["Sopraano 1", "Sopraano 1b", "Altto"]}
    app = stub_song_app(tmp_path, settings, monkeypatch)
    score(app / "cleaned.mscx", [("S1", [chord(72) * 4]), ("S1b", [chord(69) * 4]), ("A1", [chord(60) * 4])])
    out = tmp_path / "bundle"
    out.mkdir()
    _, _, groups = publish_song.build_bundle(str(app), "laulu", str(out), str(tmp_path / "folded.mscx"))
    assert groups == [["1", "2"], ["3"]]
    assert sys.modules["src.song_app.publish"].CALLS[-1]["part_names"] == ["Sopraano 1", "Altto"]


def test_a_names_list_of_another_length_is_left_for_song_app_to_refuse():
    assert publish_song.folded_names(["a", "b"], [["1", "2"], ["3"]]) == ["a", "b"]
    assert publish_song.folded_names(None, [["1"]]) is None


def line(*notes):
    return [{"pitch": p, "sec": sec, "beats": sec * 2, "syl": syl} for p, sec, syl in notes]


def test_one_line_is_sung_once():
    a = line((60, 0.5, "la"), (62, 0.5, "lo"))
    assert not publish_song.lines_differ(a, [dict(n) for n in a])


def test_same_pitches_in_another_rhythm_are_two_lines():
    high = line((60, 0.5, "la"), (60, 0.5, "lo"))
    low = line((60, 0.75, "la"), (60, 0.25, "lo"))
    assert publish_song.lines_differ(high, low)


def test_same_pitches_with_other_words_are_two_lines():
    assert publish_song.lines_differ(line((60, 0.5, "la")), line((60, 0.5, "lo")))


def test_other_pitches_are_two_lines():
    assert publish_song.lines_differ(line((60, 0.5, "la")), line((57, 0.5, "la")))


def sung(*words, bar=1):
    """Notes for words, syllables split on "-": one note a syllable, all in `bar`."""
    out = []
    for w in words:
        syls = w.split("-")
        for k, s in enumerate(syls):
            kind = "single" if len(syls) == 1 else "begin" if k == 0 else "end" if k == len(syls) - 1 else "middle"
            out.append({"pitch": 60, "beats": 1, "sec": 0.5, "syl": s, "syllabic": kind, "bar": bar})
    return out


def test_a_named_word_switches_the_language_until_the_next_one():
    # Psallite (#67): Latin, a Finnish verse from "Seimessä", Latin again from "Psallite"
    notes = sung("Psal-li-te", "in", "Sei-mes-sä", "le-pää-pi", "en-kel-ten", "Psal-li-te", "De-i")
    switches = publish_song.parse_lang_from(["fi:Seimessä,hälle", "la:Psallite"])
    langs = [n["lang"] for n in publish_song.mark_languages(notes, "la", switches)]
    assert langs == ["la"] * 4 + ["fi"] * 9 + ["la"] * 5


def test_a_line_starts_in_the_songs_own_language():
    notes = publish_song.mark_languages(sung("hodie"), "la", {"seimessä": "fi"})
    assert notes[0]["lang"] == "la"


def test_finnish_words_left_in_latin_are_found_with_their_bars():
    notes = sung("Psal-li-te", "Do-mi-no") + sung("Sei-mes-sä", "le-pää-pi", bar=9)
    found = publish_song.unmarked_finnish(publish_song.mark_languages(notes, "la", {}), "la", {})
    assert found == [(9, "seimessä"), (9, "lepääpi")]


def test_marked_finnish_and_the_songs_own_words_pass_the_check():
    # Kyrie's y is Latin's own; Seimessä is marked Finnish; a word named in its own language passes
    notes = sung("Ky-ri-e", "e-le-i-son", "Sei-mes-sä", "Ta-pi-o", "lak-ka")
    switches = publish_song.parse_lang_from(["fi:Seimessä", "la:Tapio", "la:lakka"])
    assert publish_song.unmarked_finnish(publish_song.mark_languages(notes, "la", switches), "la", switches) == []


def test_a_bad_lang_from_is_refused():
    with pytest.raises(SystemExit):
        publish_song.parse_lang_from(["xx:sana"])
    with pytest.raises(SystemExit):
        publish_song.parse_lang_from(["fi"])


# --also-eero-pc: lines shared between this card and eero-pc's (share())


@needs_ffmpeg
def test_four_lines_are_mixed_at_a_quarter_each(tmp_path):
    # #104: a part folded from four divisi lines; one sung, three silent
    sung = tmp_path / "sung.wav"
    tone(sung, "sine=frequency=220:sample_rate=44100")
    quiet = []
    for k in range(3):
        q = tmp_path / f"q{k}.wav"
        tone(q, "anullsrc=r=44100:cl=mono")
        quiet.append(str(q))
    mp3, alone = tmp_path / "part.mp3", tmp_path / "alone.mp3"
    subprocess.run(publish_song.part_mp3_command(quiet + [str(sung)], str(mp3)), check=True)
    subprocess.run(publish_song.part_mp3_command([str(sung)], str(alone)), check=True)
    assert mean_db(mp3) == pytest.approx(mean_db(alone) - 12.0, abs=1.0)


def test_a_line_sung_before_from_the_same_notes_is_kept(tmp_path):
    # #104: a run stopped after 30 of its lines; --resume sings only the rest
    work = str(tmp_path)
    notes = [{"pitch": 60, "beats": 1, "syl": "la", "sec": 0.6}]
    (tmp_path / "p7-1high.json").write_text(publish_song.json.dumps(notes))
    assert not publish_song.sung_before(work, 7, "1high", "REMY")       # never sung
    (tmp_path / "p7-1high.wav").write_bytes(b"RIFF")
    publish_song.mark_sung(work, 7, "1high", "REMY")
    assert publish_song.sung_before(work, 7, "1high", "REMY")
    assert not publish_song.sung_before(work, 7, "1high", "LYSE")       # another voice
    notes[0]["pitch"] = 62                                              # the score changed
    (tmp_path / "p7-1high.json").write_text(publish_song.json.dumps(notes))
    assert not publish_song.sung_before(work, 7, "1high", "REMY")


def test_a_line_being_sung_again_is_not_kept(tmp_path):
    # a run stopped while it sang the line over its old WAV: that WAV may be cut short
    work = str(tmp_path)
    (tmp_path / "p1-high.json").write_text("[]")
    (tmp_path / "p1-high.wav").write_bytes(b"RIFF")
    publish_song.mark_sung(work, 1, "high", "LYSE")
    publish_song.forget_sung(work, 1, "high")
    assert not publish_song.sung_before(work, 1, "high", "LYSE")
    publish_song.forget_sung(work, 1, "high")                           # nothing to forget: fine


def test_each_part_is_sung_by_the_voice_its_name_says():
    assert publish_song.part_voice("Sopraano") == publish_song.HIGH
    assert publish_song.part_voice("Altto 2") == publish_song.HIGH
    assert publish_song.part_voice("Mezzo") == publish_song.HIGH
    assert publish_song.part_voice("Tenori") == publish_song.LOW
    assert publish_song.part_voice("B2") == publish_song.LOW
    assert publish_song.part_voice("Ääni 1", "LYSE") == publish_song.HIGH   # Laurille: --voice
    assert publish_song.part_voice("Sopraano", "REMY") == publish_song.LOW
    for name in ("Piano", "1. ääni", "", "  "):
        with pytest.raises(SystemExit):
            publish_song.part_voice(name)


def test_a_place_is_read_as_the_app_takes_it():
    assert publish_song.parse_places(["Ääni 1=-", "Ääni 2 = alto ", "Uusi=^sopranos"]) == [
        {"name": "Ääni 1", "id": "-"}, {"name": "Ääni 2", "id": "alto"}, {"name": "Uusi", "parent": "sopranos"}]
    with pytest.raises(SystemExit):
        publish_song.parse_places(["no equals sign"])


def test_a_venv_with_cuda_libraries_sings_on_the_card(tmp_path):
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    assert publish_song.nvidia_dir(str(python)) is None
    (tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cudnn" / "lib").mkdir(parents=True)
    env = publish_song.singer_env(str(python), "/ufr", "fi", gpu=True)
    assert env["GPU"] == "1" and env["LD_LIBRARY_PATH"].endswith("nvidia/cudnn/lib") and env["UFR_DIR"] == "/ufr"
    assert "GPU" not in publish_song.singer_env(str(python), "/ufr", "la", gpu=False)
    assert publish_song.singer_env(str(python), "/ufr", "la", gpu=False)["SONG_LANG"] == "fi"   # g2p.py gives the sounds


def test_a_click_staff_of_rests_is_left_out_as_publishing_leaves_it_out(tmp_path):
    # seven songs here carry one (a recording spacer); --staff-ids used to name the others
    from src.sing_song.tests.test_fold import chord, rest, score
    path = score(tmp_path / "cleaned.mscx", [("T1", [chord(60) * 4]), ("Click", [rest()]),
                                             ("B1", [chord(48) * 4])])
    assert publish_song.sung_staves(path) == ["1", "3"]


def run_main(monkeypatch, root, *args):
    """main() with this checkout swapped for `root` and the bundle faked (parts named Ääni 1, Piano)."""
    monkeypatch.setattr(publish_song, "ROOT", str(root))
    monkeypatch.setattr(sys, "argv", ["sing_song.py", "laulu", "--out", str(root / "out"), *args])
    manifest = {"duration": 1.0, "parts": [{"name": "Ääni 1"}, {"name": "Piano"}]}
    monkeypatch.setattr(publish_song, "build_bundle", lambda *a: (manifest, "cleaned.mscx", None))
    publish_song.main()


def test_sing_venv_in_dotenv_is_the_one_looked_for(tmp_path, monkeypatch):
    monkeypatch.delenv("SING_VENV", raising=False)
    (tmp_path / ".env").write_text(f"SING_VENV={tmp_path}/my-singer\n")
    with pytest.raises(SystemExit) as stop:
        run_main(monkeypatch, tmp_path)
    assert f"{tmp_path}/my-singer" in str(stop.value)
    monkeypatch.delenv("SING_VENV", raising=False)


def test_piano_only_needs_no_voice_for_the_parts(tmp_path, monkeypatch):
    run_main(monkeypatch, tmp_path, "--no-sing")
    assert json.load(open(tmp_path / "out" / "laulu" / "manifest.json"))["parts"][0]["name"] == "Ääni 1"
