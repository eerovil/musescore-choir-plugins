import os
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


def test_folding_leaves_out_a_click_staff_as_publishing_does(tmp_path, monkeypatch):
    # #439 review: the folded score still has the click part, the bundle does not
    from src.sing_song.tests.test_fold import chord, rest, score
    settings = {"part_names": ["Sopraano 1", "Sopraano 1b", "Altto"]}
    app = stub_song_app(tmp_path, settings, monkeypatch)
    score(app / "cleaned.mscx", [("S1", [chord(72) * 4]), ("S1b", [chord(69) * 4]), ("Click", [rest()]),
                                 ("A1", [chord(60) * 4])])
    out = tmp_path / "bundle"
    out.mkdir()
    _, _, groups = publish_song.build_bundle(str(app), "laulu", str(out), str(tmp_path / "folded.mscx"))
    assert groups == [["1", "2"], ["4"]]
    assert sys.modules["src.song_app.publish"].CALLS[-1]["part_names"] == ["Sopraano 1", "Altto"]


def test_a_resting_part_whose_lettered_line_sings_is_kept_when_folded(tmp_path, monkeypatch):
    # #439 review: S1 rests throughout, S1b sings; folded, S1 sings S1b's line and publishing keeps it
    from src.sing_song.tests.test_fold import chord, rest, score
    app = stub_song_app(tmp_path, {}, monkeypatch)
    score(app / "cleaned.mscx", [("S1", [rest()]), ("S1b", [chord(69) * 4]), ("Click", [rest()]),
                                 ("A1", [chord(60) * 4])])
    out = tmp_path / "bundle"
    out.mkdir()
    _, _, groups = publish_song.build_bundle(str(app), "laulu", str(out), str(tmp_path / "folded.mscx"))
    assert groups == [["1", "2"], ["4"]]


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


def test_each_note_lists_the_syllable_sung_on_it_held_or_repeated_too():
    # "Kis-sa," then a note with no syllable of its own (it holds "sa"), a rest, and one more
    # that sings "sa" again (#110: the site merges a shared note only on the same syllable)
    notes = line((52, 0.25, "Kis"), (52, 0.25, "sa,"), (50, 0.5, None), (None, 0.5, None), (52, 0.5, None))
    assert publish_song.sung_words([notes]) == [[0.0, 52, "kis"], [0.25, 52, "sa"], [0.5, 50, "sa"], [1.5, 52, "sa"]]


def test_a_note_both_lines_sing_is_listed_once_with_the_others_in_time_order():
    high = line((64, 0.5, "dum"), (62, 0.5, "dum"))
    low = line((60, 0.5, "dum"), (62, 0.5, "dum"))
    assert publish_song.sung_words([high, low]) == [[0.0, 60, "dum"], [0.0, 64, "dum"], [0.5, 62, "dum"]]


def test_a_note_the_lines_sing_on_different_syllables_is_left_out():
    # both lines are in the part's one MP3: the site must never merge that note with another part
    high = line((52, 0.5, "kis"), (55, 0.5, "sa"))
    low = line((52, 0.5, "dum"), (50, 0.5, "dum"))
    assert publish_song.sung_words([high, low]) == [[0.5, 50, "dum"], [0.5, 55, "sa"]]


def test_a_sentence_spoken_on_one_chord_is_compared_whole():
    # the site merges the chord's one MIDI note as a whole: two parts saying sentences that
    # start alike must not match on the first syllable
    one = publish_song.sung_words([line((52, 2.0, "Laulu‿reiän‿tarpeesta"))])
    other = publish_song.sung_words([line((52, 2.0, "Laulu‿loppuu‿tähän"))])
    assert one == [[0.0, 52, "laulureiäntarpeesta"]]
    assert one[0][2] != other[0][2]


def test_spoken_sentences_that_differ_only_in_a_number_differ():
    one = publish_song.sung_words([line((52, 2.0, "Laulu‿118"))])
    other = publish_song.sung_words([line((52, 2.0, "Laulu‿119"))])
    assert one == [[0.0, 52, "laulu118"]]
    assert one[0][2] != other[0][2]


def test_words_json_has_each_parts_list_in_order(tmp_path):
    path = tmp_path / "words.json"
    publish_song.write_words(path, [[[0.0, 52, "kis"]], [[0.0, 52, "dum"]]])
    assert json.loads(path.read_text()) == {"version": 1, "parts": [[[0.0, 52, "kis"]], [[0.0, 52, "dum"]]]}


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


def test_a_mens_song_is_sung_low_and_a_womens_high_whatever_a_part_is_called():
    # #453: a men's choir "Solo" was sung by LYSE because its name starts with S
    assert publish_song.part_voice("Solo", voicing="men") == publish_song.LOW
    assert publish_song.part_voice("Altto", voicing="men") == publish_song.LOW
    assert publish_song.part_voice("Tenori", voicing="women") == publish_song.HIGH
    assert publish_song.part_voice("Solo", "LYSE", voicing="men") == publish_song.HIGH   # --voice still wins
    assert publish_song.part_voice("Sopraano solo", voicing="mixed") == publish_song.HIGH
    assert publish_song.part_voice("Tenori solo", voicing="mixed") == publish_song.LOW
    for voicing in ("mixed", None):
        with pytest.raises(SystemExit, match="which voice sings the part 'Solo'"):
            publish_song.part_voice("Solo", voicing=voicing)


def test_the_songs_voicing_is_read_off_its_state(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_song, "ROOT", str(tmp_path))
    (tmp_path / "songs" / "laulu").mkdir(parents=True)
    (tmp_path / "songs" / "laulu" / ".song.json").write_text('{"voicing": "men"}')
    assert publish_song.song_voicing("laulu") == "men"
    assert publish_song.song_voicing("missing") is None


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
    assert json.load(open(tmp_path / "out" / "manifest.json"))["parts"][0]["name"] == "Ääni 1"


def test_the_bundle_lands_in_out_and_an_old_run_goes_but_work_stays(tmp_path, monkeypatch):
    out = tmp_path / "out"
    (out / "parts").mkdir(parents=True)
    (out / "work").mkdir()
    (out / "parts" / "1-Old.mp3").write_text("old take")
    (out / "work" / "p1-high.wav").write_text("for --resume")
    (out / "notes.txt").write_text("somebody's own file")
    run_main(monkeypatch, tmp_path, "--no-sing")
    assert (out / "manifest.json").exists() and not (out / "laulu").exists()
    assert not (out / "parts" / "1-Old.mp3").exists()
    assert (out / "work" / "p1-high.wav").read_text() == "for --resume"
    assert (out / "notes.txt").exists()


@pytest.mark.parametrize("slug", ["/home/user/Documents", "../songs", "a/b", "..", ".trash", ""])
def test_a_slug_that_is_a_path_is_refused_before_anything_is_touched(tmp_path, monkeypatch, slug):
    victim = tmp_path / "Documents"
    victim.mkdir()
    (victim / "keep.txt").write_text("mine")
    monkeypatch.setattr(sys, "argv", ["sing_song.py", slug, "--no-sing", "--out", str(victim)])
    monkeypatch.setattr(publish_song, "build_bundle", lambda *a: pytest.fail("got past the slug check"))
    with pytest.raises(SystemExit):
        publish_song.main()
    assert (victim / "keep.txt").read_text() == "mine"


def test_a_part_name_with_a_slash_is_sung_into_parts_itself(tmp_path):
    # the MP3 sing() writes and the one publish_to_site hands on are the same file, in parts/
    mp3 = os.path.join(str(tmp_path), publish_song.sung_file(1, "S/A"))
    assert os.path.dirname(mp3) == os.path.join(str(tmp_path), "parts")
    assert os.path.basename(mp3) == "1-S_A.mp3"


def no_silent(cmd, kw, found=()):
    """A faked singer's silence check (SILENT in sing_ufr.py): it finds `found`."""
    path = kw.get("env", {}).get("SILENT")
    if path:
        assert cmd[2].endswith("sing_ufr.py")
        with open(path, "w") as f:
            json.dump(list(found), f)
    return bool(path)


def two_part_song(tmp_path):
    from src.sing_song.tests.test_fold import chord, score
    cleaned = score(tmp_path / "cleaned.mscx", [("T1", [chord(60, syl="la") * 4]), ("B1", [chord(48, syl="kk") * 4])])
    bundle, work = tmp_path / "bundle", tmp_path / "work"
    bundle.mkdir()
    work.mkdir()
    (bundle / "timing.json").write_text(json.dumps({"points": [[0, 0, 0], [4, 0, 4]], "duration": 4, "measures": 1}))
    return cleaned, bundle, work


@pytest.mark.parametrize("allow", [False, True])
def test_notes_sung_as_no_sound_stop_the_run_before_anything_is_sung(tmp_path, monkeypatch, capsys, allow):
    # #449: a note whose syllable is only stops is heard as silence; the run lists it and stops
    cleaned, bundle, work = two_part_song(tmp_path)
    real_run, sung = publish_song.run, []

    def run(cmd, **kw):
        if str(cmd[2]).endswith("unroll.py"):
            return real_run(cmd, **kw)
        bass = "p2-" in str(cmd[3]) if len(cmd) > 3 else False
        if no_silent(cmd, kw, [{"bar": 1, "syl": "kk"}] if bass else []):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["nice", "-n", "19"]:
            sung.append(os.path.basename(cmd[6]))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(publish_song, "run", run)
    monkeypatch.delenv("REMOTE_RUNNER", raising=False)
    manifest = {"duration": 4, "parts": [{"name": "T1"}, {"name": "B1"}]}
    sing = lambda: publish_song.sing(manifest, cleaned, str(bundle), str(work), ["remy_adult", "remy_adult"],
                                     "fi", sys.executable, "/ufr", False, allow_silent=allow)
    if allow:
        sing()
        assert sorted(sung) == ["p1-high.json", "p2-high.json"]
        assert "B1, bar 1: kk" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as e:
            sing()
        assert sung == []
        assert "B1, bar 1: kk" in str(e.value) and "--allow-silent" in str(e.value)


def test_sing_skips_a_folded_parts_resting_staff_and_sings_its_lettered_line(tmp_path, monkeypatch):
    # #439 review, through sing() itself: S1 rests throughout and S1b sings; the part is S1b's line
    from src.sing_song.tests.test_fold import chord, rest, score
    cleaned = score(tmp_path / "cleaned.mscx", [("S1", [rest()]), ("S1b", [chord(69, syl="la") * 4]),
                                                ("A1", [chord(60, syl="lu") * 4])])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "timing.json").write_text(json.dumps(
        {"points": [[0, 0, 0], [4, 0, 4]], "duration": 4, "measures": 1}))
    work = tmp_path / "work"
    work.mkdir()
    sung, real_run = [], publish_song.run

    def run(cmd, **kw):
        if str(cmd[2]).endswith("unroll.py"):
            return real_run(cmd, **kw)
        if "check_onsets.py" in str(cmd[2]) or "gate.py" in str(cmd[2]) or cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if no_silent(cmd, kw):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        assert cmd[:3] == ["nice", "-n", "19"]   # the singer: nice -n 19 <python> -I sing_ufr.py <line>.json ...
        sung.append((cmd[6], [n.get("pitch") for n in json.load(open(cmd[6])) if n.get("pitch")]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(publish_song, "run", run)
    monkeypatch.delenv("REMOTE_RUNNER", raising=False)   # this host's .env names one
    manifest = {"duration": 4, "parts": [{"name": "S1"}, {"name": "A1"}]}
    publish_song.sing(manifest, cleaned, str(bundle), str(work),
                      ["lyse_core", "lyse_core"], "fi", sys.executable, "/ufr", False, groups=[["1", "2"], ["3"]])
    assert [(os.path.basename(js), pitches) for js, pitches in sung] == [
        ("p1-2high.json", [69, 69, 69, 69]), ("p2-high.json", [60, 60, 60, 60])]


# REMOTE_RUNNER: lines shared between this host and the runner (share()), as --also-eero-pc did
import time  # noqa: E402


@pytest.fixture(autouse=True)
def not_stopping():
    publish_song.STOPPING.clear()
    yield
    publish_song.STOPPING.clear()


def singer(log_to, seconds=0.0):
    def sing(job):
        time.sleep(seconds)
        log_to.append(job)
    return sing


def test_without_a_runner_every_line_is_sung_here():
    here = []
    done = publish_song.share([1, 2, 3], singer(here))
    assert here == [1, 2, 3] and set(done.values()) == {"here"}


def test_both_sing_and_every_line_once():
    here, there = [], []
    done = publish_song.share(list(range(8)), singer(here, 0.05), singer(there, 0.01))
    assert sorted(here + there) == list(range(8))
    assert here and there and len(there) > len(here)   # the faster one takes more
    assert {j for j, w in done.items() if w == "there"} == set(there)


def test_a_line_the_runner_fails_is_sung_here_and_it_takes_no_more():
    here, tried = [], []

    def broken(job):
        tried.append(job)
        raise RuntimeError("no card")
    done = publish_song.share([1, 2, 3, 4], singer(here, 0.05), broken)
    assert len(tried) == 1 and sorted(here) == [1, 2, 3, 4] and set(done.values()) == {"here"}


def test_a_failure_here_stops_the_run():
    with pytest.raises(SystemExit):
        publish_song.share([1, 2, 3], lambda job: sys.exit("singer failed"))


def runner_script(tmp_path, body):
    path = tmp_path / "runner"
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return str(path)


def test_the_runner_is_asked_to_sing_the_line_and_its_wav_is_used(tmp_path):
    runner = runner_script(tmp_path, 'echo "$@" > "$(dirname "$0")/args"; echo "RIFF $SONG_LANG" > "$3"\n')
    wav = tmp_path / "p1-high.wav"
    publish_song.sing_remotely(runner, str(tmp_path / "p1-high.json"), str(wav), "remy_adult",
                               dict(os.environ, SONG_LANG="la"))
    assert wav.read_text() == "RIFF la\n"
    assert (tmp_path / "args").read_text().split() == ["sing-line", str(tmp_path / "p1-high.json"), str(wav),
                                                        "remy_adult"]


def test_a_runner_that_cannot_says_why(tmp_path):
    runner = runner_script(tmp_path, 'echo "trying"; echo "the card is not free"; exit 3\n')
    with pytest.raises(RuntimeError, match="the card is not free"):
        publish_song.sing_remotely(runner, "l.json", str(tmp_path / "o.wav"), "remy_adult", dict(os.environ))
    with pytest.raises(RuntimeError, match="exit 0"):   # exit 0 and no WAV is not sung
        publish_song.sing_remotely(runner_script(tmp_path, "exit 0\n"), "l.json", str(tmp_path / "o.wav"),
                                   "remy_adult", dict(os.environ))


def test_a_line_that_hangs_on_the_runner_gives_up_in_time(tmp_path, monkeypatch):
    # #86 in eeron-omat-taskit: a singer hung in eero-pc's driver and the run waited 13 minutes
    monkeypatch.setattr(publish_song, "REMOTE_LINE_LIMIT", 1)
    with pytest.raises(RuntimeError, match="no answer in 1 s"):
        publish_song.sing_remotely(runner_script(tmp_path, "sleep 5\n"), "l.json", str(tmp_path / "o.wav"),
                                   "remy_adult", dict(os.environ))


def test_a_failure_here_ends_what_the_runner_is_doing(tmp_path):
    finished = tmp_path / "finished"
    runner = runner_script(tmp_path, f"sleep 2; touch {finished}\n")

    def here(job):
        time.sleep(0.3)
        raise SystemExit(143)   # what stop_on_term() raises

    def there(job):
        publish_song.sing_remotely(runner, "l.json", str(tmp_path / "o.wav"), "remy_adult", dict(os.environ))
    started = time.monotonic()
    with pytest.raises(SystemExit):
        publish_song.share(["a", "b"], here, there, publish_song.stop_remote)
    assert time.monotonic() - started < 1.5     # did not wait for the runner
    time.sleep(2.5)
    assert not finished.exists()                # and the runner was ended
    with pytest.raises(RuntimeError):           # nothing new starts once stopping
        publish_song.sing_remotely(runner, "l.json", "o.wav", "remy_adult", dict(os.environ))


def test_sing_hands_lines_to_the_runner_when_there_is_one(tmp_path, monkeypatch):
    from src.sing_song.tests.test_fold import chord, score
    cleaned = score(tmp_path / "cleaned.mscx", [("T1", [chord(60, syl="la") * 4]), ("B1", [chord(48, syl="lu") * 4])])
    bundle, work = tmp_path / "bundle", tmp_path / "work"
    bundle.mkdir()
    work.mkdir()
    (bundle / "timing.json").write_text(json.dumps({"points": [[0, 0, 0], [4, 0, 4]], "duration": 4, "measures": 1}))
    # this host is slow: the runner takes the line it is free for first
    real_run = publish_song.run
    here = []

    def run(cmd, **kw):
        if str(cmd[2]).endswith("unroll.py"):
            return real_run(cmd, **kw)
        if no_silent(cmd, kw):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["nice", "-n", "19"]:
            time.sleep(0.5)
            here.append(os.path.basename(cmd[6]))
            open(cmd[7], "w").close()
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(publish_song, "run", run)
    monkeypatch.setenv("REMOTE_RUNNER", runner_script(tmp_path, 'echo "$2" >> "$(dirname "$0")/sung"; : > "$3"\n'))
    manifest = {"duration": 4, "parts": [{"name": "T1"}, {"name": "B1"}]}
    publish_song.sing(manifest, cleaned, str(bundle), str(work), ["remy_adult", "remy_adult"], "fi",
                      sys.executable, "/ufr", False)
    there = [os.path.basename(p) for p in (tmp_path / "sung").read_text().split()]
    assert sorted(here + there) == ["p1-high.json", "p2-high.json"] and here and there


def test_an_old_wav_does_not_pass_for_a_line_the_runner_did_not_sing(tmp_path):
    # #439 review: --resume keeps the work folder, so last run's WAV is there
    wav = tmp_path / "p1-high.wav"
    wav.write_text("last run's take")
    with pytest.raises(RuntimeError):
        publish_song.sing_remotely(runner_script(tmp_path, "exit 0\n"), "l.json", str(wav), "remy_adult",
                                   dict(os.environ))
    assert not wav.exists()


def test_a_stopped_runner_is_asked_to_end_its_job_first(tmp_path):
    # #439 review: the job over there runs detached; SIGKILL alone would leave it singing
    ended = tmp_path / "ended"
    runner = runner_script(tmp_path, f"trap 'touch {ended}; exit 143' TERM\nsleep 5 & wait\n")

    def here(job):
        time.sleep(0.3)
        raise SystemExit(143)

    def there(job):
        publish_song.sing_remotely(runner, "l.json", str(tmp_path / "o.wav"), "remy_adult", dict(os.environ))
    with pytest.raises(SystemExit):
        publish_song.share(["a", "b"], here, there, publish_song.stop_remote)
    assert ended.exists()


def test_a_runner_that_ignores_the_stop_is_killed_in_time(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_song, "REMOTE_STOP_S", 0.5)
    runner = runner_script(tmp_path, "trap '' TERM\nsleep 5\n")
    started = time.monotonic()

    def here(job):
        time.sleep(0.3)
        raise SystemExit(143)

    def there(job):
        publish_song.sing_remotely(runner, "l.json", str(tmp_path / "o.wav"), "remy_adult", dict(os.environ))
    with pytest.raises(SystemExit):
        publish_song.share(["a", "b"], here, there, publish_song.stop_remote)
    assert time.monotonic() - started < 3


# ---- #453: the singer version, and a run saying how it is going -------------------------------
from src.sing_song import progress  # noqa: E402


def test_the_singer_version_is_read_off_sing_ufr(tmp_path):
    assert isinstance(progress.singer_version(), int) and progress.singer_version() >= 1
    other = tmp_path / "sing_ufr.py"
    other.write_text("import os\nSINGER_VERSION = 7\nx = 1\n")
    assert progress.singer_version(str(other)) == 7
    other.write_text("import os\n")
    with pytest.raises(ValueError):
        progress.singer_version(str(other))


def test_a_line_sung_by_an_older_singer_is_sung_again_on_resume(tmp_path):
    work = str(tmp_path)
    (tmp_path / "p1-high.json").write_text("[]")
    (tmp_path / "p1-high.wav").write_bytes(b"RIFF")
    publish_song.mark_sung(work, 1, "high", "LYSE", 1)
    assert publish_song.sung_before(work, 1, "high", "LYSE", 1)
    assert not publish_song.sung_before(work, 1, "high", "LYSE", 2)


def song_folder(root):
    folder = root / "songs" / "laulu"
    folder.mkdir(parents=True)
    return folder


def test_waiting_only_marks_the_song_and_runs_nothing(tmp_path, monkeypatch):
    folder = song_folder(tmp_path)
    monkeypatch.setattr(publish_song, "build_bundle", lambda *a: pytest.fail("ran"))
    monkeypatch.setattr(publish_song, "ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["sing_song.py", "laulu", "--waiting", "--publish", "jm", "--publish", "public"])
    publish_song.main()
    run = progress.read(str(folder))
    assert run["state"] == "waiting" and run["choirs"] == ["jm", "public"]


def test_a_finished_run_says_done_with_its_time(tmp_path, monkeypatch):
    folder = song_folder(tmp_path)
    run_main(monkeypatch, tmp_path, "--no-sing")
    run = progress.read(str(folder))
    assert run["state"] == "done" and run["finished"] >= run["started"]
    assert run["version"] == progress.singer_version() and run["pid"] == os.getpid()
    assert run["sing"] is False   # --no-sing: the list does not call it a re-sing


def test_a_failed_run_says_why(tmp_path, monkeypatch):
    folder = song_folder(tmp_path)
    monkeypatch.setattr(publish_song, "ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["sing_song.py", "laulu", "--no-sing", "--out", str(tmp_path / "out")])
    monkeypatch.setattr(publish_song, "build_bundle", lambda *a: sys.exit("the score has 3 staves for 4 parts"))
    with pytest.raises(SystemExit):
        publish_song.main()
    run = progress.read(str(folder))
    assert run["state"] == "failed" and run["error"] == "the score has 3 staves for 4 parts"
    assert publish_song.failure(128 + 15) == "cancelled"


def test_each_choir_is_published_in_turn_with_the_singer_version(tmp_path, monkeypatch):
    folder = song_folder(tmp_path)
    seen = []

    def fake_publish(slug, choir, bundle_dir, manifest, sung_by, places, cleaned, version=None):
        seen.append((choir, version, progress.read(str(folder))))
    monkeypatch.setattr(publish_song, "publish_to_site", fake_publish)
    run_main(monkeypatch, tmp_path, "--no-sing", "--publish", "jm", "--publish", "public", "--publish", "jm")
    assert [(c, v) for c, v, _ in seen] == [("jm", progress.singer_version()), ("public", progress.singer_version())]
    assert [(r["state"], r["choir"], r["choir_index"], r["choir_count"]) for _, _, r in seen] == [
        ("publishing", "jm", 1, 2), ("publishing", "public", 2, 2)]


def test_a_run_whose_process_is_gone_reads_as_failed(tmp_path):
    progress.write(str(tmp_path), {"state": "singing", "pid": 2 ** 22 + 12345, "part": 2, "parts": 4})
    run = progress.read(str(tmp_path))
    assert run["state"] == "failed" and "without finishing" in run["error"]
    progress.write(str(tmp_path), {"state": "singing", "pid": os.getpid(), "part": 2, "parts": 4})
    assert progress.read(str(tmp_path))["state"] == "singing"
    assert progress.read(str(tmp_path / "nowhere")) is None


def test_remote_only_sings_every_line_on_the_runner_and_none_here():
    here, there = [], []
    publish_song.sing_lines([(1, "T1", "high", 3), (2, "T2", "high", 3)], singer(here), singer(there),
                            "runner", remote_only=True)
    assert here == [] and [j[1] for j in there] == ["T1", "T2"]


def test_remote_only_stops_on_a_line_the_runner_fails_and_needs_a_runner():
    here = []

    def broken(job):
        raise RuntimeError("the card is not free")
    with pytest.raises(SystemExit, match="did not sing T1: the card is not free"):
        publish_song.sing_lines([(1, "T1", "high", 3)], singer(here), broken, "runner", remote_only=True)
    assert here == []
    with pytest.raises(SystemExit, match="needs REMOTE_RUNNER"):
        publish_song.sing_lines([(1, "T1", "high", 3)], singer(here), broken, "", remote_only=True)


def test_remote_only_without_a_runner_is_refused_before_the_bundle(tmp_path, monkeypatch):
    monkeypatch.delenv("REMOTE_RUNNER", raising=False)
    monkeypatch.setattr(publish_song, "ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["sing_song.py", "laulu", "--remote-only"])
    monkeypatch.setattr(publish_song, "build_bundle", lambda *a: pytest.fail("built the bundle"))
    with pytest.raises(SystemExit, match="needs REMOTE_RUNNER"):
        publish_song.main()


def test_two_singers_reporting_at_once_both_land_and_leave_no_temp_files(tmp_path):
    # #454 review: the singers here and on the runner both report progress, from two threads
    import threading
    run = progress.Run(str(tmp_path), ["jm"], 1)
    errors = []

    def report(part):
        try:
            for _ in range(200):
                run.step("singing", part=part, parts=2)
        except Exception as e:   # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=report, args=(k,)) for k in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert progress.read(str(tmp_path))["state"] == "singing"
    assert [p.name for p in tmp_path.iterdir()] == [progress.RUN_FILE]


def test_remote_only_without_a_runner_turns_waiting_into_failed_with_the_reason(tmp_path, monkeypatch):
    folder = song_folder(tmp_path)
    progress.mark_waiting(str(folder), ["jm"])
    monkeypatch.delenv("REMOTE_RUNNER", raising=False)
    monkeypatch.setattr(publish_song, "ROOT", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["sing_song.py", "laulu", "--remote-only", "--publish", "jm"])
    monkeypatch.setattr(publish_song, "build_bundle", lambda *a: pytest.fail("built the bundle"))
    with pytest.raises(SystemExit):
        publish_song.main()
    run = progress.read(str(folder))
    assert run["state"] == "failed" and run["error"] == "--remote-only needs REMOTE_RUNNER in .env"
