"""Give each sung note its sounds in REMY/LYSE's phoneme set, for songs not in Finnish.

    python g2p.py <lang> <notes.json>      (en, de, sv, la, la-de, et; rewrites the file)

Each note with a syllable gets "ph": {"on": [...], "vs": [...], "co": [...]} (onset consonants,
vowels, coda consonants), which sing_ufr.py uses instead of its Finnish spelling rules.
English goes through the CMU pronouncing dictionary (g2p_en) word by word, and each word's
sounds are dealt over its syllables: one vowel sound per syllable, the consonants between two
vowels split between them. The others read the spelling of each syllable with that language's
rules (German and Swedish with the voice's German sounds, church Latin with its Italian ones,
Estonian like Finnish plus õ, ü, š, ž). English AY, AW and OY are sung as two vowels (#484).
Each language's vowels, and a variant's rules over its base's ("la-de", Latin as German choirs
sing it), are in languages.py beside this file, loaded by path since this runs with python -I.

A word's reading can be chosen (eeron-omat-taskit#143, #144, #484), strongest first:
- at one place in the song, on song-app's song page: SONG_PRONOUNCE names the song's
  pronounce.json (places.py), whose "places" give a reading for the word starting at a bar and
  beat (unroll.py's "bar" and "beat"), whatever part sings it, on every pass;
- everywhere in the song, the same file's "everywhere" (by language, then word);
- for every song a choir publishes, English only: CHOIR_WORDS lists choirs/<choir>.json files,
  os.pathsep apart, each {"pronounce": {"harvest": "har-vist"}} (or the bare map): a sound-alike
  spelling, read with g2p_en piece by piece. A spelling that gives a sound the voice lacks stops the run.
Otherwise the dictionary's (English) or the spelling rules' reading is sung.
A saved reading carries the language it is for ("lang", "en" when it has none), and a run uses
only its own language's readings. It is the voice's own sounds ("it/k it/w it/a"), dealt over the
word's syllables by that language's vowels, or for English ARPAbet ("CH AY1 L D"). The readings
this run used are listed in <notes.json>.<lang>.used.json, so sing_song.py can say which were not.

A note marked with another "lang" (publish_song.py --lang-from, #67) is left as it is: a note
with no "ph" is sung with sing_ufr.py's Finnish rules, and each other language has its own run."""
import importlib.util, json, os, re, sys, unicodedata
_spec = importlib.util.spec_from_file_location(
    "languages", os.path.join(os.path.dirname(os.path.abspath(__file__)), "languages.py"))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)
# the voice pack the singer uses (UFR_DIR, as sing_ufr.py reads it; publish_song.py --ufr-dir)
PH = set(json.load(open(os.path.join(os.environ.get("UFR_DIR", "/var/tmp/i36-ufr"), "core", "phonemes.json"))))
lang, path = sys.argv[1], sys.argv[2]
notes = json.load(open(path))
mine = lambda n: n.get("syl") and n.get("lang", lang) == lang   # this run's syllables
VOWELS = set(L.get(lang, "vowels") or [])
USED = []   # the saved readings this run applied: {lang, bar, beat, word}

def word_key(s):
    """A word as this run's readings are keyed (languages.word_key)."""
    return L.word_key(s, lang)

def en_sound(p):
    """An ARPAbet sound as the voice names it: AH1 en/ah, an unstressed AH0 the schwa en/ax."""
    base = p.rstrip("012").lower()
    return "en/ax" if base == "ah" and p.endswith("0") else f"en/{base}"

def check(arp, where, code="en"):
    """Stop the run when the voice lacks one of the sounds `arp`, naming `where` (the word, its file).
    A reading is the voice's own sounds (every one "prefix/name") or ARPAbet (none is), never a mix,
    and ARPAbet only in English."""
    voiced = ["/" in p for p in arp]
    if any(voiced) and not all(voiced):
        sys.exit(f"{where}: mixes the voice's sounds with ARPAbet; write it all in one")
    if all(voiced):
        missing = [p for p in arp if p not in PH]
    elif code != "en":
        sys.exit(f"{where}: ARPAbet is English only; write a {code} reading in the voice's sounds (it/a ...)")
    else:
        missing = [p for p in arp if en_sound(p) not in PH]
    if missing:
        sys.exit(f"{where}: the voice has no {missing}")
    return arp

def is_voiced(sounds):
    return bool(sounds) and "/" in sounds[0]

def deal(ph, syl):
    """[(sound, is_vowel)] -> one {"on", "vs", "co"} per syllable: one vowel run per syllable (extra
    vowels join the last, missing ones repeat the last), the consonants between two vowels split
    between them. With no vowel at all (a hum) every syllable holds the first sound."""
    vpos = [i for i, (_, v) in enumerate(ph) if v]
    if not vpos:
        return [{"on": [], "vs": [p for p, _ in ph][:1], "co": []} for _ in range(syl)]
    groups = [[v] for v in vpos[:syl - 1]] + [vpos[syl - 1:]] if len(vpos) >= syl else [[v] for v in vpos] + [[vpos[-1]]] * (syl - len(vpos))
    bounds = [[grp[0], grp[-1]] for grp in groups]
    out = []
    for gi, (v0, v1) in enumerate(bounds):
        prev_end = bounds[gi - 1][1] if gi > 0 else -1
        nxt = bounds[gi + 1][0] if gi + 1 < len(bounds) else len(ph)
        gap = list(range(prev_end + 1, v0)) if v0 > prev_end else []
        take = gap[len(gap) // 2:] if gi > 0 else gap
        on = [ph[i][0] for i in take]
        vs = [ph[i][0] for i in range(v0, v1 + 1) if ph[i][1]] if v1 >= v0 else [ph[v0][0]]
        after = list(range(v1 + 1, nxt)) if nxt > v1 else []
        co = [ph[i][0] for i in (after[: len(after) // 2] if gi + 1 < len(bounds) else after)]
        out.append({"on": on, "vs": vs or [ph[v0][0]], "co": co})
    return out

def saved(place, text, at, every):
    """The saved reading for this word at this place, or else everywhere, noted as used; or None."""
    if place in at:
        USED.append({"lang": lang, "bar": place[0], "beat": place[1], "word": text})
        return at[place]
    if text in every:
        USED.append({"lang": lang, "bar": None, "beat": None, "word": text})
        return every[text]
    return None

def words_of():
    """This run's syllables grouped into words: [[note index, ...], ...]."""
    words, cur = [], []
    for k, n in enumerate(notes):
        if not mine(n): continue
        cur.append(k)
        if n.get("syllabic") in (None, "single", "end"): words.append(cur); cur = []
    if cur: words.append(cur)
    return words

def place_of(idx, text):
    """A word's place (places.py): the bar and beat its first syllable starts on (unroll.py)."""
    first = notes[idx[0]]
    return (first.get("bar"), round(float(first.get("beat", -1)), 3), text)

def arpabet(g2p, text):
    return [p for p in g2p(text) if re.match(r"^[A-Z]+[0-2]?$", p)]

def choir_words(g2p):
    """CHOIR_WORDS' sound-alike spellings, as sounds: word -> ARPAbet (readings.py spelled())."""
    out = {}
    for f in filter(None, os.environ.get("CHOIR_WORDS", "").split(os.pathsep)):
        d = json.load(open(f))
        d = d["pronounce"] if isinstance(d.get("pronounce"), dict) else d
        for w, spelling in d.items():
            arp = []
            for piece in re.split(r"[\s\-]+", str(spelling).lower()):
                piece = re.sub(r"[^a-z']", "", piece)
                if piece: arp += arpabet(g2p, piece)
            if not any(p[-1].isdigit() for p in arp):
                sys.exit(f"{f}: {w!r}: {spelling!r} gives no vowel to sing")
            out[word_key(w)] = check(arp, f"{f}: {w!r} ({spelling!r})")
    return out

def song_choices(code):
    """The song page's choices in language `code` (SONG_PRONOUNCE): ({(bar, beat, word): sounds},
    {word: sounds}). A place with no "lang", and the old flat everywhere map ({word: sounds}), are
    English: only English had readings before #484."""
    f = os.environ.get("SONG_PRONOUNCE", "")
    if not f or not os.path.isfile(f): return {}, {}
    d = json.load(open(f))
    at = {(int(c["bar"]), round(float(c["beat"]), 3), L.word_key(c["word"], code)):
          check(c["sounds"].split(), f"{f}: {c['word']!r} at bar {c['bar']}", code)
          for c in d.get("places") or [] if c.get("lang", "en") == code}
    every = d.get("everywhere") or {}
    if any(isinstance(v, str) for v in every.values()):   # the old flat map
        every = {"en": {w: v for w, v in every.items() if isinstance(v, str)}}
    every = {L.word_key(w, code): check(v.split(), f"{f}: {w!r}", code)
             for w, v in (every.get(code) or {}).items()}
    return at, every

def clean(s):
    s = unicodedata.normalize("NFC", (s or "").lower())
    return re.sub(r"[^a-zåäöüõšžßéè]", "", s)

def cut(tokens, is_vowel):
    """Tokens -> (onset, vowels, coda) around the first run of vowels."""
    i = 0
    while i < len(tokens) and not is_vowel(tokens[i]): i += 1
    j = i
    while j < len(tokens) and is_vowel(tokens[j]): j += 1
    return tokens[:i], tokens[i:j], tokens[j:]

# ---- English: dictionary per word, dealt over the word's syllables ----
def english():
    from g2p_en import G2p
    g2p = G2p()
    choir = choir_words(g2p)
    at, every = song_choices("en")
    for idx in words_of():
        # keep the apostrophe: "I'm" and "don't" are dictionary words, "im" and "dont" are not (#45)
        text = word_key("".join(notes[k]["syl"] for k in idx))
        if not text: continue
        place = place_of(idx, text)
        if not re.search(r"[aeiouy]", text): continue   # scat ("dn", "mm"): no dictionary word; sing_ufr.py hums it
        arp = saved(place, text, at, every)
        if arp and is_voiced(arp):   # a reading in the voice's sounds ("en/ch en/aa en/iy ..."), #484
            for k, o in zip(idx, deal([(p, p in VOWELS) for p in arp], len(idx))): notes[k]["ph"] = o
            continue
        arp = arp or choir.get(text) or arpabet(g2p, text)
        ph = []
        for p in arp:
            ph.append((en_sound(p), p[-1].isdigit()))
        vpos = [i for i, (_, v) in enumerate(ph) if v]
        syl = len(idx)
        if not vpos:   # a hum ("mm"): voiced consonants as the "vowel"
            for k in idx: notes[k]["ph"] = {"on": [], "vs": [p for p, _ in ph][:1] or ["en/m"], "co": []}
            continue
        # one vowel per syllable: extra vowels join the last syllable, missing ones repeat the last vowel
        groups = [[v] for v in vpos[:syl - 1]] + [vpos[syl - 1:]] if len(vpos) >= syl else [[v] for v in vpos] + [[vpos[-1]]] * (syl - len(vpos))
        bounds = []
        for gi, grp in enumerate(groups):
            start = 0 if gi == 0 else None
            bounds.append([grp[0], grp[-1]])
        out = []
        for gi, (v0, v1) in enumerate(bounds):
            prev_end = bounds[gi - 1][1] if gi > 0 else -1
            nxt = bounds[gi + 1][0] if gi + 1 < len(bounds) else len(ph)
            # consonants between the previous vowel and this one: the later half is this onset
            gap = list(range(prev_end + 1, v0)) if v0 > prev_end else []
            take = gap[len(gap) // 2:] if gi > 0 else gap
            on = [ph[i][0] for i in take]
            vs = [ph[i][0] for i in range(v0, v1 + 1) if ph[i][1]] if v1 >= v0 else [ph[v0][0]]
            after = list(range(v1 + 1, nxt)) if nxt > v1 else []
            co = [ph[i][0] for i in (after[: len(after) // 2] if gi + 1 < len(bounds) else after)]
            out.append({"on": on, "vs": vs or [ph[v0][0]], "co": co})
        for k, o in zip(idx, out): notes[k]["ph"] = o

# ---- spelling rules: longest match first, per syllable ----
def variant(table):
    """The base's table with this run's variant rules laid over it (languages.py "rules"): "X" reads
    X as those sounds; "X+front" does so only before a front vowel, and the base's reading otherwise."""
    for code in reversed(L.chain(lang)[:-1]):   # the variants between the base and this code, base-most first
        for key, sounds in (L.LANGUAGES[code].get("rules") or {}).items():
            if key.endswith("+front"):
                x = key[:-len("+front")]
                def pick(s, i, first, last, x=x, own=sounds, base=table.get(x, [])):
                    if front(s, i, len(x)): return list(own)
                    return base(s, i, first, last) if callable(base) else base
                table[x] = pick
            else:
                table[key] = list(sounds)
    return table

def rules(table, vowels):
    table = variant(dict(table))
    whole = {}
    for code in reversed(L.chain(lang)):
        whole.update(L.LANGUAGES[code].get("words") or {})
    at, every = song_choices(lang)
    keys = sorted(table, key=len, reverse=True)
    def read(s, first, last):
        out, i = [], 0
        while i < len(s):
            for key in keys:
                if s.startswith(key, i):
                    v = table[key]
                    v = v(s, i, first, last) if callable(v) else v
                    out += v; i += len(key); break
            else:
                i += 1
        return out
    for idx in words_of():
        text = word_key("".join(notes[k]["syl"] for k in idx))
        reading = saved(place_of(idx, text), text, at, every) if text else None
        if reading:   # a saved reading, dealt over the word's syllables (#484)
            for k, o in zip(idx, deal([(p, p in vowels) for p in reading], len(idx))): notes[k]["ph"] = o
            continue
        for k in idx:
            n = notes[k]
            s = clean(n.get("syl"))
            if not s: continue
            first = n.get("syllabic") in (None, "single", "begin")
            last = n.get("syllabic") in (None, "single", "end")
            toks = list(whole[s]) if s in whole else read(s, first, last)
            on, vs, co = cut(toks, lambda p: p in vowels)
            if not vs: on, vs, co = [], toks[-1:] or ["it/m"], []
            n["ph"] = {"on": on, "vs": vs, "co": co}

def front(s, i, n=1):
    return s[i + n: i + n + 1] in tuple("eiyäöé") or s[i + n: i + n + 2] in ("ae", "oe")

def german():
    D = lambda x: [f"de/{x}"]
    def e(s, i, first, last):
        if not first and (s.endswith("e", i) and i == len(s) - 1 or s[i:] in ("en", "el", "em", "es")): return D("ex")
        return D("eh") if re.match(r"[^aeiouäöü]{2}", s[i + 1:i + 3]) or (i == len(s) - 2 and s[-1] not in "aeiouh") else D("e")
    def ch(s, i, first, last): return D("xh") if i > 0 and s[i - 1] in "aou" else D("ch")
    def s_(s, i, first, last): return D("z") if i < len(s) - 1 and s[i + 1] in "aeiouäöü" and (i == 0) else D("s")
    def sp(s, i, first, last): return (D("sh") + D("p")) if i == 0 else (D("s") + D("p"))
    def st(s, i, first, last): return (D("sh") + D("t")) if i == 0 else (D("s") + D("t"))
    def er(s, i, first, last): return D("er") if i == len(s) - 2 and not first else D("eh") + D("r")
    t = {"sch": D("sh"), "tsch": D("tsh"), "ch": ch, "chs": D("k") + D("s"), "ei": D("ei"), "ai": D("ei"), "ey": D("ei"),
         "eu": D("eu"), "äu": D("eu"), "au": D("au"), "ie": D("i"), "ä": D("eh"), "ö": D("oe"), "ü": D("ue"), "y": D("ue"),
         "a": D("a"), "e": e, "er": er, "i": D("ih"), "o": D("o"), "u": D("u"), "aa": D("a"), "ee": D("e"), "oo": D("o"),
         "ah": D("a"), "eh": D("e"), "ih": D("i"), "oh": D("o"), "uh": D("u"), "äh": D("eh"), "öh": D("oe"), "üh": D("ue"),
         "z": D("ts"), "tz": D("ts"), "sp": sp, "st": st, "s": s_, "ss": D("s"), "ß": D("s"), "v": D("f"), "w": D("v"),
         "qu": D("k") + D("v"), "ng": D("ng"), "nk": D("ng") + D("k"), "pf": D("pf"), "ph": D("f"), "th": D("t"),
         "j": D("j"), "r": D("r"), "c": D("k"), "ck": D("k"), "x": D("k") + D("s"), "h": D("h"),
         **{c: D(c) for c in "bdfgklmnpt"}}
    rules(t, VOWELS)

def swedish():
    D = lambda x: [f"de/{x}"]
    def k(s, i, first, last): return D("ch") if front(s, i) else D("k")
    def g(s, i, first, last): return D("j") if i == 0 and front(s, i) else D("g")
    def sk(s, i, first, last): return D("sh") if front(s, i, 2) else D("s") + D("k")
    t = {"sj": D("sh"), "skj": D("sh"), "stj": D("sh"), "sch": D("sh"), "sk": sk, "tj": D("ch"), "kj": D("ch"), "k": k, "g": g,
         "dj": D("j"), "gj": D("j"), "lj": D("j"), "hj": D("j"), "ng": D("ng"), "j": D("j"), "w": D("v"), "c": D("k"), "x": D("k") + D("s"),
         "z": D("s"), "q": D("k"), "a": D("a"), "e": D("e"), "i": D("i"), "o": D("o"), "u": ["fr/uh"], "y": D("ue"), "å": D("o"),
         "ä": D("eh"), "ö": D("oe"), "é": D("e"), **{c: D(c) for c in "bdfhlmnprstv"}}
    rules(t, VOWELS)

def latin():
    I = lambda x: [f"it/{x}"]
    def c(s, i, first, last): return I("ch") if front(s, i) else I("k")
    def g(s, i, first, last): return ["de/dsh"] if front(s, i) else I("g")
    def sc(s, i, first, last): return I("sh") if front(s, i, 2) else I("s") + I("k")
    def ti(s, i, first, last): return I("ts") + I("i") if i + 2 < len(s) and s[i + 2] in "aeiou" else I("t") + I("i")
    t = {"ae": I("e"), "oe": I("e"), "au": I("a") + I("u"), "qu": I("k") + I("w"), "gu": I("g") + I("w"), "gn": I("gn"),
         "ch": I("k"), "ph": I("f"), "th": I("t"), "c": c, "g": g, "sc": sc, "ti": ti, "j": I("y"), "y": I("i"), "h": [],
         "x": I("k") + I("s"), "z": I("dz"), "a": I("a"), "e": I("e"), "i": I("i"), "o": I("o"), "u": I("u"),
         **{x: I("rr" if x == "r" else x) for x in "bdflmnprstv"}, "k": I("k")}
    rules(t, VOWELS)

def estonian():
    I = lambda x: [f"it/{x}"]
    t = {"a": I("a"), "e": I("e"), "i": I("i"), "o": I("o"), "u": I("u"), "õ": ["de/ex"], "ä": ["en/eh"], "ö": ["fr/ee"], "ü": ["fr/uh"],
         "š": ["de/sh"], "ž": ["de/zh"], "h": ["de/h"], "j": I("y"), "r": I("rr"), "ng": ["de/ng"], "w": I("v"), "z": I("s"), "c": I("k"),
         "q": I("k"), "x": I("k") + I("s"), "y": ["fr/uh"], **{x: I(x) for x in "bdfgklmnpstv"}}
    rules(t, VOWELS)

# a variant ("la-de") runs its base's reader, whose table rules() lays the variant's rules over
{"en": english, "de": german, "sv": swedish, "la": latin, "et": estonian}[L.root(lang)]()
# English two-part vowels are sung as their two vowels (#484): the voice's own en/ay, en/aw and
# en/oy came out as a vowel that does not move ("child" sang "chaald"), and a user wrote "child"
# by hand as AA then IY. Done last, so a saved reading is split the same way.
SPLIT = {"en/ay": ["en/aa", "en/iy"], "en/aw": ["en/aa", "en/uw"], "en/oy": ["en/ao", "en/iy"]}
if lang == "en":
    for n in notes:
        if mine(n) and n.get("ph"):
            n["ph"]["vs"] = [p for v in n["ph"]["vs"] for p in SPLIT.get(v, [v])]
bad = sorted({p for n in notes if n.get("ph") for part in n["ph"].values() for p in part if p not in PH})
if bad: sys.exit(f"sounds the voice does not have: {bad}")
json.dump(notes, open(path, "w"), ensure_ascii=False, indent=0)
json.dump(USED, open(f"{path}.{lang}.used.json", "w"), ensure_ascii=False)
print(lang, sum(1 for n in notes if mine(n) and n.get("ph")), "syllables")
