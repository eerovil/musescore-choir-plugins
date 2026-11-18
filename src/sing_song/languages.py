"""Every language a song can be sung in, and what the singer needs to know about each (#484).

One record per language code:

    "name"          what people read ("Latin (German style)")
    "g2p"           True when g2p.py gives the words their sounds; Finnish and Norwegian are sung
                    with the voices' own rules instead
    "vowels"        the exact set of vowel sounds g2p.py cuts a syllable around and deals a saved
                    reading by (a sound in it starts a syllable's vowel; any other is a consonant)
    "prefixes"      the sound prefixes a saved reading in this language may use; the app checks a
                    reading's shape by them when it has no voice pack to check it against
    "finnish_look"  letters or pairs this language's words seldom have and Finnish words often do;
                    main.unmarked_finnish() refuses a word that has them unless --lang-from names it
    "base"          a variant's language: every field the variant does not give is its base's
    "rules"         a variant's spelling rules, laid over its base's table in g2p.py: a key reads
                    those letters as those sounds; a key ending "+front" only before e, i, y, ä, ö,
                    é, ae or oe, and the base's reading otherwise
    "words"         a variant's whole-syllable readings, before any rule

Read the fields through get(), which follows "base". This file is stdlib only: g2p.py runs in the
singer's own venv with ``python -I`` and loads it by path, and the app imports it.
"""
import re
import unicodedata

LANGUAGES = {
    "fi": {"name": "Finnish", "g2p": False, "vowels": [], "prefixes": []},
    "en": {"name": "English", "g2p": True,
           "vowels": [f"en/{v}" for v in ("aa", "ae", "ah", "ao", "aw", "ax", "ay", "eh", "er", "ey",
                                          "ih", "iy", "ow", "oy", "uh", "uw")],
           "prefixes": ["en/"], "finnish_look": r"[äöå]|yy|kk"},
    "de": {"name": "German", "g2p": True,
           "vowels": [f"de/{v}" for v in ("a", "e", "eh", "ex", "er", "i", "ih", "o", "oh", "u", "uh",
                                          "ei", "eu", "au", "oe", "oeh", "ue", "ueh")],
           "prefixes": ["de/"], "finnish_look": r"y|aa|ii|uu|kk|å"},
    "sv": {"name": "Swedish", "g2p": True,
           "vowels": ["de/a", "de/e", "de/i", "de/o", "fr/uh", "de/ue", "de/eh", "de/oe"],
           "prefixes": ["de/", "fr/"], "finnish_look": r"aa|ee|ii|uu|yy|ää|öö|kk"},
    "la": {"name": "Latin", "g2p": True,
           "vowels": ["it/a", "it/e", "it/i", "it/o", "it/u"],
           "prefixes": ["it/", "de/"], "finnish_look": r"[äöå]|aa|ee|oo|yy|kk"},
    # Latin as German and Austrian choirs sing it: "Hodie" with its h, "excelsis" and "caeli"
    # with ts, "regina" with a hard g, "qui" as kvi, "agnus" as ag-nus
    "la-de": {"name": "Latin (German style)", "base": "la",
              "rules": {"h": ["de/h"], "c+front": ["de/ts"], "sc+front": ["it/s", "de/ts"],
                        "g+front": ["it/g"], "qu": ["de/k", "de/v"], "gn": ["it/g", "it/n"],
                        "z": ["de/ts"]},
              "words": {}},
    "et": {"name": "Estonian", "g2p": True,
           "vowels": ["it/a", "it/e", "it/i", "it/o", "it/u", "de/ex", "en/eh", "fr/ee", "fr/uh"],
           "prefixes": ["it/", "de/", "en/", "fr/"]},
    "no": {"name": "Norwegian", "g2p": False, "vowels": [], "prefixes": []},
}


def chain(code):
    """[code, its base, that one's base, ...]"""
    out = []
    while code in LANGUAGES and code not in out:
        out.append(code)
        code = LANGUAGES[code].get("base")
    return out


def get(code, field, default=None):
    """The language's own value for field, or else its base's, following the chain."""
    for c in chain(code):
        if field in LANGUAGES[c]:
            return LANGUAGES[c][field]
    return default


def root(code):
    """The language a variant is built on (the code itself when it has no base)."""
    return chain(code)[-1] if code in LANGUAGES else code


def names():
    """{code: name}, Finnish first."""
    return {c: d["name"] for c, d in LANGUAGES.items()}


def sung_by_g2p():
    """The codes g2p.py gives sounds, a variant counting as its base does."""
    return {c for c in LANGUAGES if get(c, "g2p")}


def word_key(s, lang):
    """A word as the readings are keyed: English as its dictionary sees it (lower case, a-z and the
    apostrophe, #45), every other language with all its letters (ä, ü and õ included)."""
    s = unicodedata.normalize("NFC", (s or "").lower().replace("’", "'"))
    if lang == "en":
        return re.sub(r"[^a-z']", "", s).strip("'")
    return "".join(c for c in s if c.isalpha() or c == "'").strip("'")
