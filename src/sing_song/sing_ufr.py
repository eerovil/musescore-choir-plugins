"""Sing a part with the UFR multilingual DiffSinger model.

TIMING=model uses the bank's duration model to propose consonant lengths.  The score still
owns note boundaries: consonants are clipped and squeezed into the preceding note so every
vowel starts on its beat.  The default TIMING=flat keeps the original hand-timed baseline.
"""
import json, os, re, sys
# Bump when a change here (or in what main.py hands the singer: unroll, spoken, g2p, gate) changes
# how a song sounds. A sung publish records the version it was sung with, and the song app lists a
# song sung with an older one as waiting for a re-sing (#453). Read as text by
# src/sing_song/progress.py, so keep it one plain line.
SINGER_VERSION = 1
import numpy as np, onnxruntime as ort, soundfile as sf
D = os.environ.get("UFR_DIR", "/var/tmp/i36-ufr")
notes = json.load(open(sys.argv[1])); out = sys.argv[2]
bpm = float(sys.argv[3]) if len(sys.argv) > 3 else 76
spk = sys.argv[4] if len(sys.argv) > 4 else "fresh"
PH = json.load(open(f"{D}/core/phonemes.json")); LANG = json.load(open(f"{D}/core/languages.json"))
FPS = 44100 / 512
import os
V = {"a": "it/a", "e": "nl/e",   # e: nl/e beat it/e on both voices (#45)
     "i": "it/i", "o": "it/o", "u": "it/u",
     "y": os.environ.get("Y", "fr/uh"), "ö": os.environ.get("OE", "fr/oe"), "ä": os.environ.get("AE", "en/ae")}   # ä, ö: Eero's picks after the benchmark (#43)
V.update(json.loads(os.environ.get("VMAP", "{}")))
C = {"k": "it/k", "p": "it/p", "t": "it/t", "s": "it/s", "h": "de/h", "j": "de/j", "l": "it/l",
     "m": "it/m", "n": "it/n", "r": "it/rr", "v": "it/v", "d": "it/d", "g": "it/g", "b": "it/b",
     "f": "it/f", "N": "de/ng"}
# Loan letters in Finnish lyrics (names, "Venematka"'s w): read as a Finn says them.
C.update({"w": "it/v", "c": "it/k", "z": "it/s", "q": "it/k", "ʃ": "de/sh", "ʒ": "en/zh"})
LOAN = [("x", "ks"), ("š", "ʃ"), ("ž", "ʒ")]   # x is k + s ("Alex"), š and ž the sh and zh sounds ("šakki")
V.update({"å": "it/o", "ü": V["y"], "é": "it/e"})
C.update(json.loads(os.environ.get("CMAP", "{}")))
# SONG_LANG=no: Norwegian (Grieg's Dano-Norwegian texts), read from the spelling. y and ø borrow
# the German sounds, æ the English one; hv is v, kj is the German ich-sound, sj/skj is sh.
SONG_LANG = os.environ.get("SONG_LANG", "fi")
DIGRAPHS = []
if SONG_LANG == "no":
    V.update({"y": "de/ue", "ø": "de/oe", "æ": "en/ae", "å": "it/o", "u": "de/ue"})
    C.update({"ç": "de/ch", "c": "it/k", "z": "it/s", "w": "it/v", "x": "it/s", "q": "it/k"})
    LOAN = []
    DIGRAPHS = [("skj", "ʃ"), ("sj", "ʃ"), ("kj", "ç"), ("hv", "v"), ("hj", "j")]
VOW = "aeiouyäöæøåüé"
def split(syl):
    s = syl.lower()
    for a, b in LOAN: s = s.replace(a, b)
    s = re.sub(r"[^a-zåäöæøçʃʒüé]", "", s)
    for a, b in DIGRAPHS: s = s.replace(a, b)
    m = re.match(rf"^([^{VOW}]*)([{VOW}]+)([^{VOW}]*)$", s)
    if not m:
        # no vowel (#45): a hum ("mm", "dn") holds its last humming consonant after the ones
        # before it, a hiss ("tsss") its last s, f or h, and a click ("k", "kk") is the consonant,
        # a short "uh" and silence (#449: a bare "kk" was heard as no sound at all). After a stop,
        # a hum or an h starts with a short "uh" ("dn" is d-uh-nnn, "gh" g-uh-hhh): Eero's pick by ear
        cs = [c for c in s.replace("ng", "N") if c in C]
        if not cs: return [], ["it/s"], []
        hold = next((i for i in reversed(range(len(cs))) if cs[i] in "mnlrN"), None)
        if hold is None: hold = next((i for i in reversed(range(len(cs))) if cs[i] in "sfhzʃʒç"), None)
        if hold is None: return [C[c] for c in cs], ["en/ax", "SP"], []
        first = hold
        while first > 0 and cs[first - 1] == cs[hold]: first -= 1      # "mm", "tsss": one held sound
        schwa = cs[hold] in "mnlrNh" and any(c in "kptdgb" for c in cs[:first])
        return [C[c] for c in cs[:first]], (["en/ax"] if schwa else []) + [C[cs[hold]]], [C[c] for c in cs[hold + 1:]]
    on, nuc, co = m.groups()
    vs = []
    for ch in nuc:
        if not vs or V[ch] != vs[-1]: vs.append(V[ch])
    return [C[c] for c in on], vs, [C[c] for c in co.replace("ng", "N")]
def repeated(n, last_syl, last_ph):
    """The first note after a rest with no syllable, no slur and no extender over it: there is
    no vowel to hold, so the singers sing the last syllable again ("duu"); hold_runs marks the
    notes after it held."""
    if not n.get("syl") and "melisma" in n and not n["melisma"] and last_syl:
        return dict(n, syl=last_syl, syllabic="single", **({"ph": last_ph} if last_ph else {}))
    return n
def hold_runs(notes):
    """Notes in a row with no syllable carry the last syllable on, held at the new pitch as under
    a slur, however many there are (stemmanauhat-cf#125; was one or two, #45). After a rest there
    is no vowel to hold, so the first of them sings the syllable again and the rest hold it."""
    out, k = [dict(n) for n in notes], 0
    while k < len(out):
        j = k
        while j < len(out) and out[j]["pitch"] is not None and not out[j].get("syl"): j += 1
        after_rest = k == 0 or out[k - 1]["pitch"] is None
        for n in out[k + after_rest:j]:
            if "melisma" in n: n["melisma"] = True
        k = max(j, k + 1)
    return out
def sounds(n):
    """(onset, vowels, coda): g2p.py's sounds for other languages, else the Finnish spelling rules."""
    return (n["ph"]["on"], n["ph"]["vs"], n["ph"]["co"]) if n.get("ph") else split(n["syl"])
# a syllable whose held sound is only stops and silence is not heard at all (#449: a bare "kk"
# was sung as k, then silence). SILENT=<file> below lists such notes before anything is sung
QUIET = {"SP", "AP", "it/cl", "de/q"} | {f"{l}/{x}" for l in ("it", "en", "de") for x in "kptdbg"}
def silent(n):
    return all(p in QUIET for p in sounds(n)[1])
beat = 60 / bpm
STOPS = {f"{l}/{x}" for l in ("it", "en", "de") for x in "kptd"} | set(filter(None, os.environ.get("CLOSE", "").split(",")))
STOPS -= set(filter(None, os.environ.get("NOCLOSE", "").split(",")))   # NOCLOSE=it/d: no closure before d
# frames of silence for a double stop ("tt"); a single one gets 5. 18 ranked first for tt and kk (#43)
DBL_CLOSE = int(os.environ.get("DBL_CLOSE", "18"))
LONG_SHARE = float(os.environ.get("LONG_SHARE", "0.2"))   # ... and after a long vowel ("naan"): it stays long
DBL_SHARE = float(os.environ.get("DBL_SHARE", "0.65"))   # share of the note a double stop may take
S_MIN = int(os.environ.get("S_MIN", "5"))   # frames an s keeps before the next vowel, however short the note (#61)
SGL_CLOSE = int(os.environ.get("SGL_CLOSE", "3"))   # frames of silence before a single stop (#45: 5 made "o-do" pause; Eero picked 3 by ear)
# a double n, s, m...: this many times a single one (#45: 3x, 2.5x and 4x ranked first on both
# voices; no length tried made ll heard as double). DBL_LEN=<x> sets one length for all.
DBL_LEN = ({} if "DBL_LEN" not in os.environ else {"*": float(os.environ["DBL_LEN"])}) or \
    {"it/n": 3, "it/s": 2.5, "it/m": 4, "de/ng": 3, "*": 2}
# an n, m or ng that closes a syllable before another consonant ("rin-ta", "kam-pa") is held as
# long as a double one; "han-get" is a double ng (Eero, #45). NASAL_LONG=0: as before.
NASAL_LONG = os.environ.get("NASAL_LONG", "1") == "1"
NASALS = {"it/n", "it/m", "de/ng"}
DIPH = float(os.environ.get("DIPH", "0.5"))       # share of the note the first vowel of "ai", "uo" gets (#45: 0.7 lost the second vowel)
SCHWA = 0.3                                        # the "uh" of a scat "dn": share of the note
CLICK_UH = float(os.environ.get("CLICK_UH", "0.25"))   # seconds of the "uh" after a bare click ("kk", #449/#450: a third of an eighth was barely heard)
HUMS = {"it/m", "it/n", "de/ng", "it/l", "it/rr", "de/h"}
NG = os.environ.get("NG", "1") == "1"            # n before k is the ng sound, and "n-g" a long ng (Eero's pick by ear, #45); NG=0: plain n
CLEN = {"de/q": 4, "it/s": 9, "de/h": 6, "it/rr": 6, "it/cl": 5}   # consonant lengths in frames (~11.6 ms)
CLIP = {
    **{f"it/{c}": (0.03, 0.065) for c in "bdg"},
    **{f"it/{c}": (0.04, 0.13) for c in "ptk"},
    **{f"it/{c}": (0.06, 0.15) for c in "mn"}, "de/ng": (0.07, 0.20),
    **{f"it/{c}": (0.055, 0.18) for c in "fs"}, "de/h": (0.055, 0.18),
    "it/v": (0.05, 0.15),
    **{f"it/{c}": (0.04, 0.13) for c in ("l", "rr", "y")}, "de/j": (0.04, 0.13),
    "de/q": (0.02, 0.08),
}
# a short vowel closed by a stop ("tip", "tap") on a note of at most STOP_BEATS beats is sung
# STOP_VOWEL seconds, then the stop, then silence to the next note, rather than held to the end of
# the note ("tiip"). Eero's pick by ear on #450 (0.15 s was too short for "dut", "nuk"); a longer
# note ("set," on a half) holds. 0: held.
STOP_VOWEL = float(os.environ.get("STOP_VOWEL", "0.25"))
STOP_BEATS = float(os.environ.get("STOP_BEATS", "1"))
# the same for a short vowel closed by l, m, n, r or s ("var-joon" sang "vaar-joon", "sil-lan"
# "siil-lan"): the vowel gets CODA_VOWEL seconds (or CODA_SHARE of the note) and the consonant is
# held to the next syllable; CODA_HOLD=0 falls silent after it instead. CODA= turns it off.
CODA = set(filter(None, os.environ.get("CODA", "it/rr,it/l,it/n,it/m,it/s").split(",")))
CODA_VOWEL = float(os.environ.get("CODA_VOWEL", str(STOP_VOWEL)))
CODA_SHARE = float(os.environ.get("CODA_SHARE", "0"))
CODA_HOLD = os.environ.get("CODA_HOLD", "1") == "1"
# ... and is held at most CODA_MAX seconds, the vowel taking the rest: on a slow beat it took the
# whole note ("hosss-tan-sa" in Metsänneito, #450)
CODA_MAX = float(os.environ.get("CODA_MAX", "0.25"))
def short_before_stop(n, co, nxt):
    """A Finnish syllable with one short vowel closed by one stop, or by l, m, n, r or s, on a short
    note with no slur holding it on: True, or "double" for a stop doubled into the next syllable
    ("huk-kuu"), whose closure the next syllable sings."""
    if SONG_LANG != "fi": return False               # Norwegian "tak" has a long a before its k
    if n.get("beats", 0) > STOP_BEATS or n.get("ph") or len(co) != 1 or co[0] not in STOPS | CODA: return False
    if len(re.findall(rf"[{VOW}]", n["syl"].lower())) != 1: return False
    if nxt is None or nxt["pitch"] is None: return True
    if not nxt.get("syl"): return False              # held on into the next note
    on = sounds(nxt)[0]
    own = sounds(n)[0]
    if MID_STOP and co[0] in STOPS and on and on[0] != co[0] and n.get("syllabic") in ("begin", "middle") \
            and not (own and own[0] == on[0]):      # "tip-tap", "tik-tak" stay short
        return "late"                                # "pit-kin" sang "pittt-kin" (#450)
    if on and on[0] == co[0]:                        # "huk-kuu" sang "huuk-kuu" (#450)
        if co[0] in STOPS: return "double" if DOUBLE_STOP else False
        return True                                  # "sil-lan": short i, then the l (CODA_HOLD says how)
    return True
# a stop inside a word before another consonant ("pit-kin") keeps the vowel MID_SHARE of the note
# and is sung with that consonant just before the next beat: a gap after the t made it "pittt-kin"
# (Eero's pick by ear, #450). A syllable sung again ("tip-tap") keeps the short vowel. MID_STOP=0: off
MID_STOP = os.environ.get("MID_STOP", "1") == "1"
MID_SHARE = float(os.environ.get("MID_SHARE", "0.85"))
# the silence of a doubled stop, its closure included, is at most DBL_GAP seconds and the vowel
# takes the rest ("vet-ten" on a slow quarter had 0.45 s, Eero's pick by ear, #450). 0: no limit
DBL_GAP = float(os.environ.get("DBL_GAP", "0.25"))
DOUBLE_STOP = os.environ.get("DOUBLE_STOP", "1") == "1"   # "huk-kuu": a doubled stop keeps its vowel short too
TIMING = os.environ.get("TIMING", "flat")
# GPU=1 runs on an NVIDIA card (the GTX 970 needs onnxruntime-gpu 1.17 with CUDA 11; see the note)
PROVIDERS = ["CUDAExecutionProvider", "CPUExecutionProvider"] if os.environ.get("GPU") else ["CPUExecutionProvider"]
def cons_seq(cs, proposed=None):
    """Consonants sung between two vowels. Stops (k, p, t and d) get a closure (silence) before them, as
    DiffSinger voices expect; a doubled stop (the "tt" in "koittaa") gets one longer closure."""
    seq = []
    proposed = list(proposed or [])
    proposed += [None] * (len(cs) - len(proposed))
    for i, (c, pred) in enumerate(zip(cs, proposed)):
        model_d = None
        if pred is not None:
            lo, hi = CLIP.get(c, (0.04, 0.15))
            model_d = max(1, int(round(np.clip(pred, lo, hi) * FPS)))
        if c in STOPS:
            if i + 1 < len(cs) and cs[i + 1] == c: continue        # first half of a double stop
            dbl = i > 0 and cs[i - 1] == c
            seq.append(("it/cl", DBL_CLOSE if dbl else SGL_CLOSE)); seq.append((c, model_d or 4))
        elif i > 0 and cs[i - 1] == c:
            seq[-1] = (c, seq[-1][1] + (model_d or int(round(seq[-1][1] * (DBL_LEN.get(c, DBL_LEN["*"]) - 1)))))   # double l, n, s...: longer
        elif NASAL_LONG and c in NASALS and i + 1 < len(cs) and cs[i + 1] not in (c, "de/q") and not model_d:   # not before the break of a vowel word ("junaan | aina")
            seq.append((c, int(round(CLEN.get(c, 5) * DBL_LEN.get(c, DBL_LEN["*"])))))
        else:
            seq.append((c, model_d or CLEN.get(c, 5)))
    return seq
def put(seq, budget=None):
    """Place consonants just before the beat, taking their time from the note before them only
    (its sounds from index note_lo on), so no older note is shortened. Each sound keeps 3 frames
    when it can, else 1; on a note too short for all the consonants, those nearest the beat stay."""
    if not seq: return
    spare = lambda keep: sum(max(durs[i] - keep, 0) for i in range(note_lo, len(durs)))
    keep = 3 if spare(3) >= len(seq) else 1
    room = spare(keep)
    if room == 0: return                 # a note with no frame to spare: no consonants fit
    if room < len(seq): seq = seq[len(seq) - room:]
    budget = room if budget is None else min(room, budget)
    budget = max(len(seq), budget)      # the acoustic model needs at least one frame per token
    if sum(d for _, d in seq) > budget:
        scale = budget / sum(d for _, d in seq)
        seq = [(c, max(1, int(round(d * scale)))) for c, d in seq]
        while sum(d for _, d in seq) > budget:
            j = max(range(len(seq)), key=lambda x: seq[x][1])
            c, d = seq[j]; seq[j] = (c, d - 1)
    need, i = sum(d for _, d in seq), len(durs) - 1
    while need > 0 and i >= note_lo:     # borrow from the note's last sound, then its earlier ones
        take = min(need, max(durs[i] - keep, 0)); durs[i] -= take; need -= take; i -= 1
    assert need == 0, ("consonants do not fit before beat", seq, budget)
    for c, d in seq: toks.append(c); durs.append(d)

def ng(boundary):
    """The consonants between two vowels as Finns say them: with NG, "kenkä" is keŋ-kä and
    "kengät" keŋ-ŋät."""
    if not NG: return boundary
    return [("de/ng" if c == "it/n" and nxt in ("it/k", "it/g") else "de/ng" if c == "it/g" and prv in ("it/n", "de/ng") else c)
            for prv, c, nxt in zip([None] + boundary, boundary, boundary[1:] + [None])]
def model_phrases(notes):
    """The sung syllables by phrase, as the rendering loop below sings them (repeated syllables,
    g2p.py's sounds): the duration model's input."""
    phrases, current = [], []
    tsec = 0.0; last_syl = last_ph = None
    for i, n in enumerate(notes):
        nb = n["sec"] if n.get("sec") else n["beats"] * beat
        nf = int(round((tsec + nb) * FPS)) - int(round(tsec * FPS)); tsec += nb
        if n["pitch"] is None:
            if current: phrases.append(current); current = []
            continue
        n = repeated(n, last_syl, last_ph)
        if not n.get("syl"):
            if current: current[-1]["frames"] += nf
            continue
        on, vs, co = sounds(n)
        last_syl, last_ph = n["syl"], n.get("ph")
        current.append({"index": i, "on": on, "vs": vs, "co": co, "midi": n["pitch"], "frames": nf})
    if current: phrases.append(current)
    for phrase in phrases:                # the same ng as the rendering loop sings
        for u, v in zip(phrase, phrase[1:]):
            b = ng(u["co"] + v["on"]); u["co"], v["on"] = b[:len(u["co"])], b[len(u["co"]):]
    return phrases
def model_boundaries(notes, emb):
    """Return duration-model proposals for each note's preceding consonants and final coda."""
    if TIMING != "model": return {}, None
    phrases = model_phrases(notes)
    ling = ort.InferenceSession(f"{D}/dsdur/millefeuille_v001.linguistic.onnx", providers=PROVIDERS)
    dur = ort.InferenceSession(f"{D}/dsdur/millefeuille_v001.dur.onnx", providers=PROVIDERS)
    out = {}
    for phrase in phrases:
        words = [["SP"] + phrase[0]["on"]]
        for i, u in enumerate(phrase):
            words.append(u["vs"] + u["co"] + (phrase[i + 1]["on"] if i + 1 < len(phrase) else []))
        word_dur = [max(1, int(0.4 * FPS))] + [u["frames"] for u in phrase]
        midi = [phrase[0]["midi"]] + [u["midi"] for u in phrase]
        phs = [p for word in words for p in word]
        lang = [LANG.get(p.split("/")[0], 0) if "/" in p else 0 for p in phs]
        enc, mask = ling.run(None, {"tokens": np.array([[PH[p] for p in phs]], np.int64),
            "languages": np.array([lang], np.int64),
            "word_div": np.array([[len(w) for w in words]], np.int64),
            "word_dur": np.array([word_dur], np.int64)})
        pred = dur.run(None, {"encoder_out": enc, "x_masks": mask,
            "ph_midi": np.array([[m for word, m in zip(words, midi) for _ in word]], np.int64),
            "spk_embed": np.tile(emb, (1, len(phs), 1)).astype(np.float32)})[0][0] / FPS
        by_word, k = [], 0
        for word in words:
            by_word.append(pred[k:k + len(word)].tolist()); k += len(word)
        out[phrase[0]["index"]] = by_word[0][1:]
        for i, u in enumerate(phrase):
            start = len(u["vs"])
            out[(u["index"], "coda")] = by_word[i + 1][start:start + len(u["co"])]
            if i + 1 < len(phrase):
                out[phrase[i + 1]["index"]] = by_word[i + 1][start + len(u["co"]):]
    return out, phrases

if os.environ.get("SILENT"):     # [{bar, syl}] of the notes silent() finds, then stop
    json.dump([{"bar": n.get("bar"), "syl": n["syl"]} for n in notes if n.get("pitch") is not None
               and n.get("syl") and silent(n)], open(os.environ["SILENT"], "w"), ensure_ascii=False)
    sys.exit(0)
emb = np.fromfile(f"{D}/tiger/{spk}.emb", dtype=np.float32)
dur_emb_path = f"{D}/dsdur/millefeuille_v001.{spk}.emb"
dur_emb = np.fromfile(dur_emb_path, dtype=np.float32)[None, None, :] if TIMING == "model" else None
notes = hold_runs(notes)
model_durs, _ = model_boundaries(notes, dur_emb)
toks, durs, f0n = ["SP"], [int(0.4 * FPS)], []
note_lo = 0
t = durs[0]; pending = []; pending_pred = []; breath = False; tsec = 0.0; prev_nf = durs[0]
last_syl = last_ph = None; prev_long = False
held = []      # a coda already sung in its own note ("ken-kä"'s n): the next onset's ng context
BREATH = 0.5   # beats of breath after a comma or full stop on a long note          # coda consonants of the previous syllable
for ni, n in enumerate(notes):
    nb = n["sec"] if n.get("sec") else n["beats"] * beat   # seconds: the score's own tempo marks when it has them
    # frames from the running total, so rounding never piles up (it added 0.4 s over a 3-minute song)
    nf = int(round((tsec + nb) * FPS)) - int(round(tsec * FPS)); tsec += nb
    if n["pitch"] is None:
        seq = cons_seq(pending, pending_pred); pending = []; pending_pred = []
        k = sum(d for _, d in seq)
        if TIMING != "model" and seq and nf - k >= 3:
            # the word's last consonants ("viis") go into the rest, so the vowel keeps its whole note (#45)
            for c, d in seq: toks.append(c); durs.append(d)
            note_lo = len(toks); toks.append("SP"); durs.append(nf - k)
        else:
            put(seq, max(1, prev_nf // 2))
            note_lo = len(toks); toks.append("SP"); durs.append(nf)
        f0n.append((t, t + nf, None)); t += nf; prev_nf = nf; prev_long = False; held = []; continue
    n = repeated(n, last_syl, last_ph)
    if not n.get("syl"):
        # melisma: a note without its own syllable keeps singing the vowel at the new pitch
        durs[-1] += nf
        f0n.append((t, t + nf, n.get("hz") or 440 * 2 ** ((n["pitch"] - 69) / 12))); t += nf; prev_nf += nf
        continue
    on, vs, co = sounds(n)
    last_syl, last_ph = n["syl"], n.get("ph")
    word_start = n.get("syllabic") in ("single", "begin")
    if breath:
        # phrase end: finish the last word, then a short breath before the next one
        ap = int(BREATH * (n["sec"] / n["beats"] if n.get("sec") else beat) * FPS)
        put(cons_seq(pending, pending_pred) + [("AP", ap)]); pending = []; pending_pred = []
    if word_start and not on and not pending and toks[-1] not in ("AP", "SP"):
        on = ["de/q"]                     # glottal stop: "koittaa | yön" stay two words
    # coda of the previous syllable + this onset all sound just before the beat
    boundary = ng(held + pending + on)[len(held):]; held = []
    boundary_pred = pending_pred + model_durs.get(ni, [])
    # the consonants take at most half of the note before them, a fifth after a long vowel
    # ("ju-naan-ko": the aa stays long), so short notes keep their vowels (#45)
    # a double stop ("pap-pap", "tyttö") may take more: its silence is what makes it heard
    seq = cons_seq(boundary, boundary_pred)
    share = DBL_SHARE if any(c == "it/cl" and d >= DBL_CLOSE for c, d in seq) else (LONG_SHARE if prev_long else 1 / 2)
    budget = max(1, int(prev_nf * share))
    # an s squeezed by that share into a frame or two is not heard at all ("tar-pees-ta" sang
    # "pee-ta", #61): give the consonants room for S_MIN frames of s, up to half the note
    s_d = sum(d for c, d in seq if c == "it/s")
    if s_d and budget * s_d / sum(d for _, d in seq) < S_MIN:
        budget = max(budget, min(prev_nf // 2, -(-S_MIN * sum(d for _, d in seq) // s_d)))
    put(seq, budget)
    assert sum(durs) == t, ("vowel moved off its beat", ni, sum(durs), t)
    breath = bool(re.search(r"[,.;!?]", n["syl"])) and n["beats"] >= 2
    if len(vs) == 1: vd = [nf]
    else:
        share = SCHWA if vs[0] == "en/ax" and (vs[1] in HUMS or vs[1] == "SP") else DIPH
        first = int(nf * share)
        if vs == ["en/ax", "SP"]: first = max(first, min(nf - 1, int(round(CLICK_UH * FPS))))
        rest = (nf - first) // (len(vs) - 1)
        vd = [first] + [rest] * (len(vs) - 1); vd[-1] += nf - sum(vd)
    note_lo = len(toks)
    closed = STOP_VOWEL and len(vs) == 1 and short_before_stop(n, co, notes[ni + 1] if ni + 1 < len(notes) else None)
    stop = cons_seq(co) if closed and closed not in ("double", "late") else []
    if closed == "late":
        vf = int(nf * MID_SHARE) if MID_SHARE else int(round(STOP_VOWEL * FPS))
        closed = "double"
    else: vf = int(round(STOP_VOWEL * FPS))
    if stop and co[0] not in STOPS:
        vf = int(nf * CODA_SHARE) if CODA_SHARE else int(round(CODA_VOWEL * FPS))
    if closed == "double" and DBL_GAP: vf = max(vf, nf - int(round(DBL_GAP * FPS)))
    if closed == "double" and nf - vf >= 3:
        # "huk-kuu": the vowel, then silence; the double k's closure and release come before the
        # next beat as before, taken out of that silence
        toks += vs + ["SP"]; durs += [vf, nf - vf]
    elif stop and co[0] not in STOPS and CODA_HOLD and nf - vf >= 3:
        if CODA_MAX: vf = max(vf, nf - int(round(CODA_MAX * FPS)))
        nxt = notes[ni + 1] if ni + 1 < len(notes) else None
        on_next = sounds(nxt)[0] if nxt and nxt["pitch"] is not None and nxt.get("syl") else []
        c = ng(co + on_next)[0]           # "ken-kä": the held n is an ng
        toks += vs + [c]; durs += [vf, nf - vf]; co = []; held = [c]
    elif stop and nf - vf - sum(d for _, d in stop) >= 3:
        # "tip": the vowel, the stop, then silence until the next syllable's consonants (#450)
        toks += vs + [c for c, _ in stop] + ["SP"]
        durs += [vf] + [d for _, d in stop] + [nf - vf - sum(d for _, d in stop)]
        co = []
    else:
        toks += vs; durs += vd
    pending = co; pending_pred = model_durs.get((ni, "coda"), []) if co else []
    hz = n.get("hz") or 440 * 2 ** ((n["pitch"] - 69) / 12)
    f0n.append((t, t + nf, hz)); t += nf; prev_nf = nf
    prev_long = bool(re.search(r"([aeiouyäö])\1", n["syl"].lower()))
tail = cons_seq(pending, pending_pred)          # the song's last consonants: after its last note
if TIMING == "model": put(tail, max(1, prev_nf // 2)); tail = []
assert min(durs) > 0, ("a sound got no time", [(toks[i], d) for i, d in enumerate(durs) if d <= 0], toks[:12], durs[:12])
assert sum(durs) == t, ("phoneme timing changed score length", sum(durs), t)
for c, d in tail: toks.append(c); durs.append(d)
toks.append("SP"); durs.append(max(8, int(0.5 * FPS) - sum(d for _, d in tail)))
N = sum(durs); f0 = np.zeros(N)
last = next(hz for _, _, hz in f0n if hz)       # a part may start with a rest
f0[: f0n[0][0]] = last
for s, e, hz in f0n:
    hz = hz or last; f0[s:e] = hz; last = hz
f0[f0n[-1][1]:] = last
assert np.isfinite(f0).all() and (f0 > 0).all(), "bad pitch curve"
lf = np.convolve(np.pad(np.log2(f0), 3, mode="edge"), np.ones(7) / 7, "valid")
f0 = (2 ** lf).astype(np.float32)
if os.environ.get("DUMP_TOKENS"):        # the sounds and their frames, without singing (tests, #61)
    json.dump({"toks": toks, "durs": [int(d) for d in durs]}, open(os.environ["DUMP_TOKENS"], "w")); sys.exit(0)
ac = ort.InferenceSession(f"{D}/core/acoustic.onnx", providers=PROVIDERS)
def sing(toks, durs, f0):
    n = sum(durs); lang = [LANG.get(x.split("/")[0], 0) if "/" in x else 0 for x in toks]
    return ac.run(None, {"tokens": np.array([[PH[x] for x in toks]], np.int64),
        "languages": np.array([lang], np.int64), "durations": np.array([durs], np.int64),
        "f0": f0[None], "gender": np.full((1, n), float(os.environ.get("GENDER", "0")), np.float32), "velocity": np.ones((1, n), np.float32),
        "spk_embed": np.tile(emb, (1, n, 1)).astype(np.float32),
        "depth": np.array(0.6, np.float32), "steps": np.array(20, np.int64)})[0]
OV = 32     # frames on each side of a cut with no rest: both pieces sing them, cross-faded
def windows(toks, durs, limit, ov=OV):
    """The frames [a, b) to sing at a time, at most about `limit` long, as OpenUtau sings phrase
    by phrase: a cut halves a rest, so the pieces meet in silence (a rest longer than `limit` is
    cut as often as it needs, still in silence). A stretch longer than `limit`
    with no rest (a held cluster, a loop sung for minutes, #104) is cut in the middle of its
    longest held sound instead, or failing that where the limit runs out; there the two pieces
    overlap by 2 * ov frames, which joined() cross-fades."""
    wins, start, t, rest, held = [], 0, 0, None, None
    for i, (p, d) in enumerate(zip(toks, durs)):
        if p == "SP" and 0 < i < len(toks) - 1 and d >= 8: rest = (t + d // 2, t + d)
        elif p != "SP" and d >= 2 * ov and start + ov < t + d // 2 <= start + limit:
            if held is None or d > held[1]: held = (t + d // 2, d)
        t += d
        while t - start > limit:        # a while: one held note may run past the limit twice
            if rest and rest[1] > start:     # its middle, or the rest's end once past it
                c = min(rest[0] if rest[0] > start else rest[1], start + limit)
                wins.append((start, c)); start = c
            else:
                c = held[0] if held else start + limit
                wins.append((start, min(c + ov, sum(durs)))); start = c - ov
                rest = None
            held = None
    return wins + [(start, sum(durs))]
def clip(toks, durs, a, b):
    """The sounds of frames [a, b): the ones a cut runs through keep only their frames inside."""
    ts, ds, t = [], [], 0
    for p, d in zip(toks, durs):
        lo, hi = max(t, a), min(t + d, b)
        if hi > lo: ts.append(p); ds.append(hi - lo)
        t += d
    return ts, ds
def joined(parts, wins):
    """The pieces' mels in one, cross-fading where two pieces sang the same frames."""
    mel = np.zeros(parts[0].shape[:1] + (wins[-1][1],) + parts[0].shape[2:], parts[0].dtype)
    end = 0
    for m, (a, b) in zip(parts, wins):
        k = end - a
        if k > 0:
            fade = np.linspace(0, 1, k, dtype=mel.dtype).reshape((1, k) + (1,) * (mel.ndim - 2))
            mel[:, a:end] = mel[:, a:end] * (1 - fade) + m[:, :k] * fade
        mel[:, max(a, end):b] = m[:, max(k, 0):]; end = b
    return mel
wins = windows(toks, durs, int(os.environ.get("AC_CHUNK", "0")) or 10**9)
mel = joined([sing(*clip(toks, durs, a, b), f0[a:b]) for a, b in wins], wins)
assert mel.shape[1] == N, ("pieces lost frames", mel.shape, N)
del ac
voc = ort.InferenceSession(f"{D}/core/potluck.onnx", providers=PROVIDERS)
def vocode(mel, f0):
    """The vocoder on a whole song needs more than a 4 GB card has: run it in pieces of
    VOC_CHUNK frames that overlap by 32 frames, and cross-fade the overlaps."""
    step = int(os.environ.get("VOC_CHUNK", "0")) or mel.shape[1]
    ov, hop = 32, 512
    out = np.zeros(mel.shape[1] * hop, np.float32)
    for a in range(0, mel.shape[1], step):
        lo, hi = max(0, a - ov), min(mel.shape[1], a + step)
        w = voc.run(None, {"mel": mel[:, lo:hi], "f0": f0[None, lo:hi]})[0][0][: (hi - lo) * hop]
        if lo < a:
            k = (a - lo) * hop; fade = np.linspace(0, 1, k, dtype=np.float32)
            out[lo * hop: a * hop] = out[lo * hop: a * hop] * (1 - fade) + w[:k] * fade
            w = w[k:]
        out[a * hop: a * hop + len(w)] = w
    return out
wav = vocode(mel, f0)
sf.write(out, wav, 44100); print(out, round(len(wav) / 44100, 1), f"s; {TIMING} timing;", " ".join(toks))
if os.environ.get("PRINT_DURS"):
    print(" ".join(f"{p}:{d}" for p, d in zip(toks, durs)))
