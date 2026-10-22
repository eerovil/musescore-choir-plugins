"""Spoken lines for the singer: a sentence printed under one held chord, sung one syllable a note.

A score can put a whole spoken sentence under one note (Laulu reiän tarpeesta, #61), its words
joined with `‿` so the lyric import keeps it on that note. The renderer sings one syllable per
note, so for singing the note is cut into one equal note per syllable, on the same pitch. The
score itself is not changed. Numbers are said as words ("118" is "satakahdeksantoista").
"""
import re

SPOKEN_JOIN = "‿"   # ‿, how such a sentence's words are joined in the lyrics
VOWELS = "aeiouyäöå"
# Finnish diphthongs. Strictly ie, uo and yö are diphthongs only in a word's first syllable,
# but sung text is full of compounds ("silmä-suojainten"), so they are kept everywhere.
DIPHTHONGS = {"ai", "ei", "oi", "ui", "yi", "äi", "öi", "au", "eu", "iu", "ou", "ey", "iy", "äy",
              "öy", "ie", "uo", "yö"}

ONES = ["", "yksi", "kaksi", "kolme", "neljä", "viisi", "kuusi", "seitsemän", "kahdeksan",
        "yhdeksän"]


def number_words(n: int) -> str:
    """0 to 9999 as one Finnish word: 118 -> "satakahdeksantoista"."""
    if not 0 <= n <= 9999:
        raise ValueError(f"no words for {n}")
    if n == 0:
        return "nolla"
    out = ""
    thousands, n = divmod(n, 1000)
    if thousands:
        out += "tuhat" if thousands == 1 else ONES[thousands] + "tuhatta"
    hundreds, n = divmod(n, 100)
    if hundreds:
        out += "sata" if hundreds == 1 else ONES[hundreds] + "sataa"
    if n == 10:
        out += "kymmenen"
    elif 10 < n < 20:
        out += ONES[n - 10] + "toista"
    else:
        tens, ones = divmod(n, 10)
        if tens:
            out += ONES[tens] + "kymmentä"
        out += ONES[ones]
    return out


def _vowel(ch: str) -> bool:
    return ch.lower() in VOWELS


def syllables(word: str) -> list:
    """Finnish syllables of one word: "silmäsuojainten" -> ["sil", "mä", "suo", "jain", "ten"].

    A syllable starts at a consonant followed by a vowel; two vowels split unless they are the
    same vowel or a diphthong. Punctuation stays on the syllable it follows.
    """
    letters = [i for i, ch in enumerate(word) if ch.isalpha()]
    if not letters:
        return [word] if word else []
    cuts = []
    start = letters[0]
    for k in range(1, len(letters)):
        i, prev = letters[k], letters[k - 1]
        nxt = letters[k + 1] if k + 1 < len(letters) else None
        seen_vowel = any(_vowel(word[j]) for j in range(start, i))
        if not _vowel(word[i]) and nxt is not None and _vowel(word[nxt]) and seen_vowel:
            cuts.append(i)
            start = i
        elif _vowel(word[i]) and _vowel(word[prev]):
            pair = (word[prev] + word[i]).lower()
            run = 1
            while prev - run >= start and _vowel(word[prev - run]):
                run += 1
            same = pair[0] == pair[1]
            diph = pair in DIPHTHONGS
            if run > 1 or not (same or diph):
                cuts.append(i)
                start = i
    edges = [0] + cuts + [len(word)]
    return [word[a:b] for a, b in zip(edges, edges[1:])]


def spoken_syllables(text: str) -> list:
    """A `‿`-joined spoken sentence as the syllables to sing, numbers as words."""
    out = []
    for word in re.split(rf"[{SPOKEN_JOIN}\s]+", text):
        if not word:
            continue
        word = re.sub(r"\d+", lambda m: number_words(int(m.group())), word)
        out += syllables(word)
    return out


def split_spoken(notes: list) -> list:
    """unroll.py's notes with each spoken sentence cut into one equal note per syllable."""
    out = []
    for n in notes:
        syl = n.get("syl") or ""
        if SPOKEN_JOIN not in syl:
            out.append(n)
            continue
        syls = spoken_syllables(syl)
        k = len(syls)
        for s in syls:
            out.append({**n, "syl": s, "beats": n["beats"] / k, "sec": n["sec"] / k,
                        "syllabic": "single", "melisma": False})
    return out
