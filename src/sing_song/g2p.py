"""Give each sung note its sounds in REMY/LYSE's phoneme set, for songs not in Finnish.

    python g2p.py <lang> <notes.json>      (en, de, sv, la, et; rewrites the file)

Each note with a syllable gets "ph": {"on": [...], "vs": [...], "co": [...]} (onset consonants,
vowels, coda consonants), which sing_ufr.py uses instead of its Finnish spelling rules.
English goes through the CMU pronouncing dictionary (g2p_en) word by word, and each word's
sounds are dealt over its syllables: one vowel sound per syllable, the consonants between two
vowels split between them. The others read the spelling of each syllable with that language's
rules (German and Swedish with the voice's German sounds, church Latin with its Italian ones,
Estonian like Finnish plus õ, ü, š, ž).

A note marked with another "lang" (publish_song.py --lang-from, #67) is left as it is: a note
with no "ph" is sung with sing_ufr.py's Finnish rules, and each other language has its own run."""
import json, os, re, sys, unicodedata
# the voice pack the singer uses (UFR_DIR, as sing_ufr.py reads it; publish_song.py --ufr-dir)
PH = set(json.load(open(os.path.join(os.environ.get("UFR_DIR", "/var/tmp/i36-ufr"), "core", "phonemes.json"))))
lang, path = sys.argv[1], sys.argv[2]
notes = json.load(open(path))
mine = lambda n: n.get("syl") and n.get("lang", lang) == lang   # this run's syllables

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
    words, cur = [], []
    for k, n in enumerate(notes):
        if not mine(n): continue
        cur.append(k)
        if n.get("syllabic") in (None, "single", "end"): words.append(cur); cur = []
    if cur: words.append(cur)
    for idx in words:
        # keep the apostrophe: "I'm" and "don't" are dictionary words, "im" and "dont" are not (#45)
        text = "".join(re.sub(r"[^a-z']", "", unicodedata.normalize("NFC", notes[k]["syl"].lower().replace("\u2019", "'"))) for k in idx).strip("'")
        if not text: continue
        if not re.search(r"[aeiouy]", text): continue   # scat ("dn", "mm"): no dictionary word; sing_ufr.py hums it
        arp = [p for p in g2p(text) if re.match(r"^[A-Z]+[0-2]?$", p)]
        ph = []
        for p in arp:
            base = p.rstrip("012").lower()
            if base == "ah" and p.endswith("0"): base = "ax"
            ph.append((f"en/{base}", p[-1].isdigit()))
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
def rules(table, vowels, word_rules=None):
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
    for k, n in enumerate(notes):
        s = clean(n.get("syl")) if mine(n) else ""
        if not s: continue
        first = n.get("syllabic") in (None, "single", "begin")
        last = n.get("syllabic") in (None, "single", "end")
        toks = read(s, first, last)
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
    rules(t, {p for p in PH if p.startswith("de/") and p[3:] in ("a", "e", "eh", "ex", "er", "i", "ih", "o", "oh", "u", "uh", "ei", "eu", "au", "oe", "oeh", "ue", "ueh")})

def swedish():
    D = lambda x: [f"de/{x}"]
    def k(s, i, first, last): return D("ch") if front(s, i) else D("k")
    def g(s, i, first, last): return D("j") if i == 0 and front(s, i) else D("g")
    def sk(s, i, first, last): return D("sh") if front(s, i, 2) else D("s") + D("k")
    t = {"sj": D("sh"), "skj": D("sh"), "stj": D("sh"), "sch": D("sh"), "sk": sk, "tj": D("ch"), "kj": D("ch"), "k": k, "g": g,
         "dj": D("j"), "gj": D("j"), "lj": D("j"), "hj": D("j"), "ng": D("ng"), "j": D("j"), "w": D("v"), "c": D("k"), "x": D("k") + D("s"),
         "z": D("s"), "q": D("k"), "a": D("a"), "e": D("e"), "i": D("i"), "o": D("o"), "u": ["fr/uh"], "y": D("ue"), "å": D("o"),
         "ä": D("eh"), "ö": D("oe"), "é": D("e"), **{c: D(c) for c in "bdfhlmnprstv"}}
    rules(t, {"de/a", "de/e", "de/i", "de/o", "fr/uh", "de/ue", "de/eh", "de/oe"})

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
    rules(t, {"it/a", "it/e", "it/i", "it/o", "it/u"})

def estonian():
    I = lambda x: [f"it/{x}"]
    t = {"a": I("a"), "e": I("e"), "i": I("i"), "o": I("o"), "u": I("u"), "õ": ["de/ex"], "ä": ["en/eh"], "ö": ["fr/ee"], "ü": ["fr/uh"],
         "š": ["de/sh"], "ž": ["de/zh"], "h": ["de/h"], "j": I("y"), "r": I("rr"), "ng": ["de/ng"], "w": I("v"), "z": I("s"), "c": I("k"),
         "q": I("k"), "x": I("k") + I("s"), "y": ["fr/uh"], **{x: I(x) for x in "bdfgklmnpstv"}}
    rules(t, {"it/a", "it/e", "it/i", "it/o", "it/u", "de/ex", "en/eh", "fr/ee", "fr/uh"})

{"en": english, "de": german, "sv": swedish, "la": latin, "et": estonian}[lang]()
bad = sorted({p for n in notes if n.get("ph") for part in n["ph"].values() for p in part if p not in PH})
if bad: sys.exit(f"sounds the voice does not have: {bad}")
json.dump(notes, open(path, "w"), ensure_ascii=False, indent=0)
print(lang, sum(1 for n in notes if mine(n) and n.get("ph")), "syllables")
