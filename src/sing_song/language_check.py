"""A song's language: where it is saved, the languages it can be, and whether words look Finnish (#484).

The language lives in its own file, ``songs/<slug>/language.json``::

    {"language": "en", "set_at": 1795000000.0, "by": "app" | "sing_song.py"}

and not in ``.song.json``: the app's jobs load ``.song.json``, work for minutes, and save the
whole of it back, so a language set meanwhile (by ``sing_song.py --change-lang``) would be undone.
Only the setters write this file, each write whole by rename, so the last one stands.

``looks_foreign`` is the reverse of ``main.unmarked_finnish``: Finnish words seldom have
b, c, f, q, w, x, z, th, sh, ch, or a g that does not follow an n. A song with no saved language
is sung in Finnish only when its words pass it.

Imported by the app (state, server, resing) and by ``sing_song.py``; the score reader needs lxml,
which both have.
"""
import json
import os
import re
import tempfile
import time

FILE = "language.json"

# The languages a song can be sung in today: Finnish, the ones g2p.py has rules for, and
# Norwegian, which the voices sing from their own rules.
LANGUAGES = {
    "fi": "Finnish",
    "en": "English",
    "de": "German",
    "sv": "Swedish",
    "la": "Latin",
    "et": "Estonian",
    "no": "Norwegian",
}

# A song with no saved language is refused when at least this share of its distinct words, and
# at least MIN_WORDS of them, look foreign. Measured on the songs on this host (see the PR of #484).
SHARE = 0.15
MIN_WORDS = 3

_FOREIGN = re.compile(r"[bcfqwxz]|th|sh|ch|(?<!n)g")
_LETTERS = re.compile(r"[^a-zåäöüõéèàáíóúßæø]+")


def describe(code):
    """'English (en)', or the code alone when it is not one of LANGUAGES."""
    return f"{LANGUAGES[code]} ({code})" if code in LANGUAGES else str(code)


def path(song_dir):
    return os.path.join(song_dir, FILE)


def saved(song_dir):
    """The song's saved language code, or None (no file, or one that cannot be read)."""
    try:
        with open(path(song_dir), encoding="utf-8") as f:
            code = json.load(f).get("language")
    except (OSError, ValueError, AttributeError):
        return None
    return code if code in LANGUAGES else None


def save(song_dir, code, by):
    """Write the song's language (whole, by rename); returns the record written."""
    if code not in LANGUAGES:
        raise ValueError(f"unknown language {code!r}: one of {', '.join(LANGUAGES)}")
    record = {"language": code, "set_at": time.time(), "by": by}
    fd, tmp = tempfile.mkstemp(prefix=FILE + ".", suffix=".tmp", dir=song_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, path(song_dir))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return record


def plain(word):
    """The word in lower case with everything but its letters taken out."""
    return _LETTERS.sub("", word.lower())


def foreign_words(words):
    """The distinct words (plain, sorted) that look foreign, and how many distinct words there are."""
    distinct = {plain(w) for w in words} - {""}
    return sorted(w for w in distinct if _FOREIGN.search(w)), len(distinct)


def looks_foreign(words):
    """True when the words do not look Finnish: at least SHARE of the distinct words, and at least
    MIN_WORDS of them, have letters Finnish words seldom have."""
    foreign, total = foreign_words(words)
    return len(foreign) >= MIN_WORDS and len(foreign) >= SHARE * total


def score_words(mscx_path):
    """Every sung word of a MuseScore 3 score: the ``Lyrics/text`` of each staff, verse and voice,
    syllables joined by their ``syllabic``. Staff texts, tempo marks, titles and the red ``⚠``
    marks are not lyrics and are not read."""
    from lxml import etree  # noqa: PLC0415 - only this reader needs it
    root = etree.parse(mscx_path).getroot()
    words = []
    for staff in root.iter("Staff"):
        open_words = {}   # (voice, verse) -> syllables so far
        for measure in staff.iter("Measure"):
            for v, voice in enumerate(measure.findall("voice")):
                for lyrics in voice.iter("Lyrics"):
                    text_el = lyrics.find("text")
                    if text_el is None:
                        continue
                    key = (v, lyrics.findtext("no") or "0")
                    syllable = "".join(text_el.itertext())
                    kind = lyrics.findtext("syllabic") or "single"
                    if kind in ("begin", "single"):
                        if open_words.get(key):
                            words.append("".join(open_words[key]))
                        open_words[key] = []
                    open_words.setdefault(key, []).append(syllable)
                    if kind in ("end", "single"):
                        words.append("".join(open_words.pop(key)))
        words.extend("".join(s) for s in open_words.values() if s)
    return [w for w in words if plain(w)]
