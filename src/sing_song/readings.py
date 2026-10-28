"""English words the dictionary reads more than one way, and how each reading sounds.

    python -I readings.py < words.json      (in the singer's venv: g2p_en and its CMU dictionary)

Reads a JSON list of words (word_key()'d, as places.py gives them) and prints a JSON object:
for each word with two or more readings that sound different, its readings, the one sung when
none is chosen first:

    {"wind": [{"sounds": "W IH1 N D", "say": "wind", "as_in": "sit"},
              {"sounds": "W AY1 N D", "say": "wynd", "as_in": "find"}]}

Readings that differ only in stress or in an unstressed vowel ("the": DH AH0, DH AH1, DH IY0)
sound alike when sung, so such a word is left out; so are a quick word's weak forms ("for" F ER0)
and the dictionary's accent variants ("where" with or without HH, "your" AO or UH). song-app's song page lists the words this
gives (eeron-omat-taskit#144), and g2p.py turns a choir's sound-alike spellings into sounds
with sound_alike()."""
import json
import re
import sys

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
# a plain respelling of each sound, and a common word with the vowel, so a person can tell
# readings apart without the sound codes
SAY = {"AA": "ah", "AE": "a", "AH": "u", "AO": "aw", "AW": "ow", "AY": "y", "EH": "e", "ER": "ur",
       "EY": "ay", "IH": "i", "IY": "ee", "OW": "oh", "OY": "oy", "UH": "oo", "UW": "oo",
       "B": "b", "CH": "ch", "D": "d", "DH": "th", "F": "f", "G": "g", "HH": "h", "JH": "j", "K": "k",
       "L": "l", "M": "m", "N": "n", "NG": "ng", "P": "p", "R": "r", "S": "s", "SH": "sh", "T": "t",
       "TH": "th", "V": "v", "W": "w", "Y": "y", "Z": "z", "ZH": "zh"}
AS_IN = {"AA": "father", "AE": "cat", "AH": "cup", "AO": "law", "AW": "cow", "AY": "find", "EH": "bed",
         "ER": "bird", "EY": "day", "IH": "sit", "IY": "see", "OW": "go", "OY": "boy", "UH": "book",
         "UW": "food"}


# a vowel before R sounds like these ("tear": T EH1 R the rip, T IH1 R the drop)
BEFORE_R = {"EH": ("ai", "hair"), "IH": ("ee", "near"), "IY": ("ee", "near"), "UH": ("oo", "poor")}


def base(p):
    return p.rstrip("012")


def stressed(p):
    return p[-1:] in ("1", "2")


# stressed vowels the dictionary gives as one word's regional variants ("your" Y AO1 R / Y UH1 R,
# "rather" R AE1 DH ER0 / R AH1 DH ER0, "again" ... EH1 N / ... EY1 N): sung either way
ACCENT = [{"AA", "AO", "AH"}, {"AE", "AA"}, {"AE", "AH"}, {"AO", "UH"}, {"UH", "UW"}, {"EH", "EY"}]


def plain(pron):
    """A reading without the sounds that only mark an accent: HH before W ("where"), Y between a
    consonant and UW ("new"), T after N or F ("don't", "often"), a last unstressed -ing's NG."""
    out = []
    for i, p in enumerate(pron):
        nxt = pron[i + 1] if i + 1 < len(pron) else ""
        prev = pron[i - 1] if i else ""
        if p == "HH" and nxt == "W":
            continue
        if p == "Y" and prev and base(prev) not in VOWELS and base(nxt) == "UW":
            continue
        if p == "T" and prev in ("N", "F"):
            continue
        if p == "NG" and not nxt and prev.endswith("0"):   # "goin'"
            p = "N"
        out.append(p)
    return out


def sound_alike(a, b):
    """True when two readings sound alike when sung: they differ only in stress, in an unstressed
    vowel, or in an accent's sounds (plain(), ACCENT)."""
    a, b = plain(a), plain(b)
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if base(x) in VOWELS and base(y) in VOWELS:
            if (base(x) != base(y) and stressed(x) and stressed(y)
                    and not any({base(x), base(y)} <= pair for pair in ACCENT)):
                return False
        elif x != y:
            return False
    return True


def distinct(prons):
    """One reading of each group that sounds alike, in the dictionary's order. A weak form, with no
    stressed vowel ("for" F ER0, "are" ER0), is the same word said quickly: left out."""
    out = []
    strong = [p for p in prons if any(stressed(x) for x in p)] or prons
    for p in strong:
        if not any(sound_alike(p, q) for q in out):
            out.append(p)
    return out


def respell(pron):
    """W AY1 N D -> "wynd": a reading in plain letters, the stressed vowel's sound shown."""
    s = []
    for i, p in enumerate(pron):
        b = base(p)
        say = SAY.get(b, b.lower())
        if b == "AY" and i == len(pron) - 1:
            say = "eye" if not s else "y"
        if b in BEFORE_R and i + 1 < len(pron) and pron[i + 1] == "R":
            say = BEFORE_R[b][0]
        s.append(say)
    return "".join(s)


def describe(pron):
    at = next((i for i, p in enumerate(pron) if stressed(p)), next((i for i, p in enumerate(pron) if base(p) in VOWELS), None))
    as_in = ""
    if at is not None:
        b = base(pron[at])
        r = at + 1 < len(pron) and pron[at + 1] == "R" and b in BEFORE_R
        as_in = BEFORE_R[b][1] if r else AS_IN.get(b, "")
    return {"sounds": " ".join(pron), "say": respell(pron), "as_in": as_in}


def readings(words, cmu, sung):
    """{word: [describe(reading)...]} for the words with two readings or more; the reading
    `sung(word)` gives (what is sung when none is chosen) first."""
    out = {}
    for w in words:
        prons = distinct(cmu.get(w, []))
        if len(prons) < 2:
            continue
        default = sung(w)
        first = [p for p in prons if sound_alike(p, default)][:1]
        out[w] = [describe(p) for p in first + [p for p in prons if p not in first]]
    return out


def g2p_sounds(g2p, text):
    """What g2p_en gives for `text`, sounds only."""
    return [p for p in g2p(text) if re.match(r"^[A-Z]+[0-2]?$", p)]


def spelled(g2p, spelling):
    """A sound-alike spelling ("har-vist") turned into sounds, piece by piece."""
    out = []
    for piece in re.split(r"[\s\-]+", spelling.lower()):
        piece = re.sub(r"[^a-z']", "", piece)
        if piece:
            out += g2p_sounds(g2p, piece)
    return out


if __name__ == "__main__":
    from g2p_en import G2p
    g2p = G2p()
    words = json.load(sys.stdin)
    json.dump(readings(words, g2p.cmu, lambda w: g2p_sounds(g2p, w)), sys.stdout)
