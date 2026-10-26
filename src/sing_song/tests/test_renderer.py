"""The renderer's spelling rules, without the voice files: run the part of sing_ufr.py that
turns a syllable into sounds."""
import json, os, re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent.parent / "sing_ufr.py").read_text()


def rules(**env):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        ns = {"os": os, "re": re, "json": json}
        exec(SRC[SRC.index("V = {"):SRC.index("beat = 60 / bpm")], ns)
        return ns
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


@pytest.fixture(scope="module")
def fi():
    return rules(SONG_LANG="fi")


def test_benchmark_rules(fi):
    assert fi["split"]("pä") == (["it/p"], ["en/ae"], [])
    assert fi["split"]("jö") == (["de/j"], ["fr/oe"], [])
    assert fi["split"]("ve") == (["it/v"], ["nl/e"], [])


def test_loan_letters(fi):
    assert fi["split"]("lex") == (["it/l"], ["nl/e"], ["it/k", "it/s"])     # x is k + s
    assert fi["split"]("šak") == (["de/sh"], ["it/a"], ["it/k"])
    assert fi["split"]("žan") == (["en/zh"], ["it/a"], ["it/n"])
    assert fi["split"]("wal") == (["it/v"], ["it/a"], ["it/l"])


def test_syllables_without_a_vowel(fi):
    split = fi["split"]
    assert split("dn") == (["it/d"], ["en/ax", "it/n"], [])     # d, a short "uh", the n hummed
    assert split("dmm") == (["it/d"], ["en/ax", "it/m"], [])
    assert split("dng") == (["it/d"], ["en/ax", "de/ng"], [])
    assert split("gh") == (["it/g"], ["en/ax", "de/h"], [])
    assert split("mm") == ([], ["it/m"], [])                    # a plain hum stays a hum
    assert split("hmm") == (["de/h"], ["it/m"], [])
    assert split("k") == (["it/k"], ["en/ax", "SP"], [])     # a click: the consonant, a short "uh" (#449)
    assert split("kk") == (["it/k", "it/k"], ["en/ax", "SP"], [])
    assert split("tššš") == (["it/t"], ["de/sh"], [])       # a hiss holds its last hissing sound
    assert split("tsss") == (["it/t"], ["it/s"], [])


def test_a_syllable_of_only_stops_and_silence_is_silent(fi):
    silent = fi["silent"]
    assert not silent({"syl": "kk"})                      # sung k-uh now (#449)
    assert not silent({"syl": "dn"}) and not silent({"syl": "tsss"}) and not silent({"syl": "la"})
    assert silent({"syl": "kk", "ph": {"on": ["en/k"], "vs": ["SP"], "co": []}})
    assert silent({"syl": "t", "ph": {"on": ["en/t"], "vs": [], "co": []}})
    assert not silent({"syl": "the", "ph": {"on": ["en/dh"], "vs": ["en/ax"], "co": []}})


def test_norwegian_keeps_its_own_x(fi):
    no = rules(SONG_LANG="no")
    assert no["split"]("sjø") == (["de/sh"], ["de/oe"], [])
    assert no["split"]("lax")[2] == ["it/s"]


def test_syllable_repeated_only_without_slur_or_extender(fi):
    rep = fi["repeated"]
    bare = {"pitch": 60, "syl": None, "melisma": False}
    assert rep(bare, "duu", None)["syl"] == "duu"
    assert rep({**bare, "melisma": True}, "duu", None)["syl"] is None     # slurred: hold the vowel
    assert rep({"pitch": 60, "syl": None}, "duu", None)["syl"] is None    # older notes: as before
    assert rep(bare, None, None)["syl"] is None
    ph = {"on": ["en/d"], "vs": ["en/uw"], "co": []}
    assert rep(bare, "doo", ph)["ph"] == ph


def test_sounds_from_g2p_override_finnish_spelling(fi):
    ph = {"on": ["en/dh"], "vs": ["en/ax"], "co": []}
    assert fi["sounds"]({"syl": "the", "ph": ph}) == (["en/dh"], ["en/ax"], [])
    assert fi["sounds"]({"syl": "ta"}) == (["it/t"], ["it/a"], [])


def test_nasal_before_a_consonant_is_long():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 100}
    exec(SRC[SRC.index("V = {"):SRC.index("def put(")], ns)
    seq = ns["cons_seq"]
    assert seq(["it/n", "it/t"])[0] == ("it/n", 15)            # "rin-ta": as long as "nn"
    assert seq(["it/n", "it/n"]) == [("it/n", 15)]              # "nn"
    assert seq(["de/ng", "de/ng"]) == [("de/ng", 15)]           # "han-get" with NG: a long ng
    assert seq(["it/m", "it/p"])[0] == ("it/m", 20)             # "kam-pa": as long as "mm"
    assert seq(["it/n"]) == [("it/n", 5)]                       # "ni" onset or word end: short
    assert seq(["it/l", "it/t"])[0] == ("it/l", 5)              # only n, m and ng


def test_duration_model_sees_what_is_sung():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 60}
    exec(SRC[SRC.index("V = {"):SRC.index("def model_boundaries(")], ns)
    ph = {"on": ["en/d"], "vs": ["en/uw"], "co": []}
    notes = [{"pitch": 60, "beats": 1, "syl": "doo", "syllabic": "single", "ph": ph},
             {"pitch": 62, "beats": 1, "syl": None, "melisma": False},      # sung "doo" again
             {"pitch": 64, "beats": 1, "syl": None, "melisma": True},       # held
             {"pitch": None, "beats": 1, "syl": None},
             {"pitch": 60, "beats": 1, "syl": "ta", "syllabic": "single"}]
    first, second = ns["model_phrases"](notes)
    assert [(u["on"], u["vs"], u["frames"]) for u in first] == [
        (["en/d"], ["en/uw"], 86),                  # g2p.py's sounds, not the Finnish spelling
        (["en/d"], ["en/uw"], 172)]                 # the repeated syllable, holding the slurred note
    assert [(u["on"], u["vs"]) for u in second] == [(["it/t"], ["it/a"])]


def test_nasal_before_the_break_of_a_vowel_word_stays_short():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 100}
    exec(SRC[SRC.index("V = {"):SRC.index("def put(")], ns)
    assert ns["cons_seq"](["it/n", "de/q"])[0] == ("it/n", 5)   # "junaan | aina"


def test_consonants_borrow_only_from_the_note_before():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 100}
    exec(SRC[SRC.index("V = {"):SRC.index("def ng(")], ns)
    ns.update(toks=["it/a", "it/e"], durs=[30, 6], note_lo=1)          # an older note, then a short one
    ns["put"]([("it/cl", 5), ("it/t", 4)], 10)
    assert ns["durs"][0] == 30                                          # the older note keeps its time
    assert ns["durs"][1] == 3 and sum(ns["durs"][2:]) == 3              # the short note gives what it can
    assert ns["toks"][2:] == ["it/cl", "it/t"]
    ns.update(toks=["it/a", "it/e"], durs=[30, 2], note_lo=1)           # too short even for one frame each
    ns["put"]([("it/cl", 5), ("it/t", 4)], 10)
    assert ns["durs"] == [30, 1, 1] and ns["toks"] == ["it/a", "it/e", "it/t"]
    ns.update(toks=["it/a", "it/e"], durs=[30, 1], note_lo=1)           # a one-frame note: nothing fits
    ns["put"]([("it/cl", 5), ("it/t", 4)], 10)
    assert ns["durs"] == [30, 1] and ns["toks"] == ["it/a", "it/e"]


def test_a_short_vowel_closed_by_a_consonant_is_found():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 100}
    exec(SRC[SRC.index("V = {"):SRC.index("TIMING = ")], ns)
    short = ns["short_before_stop"]
    n = lambda syl, pitch=60, beats=1: {"pitch": pitch, "syl": syl, "beats": beats}
    rest = {"pitch": None, "syl": None}
    assert short(n("tip"), ["it/p"], n("tap"))                  # "tip-tap"
    assert short(n("tap."), ["it/p"], rest)                     # before a rest
    assert short(n("tap"), ["it/p"], None)                      # the last note
    assert not short(n("vat", beats=2), ["it/t"], rest)         # a long note holds its vowel
    assert short(n("nuk"), ["it/k"], n("kuu")) == "double"      # "huk-kuu": a double k
    assert short(n("var"), ["it/rr"], n("joon"))                # "var-joon"
    word = lambda syl: {**n(syl), "syllabic": "begin"}
    assert short(word("pit"), ["it/t"], n("kin")) == "late"     # "pit-kin": i held, t with the k
    assert short(word("tip"), ["it/p"], n("tap")) is True       # "tip-tap": short, as "tip tap"
    assert short(n("sil"), ["it/l"], n("lan"))                  # "sil-lan": the l held
    assert not short(n("tont"), ["it/n", "it/t"], n("tu"))      # two consonants: as before
    assert not short(n("dyyk"), ["it/k"], n("dong"))            # a long vowel stays long
    assert not short(n("pait"), ["it/t"], n("si"))              # and so does "ai"
    assert not short(n("tip"), ["it/p"], {"pitch": 62, "syl": None})   # slurred on
    assert not short(n("kah"), ["de/h"], n("vi"))               # not a stop, l, m, n, r or s
    assert not short(n("kaik"), ["it/k"], n("ki"))
    assert not short({**n("hat"), "ph": {"on": [], "vs": [], "co": []}}, ["en/t"], None)   # g2p's sounds


def planned(tmp_path, notes, **env):
    """The sounds and frames sing_ufr.py plans for `notes` (DUMP_TOKENS), without the voice
    files or onnxruntime: a stand-in UFR folder and stand-in modules."""
    import sys, types
    import numpy as np
    ufr = tmp_path / "ufr"
    (ufr / "core").mkdir(parents=True); (ufr / "tiger").mkdir()
    (ufr / "core" / "phonemes.json").write_text("{}")
    (ufr / "core" / "languages.json").write_text("{}")
    np.zeros(4, np.float32).tofile(ufr / "tiger" / "fresh.emb")
    line = tmp_path / "line.json"
    line.write_text(json.dumps(notes))
    dump = tmp_path / "dump.json"
    saved = {k: os.environ.get(k) for k in ("UFR_DIR", "DUMP_TOKENS", *env)}
    argv, mods = sys.argv, {m: sys.modules.get(m) for m in ("onnxruntime", "soundfile")}
    os.environ.update(UFR_DIR=str(ufr), DUMP_TOKENS=str(dump), **env)
    sys.argv = ["sing_ufr.py", str(line), str(tmp_path / "out.wav"), "60"]
    sys.modules.update(onnxruntime=types.ModuleType("onnxruntime"), soundfile=types.ModuleType("soundfile"))
    try:
        with pytest.raises(SystemExit):
            exec(SRC, {"__name__": "__main__"})
    finally:
        sys.argv = argv
        for m, v in mods.items():
            if v is None: sys.modules.pop(m, None)
            else: sys.modules[m] = v
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    d = json.loads(dump.read_text())
    return list(zip(d["toks"], d["durs"]))


def test_a_held_n_before_k_is_still_an_ng(tmp_path):
    # #451 review: "ken-kä" with its n held in the first note still sings keŋ-kä, as before
    notes = [{"pitch": 60, "beats": 1, "syl": "ken", "syllabic": "begin"},
             {"pitch": 62, "beats": 1, "syl": "kä", "syllabic": "end"}]
    toks = [t for t, _ in planned(tmp_path, notes, SONG_LANG="fi")]
    assert "de/ng" in toks and "it/n" not in toks
    i = toks.index("nl/e")
    assert toks[i + 1] == "de/ng"                       # held after the short e
    notes = [{"pitch": 60, "beats": 1, "syl": "ken", "syllabic": "begin"},
             {"pitch": 62, "beats": 1, "syl": "gät", "syllabic": "end"}]
    (tmp_path / "2").mkdir()
    toks = [t for t, _ in planned(tmp_path / "2", notes, SONG_LANG="fi")]
    assert "it/g" not in toks and toks.count("de/ng") >= 2   # "ken-gät": a long ng, as before


def test_a_closing_stop_on_a_long_note_keeps_the_vowel_and_a_short_gap(tmp_path):
    # #450: "nyt." stretched to 2.1 s by a fermata sang 0.25 s of y and 1.8 s of silence
    notes = [{"pitch": 60, "beats": 1, "sec": 2.1, "syl": "nyt.", "syllabic": "single"},
             {"pitch": 62, "beats": 1, "sec": 0.7, "syl": "ei", "syllabic": "single"}]
    plan = planned(tmp_path, notes, SONG_LANG="fi")
    toks = [t for t, _ in plan]
    i = toks.index("fr/uh")
    vowel, gap = plan[i][1], plan[i + 3][1]
    assert toks[i + 1:i + 4] == ["it/cl", "it/t", "SP"]
    assert vowel > 1.5 * 44100 / 512 and gap <= round(0.3 * 44100 / 512)


def test_the_short_vowel_rule_is_finnish_only():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 100}
    old = os.environ.get("SONG_LANG")
    os.environ["SONG_LANG"] = "no"
    try:
        exec(SRC[SRC.index("V = {"):SRC.index("TIMING = ")], ns)
    finally:
        if old is None: os.environ.pop("SONG_LANG")
        else: os.environ["SONG_LANG"] = old
    # Norwegian "tak" has a long vowel before its single k: timed as before, not cut short
    assert not ns["short_before_stop"]({"pitch": 60, "syl": "tak", "beats": 1}, ["it/k"], None)


def test_duration_model_sees_the_ng_that_is_sung():
    import numpy as np
    ns = {"os": os, "re": re, "json": json, "np": np, "FPS": 44100 / 512, "bpm": 60}
    exec(SRC[SRC.index("V = {"):SRC.index("def model_boundaries(")], ns)
    notes = [{"pitch": 60, "beats": 1, "syl": "ken", "syllabic": "begin"},
             {"pitch": 60, "beats": 1, "syl": "kä", "syllabic": "end"},
             {"pitch": 60, "beats": 1, "syl": "kan", "syllabic": "begin"},
             {"pitch": 60, "beats": 1, "syl": "gas", "syllabic": "end"}]
    (a, b, c, d), = ns["model_phrases"](notes)
    assert a["co"] == ["de/ng"] and b["on"] == ["it/k"]
    assert c["co"] == ["de/ng"] and d["on"] == ["de/ng"]


def test_empty_notes_hold_the_syllable_however_many(fi):
    n = lambda syl=None: {"pitch": 60, "beats": 1, "syl": syl, "melisma": False}
    rest = {"pitch": None, "beats": 1, "syl": None}
    out = fi["hold_runs"]([n("la"), n(), n(), rest, n("la"), n(), n(), n(), n(), n()])
    assert [x.get("melisma") for x in out] == [False, True, True, None, False, True, True, True, True, True]
    assert fi["repeated"](out[1], "la", None)["syl"] is None       # held
    assert fi["repeated"](out[9], "la", None)["syl"] is None       # still held on the fifth


def test_empty_notes_after_a_rest_sing_the_syllable_again_then_hold_it(fi):
    n = lambda syl=None: {"pitch": 60, "beats": 1, "syl": syl, "melisma": False}
    rest = {"pitch": None, "beats": 1, "syl": None}
    out = fi["hold_runs"]([n("la"), rest, n(), n(), n()])
    assert [x.get("melisma") for x in out] == [False, None, False, True, True]
    assert fi["repeated"](out[2], "la", None)["syl"] == "la"       # sung again
    assert fi["repeated"](out[3], "la", None)["syl"] is None       # then held


def test_notes_from_older_bundles_are_left_as_they_are(fi):
    out = fi["hold_runs"]([{"pitch": 60, "syl": "la"}, {"pitch": 62, "syl": None}])
    assert "melisma" not in out[1]


def chunking():
    import numpy as np
    ns = {"np": np}
    exec(SRC[SRC.index("OV = 32"):SRC.index("wins = windows(")], ns)
    return ns


def test_a_long_part_is_cut_at_its_rests():
    ns = chunking()
    toks = ["SP", "it/a", "SP", "it/o", "SP", "it/e", "SP"]
    durs = [10, 50, 20, 50, 20, 50, 10]
    assert ns["windows"](toks, durs, 100) == [(0, 70), (70, 140), (140, 210)]
    assert ns["clip"](toks, durs, 70, 140) == (["SP", "it/o", "SP"], [10, 50, 10])


def test_a_stretch_with_no_rest_is_cut_inside_its_longest_held_sound():
    # a loop sung for minutes (#104): the old cutter found no rest and sang it in one piece,
    # more than the 4 GB card holds
    ns = chunking()
    toks = ["SP", "it/k", "it/a", "it/t", "it/o", "it/a", "it/o", "SP"]
    durs = [10, 5, 80, 5, 120, 70, 90, 10]
    wins = ns["windows"](toks, durs, 250, ov=10)
    assert wins == [(0, 170), (150, 390)]        # the first o sounds at frames 100-220
    assert ns["clip"](toks, durs, 150, 390) == (["it/o", "it/a", "it/o", "SP"], [70, 70, 90, 10])


def test_a_note_longer_than_the_limit_is_cut_where_the_limit_runs_out():
    ns = chunking()
    wins = ns["windows"](["SP", "it/a", "SP"], [10, 1000, 10], 400, ov=10)
    assert wins == [(0, 410), (390, 800), (780, 1020)]
    assert all(b - a <= 420 for a, b in wins)


def test_a_rest_longer_than_the_limit_is_cut_in_silence_within_the_limit():
    # review of #108: the old cut went to the middle of the rest, ten times the limit
    ns = chunking()
    toks, durs = ["SP", "it/a", "SP", "it/o", "SP"], [10, 100, 12000, 100, 10]
    wins = ns["windows"](toks, durs, 600)
    assert all(b - a <= 600 for a, b in wins[:-1])
    assert all(b == a2 for (_, b), (a2, _) in zip(wins, wins[1:]))     # no overlap: cut in silence
    assert all(110 < b <= 12110 for _, b in wins[:-1])                 # every cut inside the rest
    assert wins[-1][1] == 12220


def test_a_piece_cut_inside_a_sound_is_sung_with_silence_beyond_the_cut():
    # eeron-omat-taskit#123: a held vowel cut between two pieces was sung as nothing at all
    import numpy as np
    ns = chunking()
    toks, durs = ["SP", "it/k", "it/a", "SP"], [10, 5, 100, 10]
    f0 = np.arange(125, dtype=np.float32)
    ts, ds, f, pre = ns["piece"](toks, durs, f0, 0, 70, edge=8)
    assert (ts, ds, pre) == (["SP", "it/k", "it/a", "SP"], [10, 5, 55, 8], 0)
    assert len(f) == 78 and f[-8:].tolist() == [69] * 8
    ts, ds, f, pre = ns["piece"](toks, durs, f0, 50, 125, edge=8)
    assert (ts, ds, pre) == (["SP", "it/a", "SP"], [8, 65, 10], 8)
    assert len(f) == 83 and f[:9].tolist() == [50] * 9
    assert ns["piece"](toks, durs, f0, 0, 125, edge=8)[3] == 0      # whole: nothing added


def test_overlapping_pieces_are_cross_faded_and_lose_no_frames():
    import numpy as np
    ns = chunking()
    wins = [(0, 6), (4, 10)]
    first, second = np.ones((1, 6, 2)), np.zeros((1, 6, 2))
    mel = ns["joined"]([first, second], wins)
    assert mel.shape == (1, 10, 2)
    assert mel[0, :, 0].tolist() == [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    wins = [(0, 4), (4, 9)]                         # cut at a rest: pieces only meet
    mel = ns["joined"]([np.ones((1, 4, 2)), 2 * np.ones((1, 5, 2))], wins)
    assert mel[0, :, 0].tolist() == [1, 1, 1, 1, 2, 2, 2, 2, 2]
