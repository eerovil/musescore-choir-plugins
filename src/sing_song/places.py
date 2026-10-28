"""The places a song's parts sing each word, and the readings chosen for them (eeron-omat-taskit#144).

A place is (bar, beat, word): the bar a word's first syllable is in (1-based, as unroll.py's
"bar"), where in the bar it starts (quarter notes as written, unroll.py's "beat"), and the word
as word_key() gives it. Parts singing the word together share the place, and a reading chosen
there is sung by all of them, on every pass through the bar; a part entering later on the same
word in the bar has a place of its own.

The choices are kept with the song in songs/<slug>/pronounce.json:

    {"places": [{"bar": 3, "beat": 1.0, "word": "wind", "sounds": "W IH1 N D"}],
     "everywhere": {"read": "R EH1 D"}}

g2p.py reads that file when it sings (a place first, then "everywhere"), song-app's song page
lists and saves it, and stale() names the choices whose place a score edit took away.
"""
import json
import os
import re
import unicodedata

from lxml import etree

FILE = "pronounce.json"


def word_key(s):
    """A word as the English lookup sees it: lower case, letters and the apostrophe. g2p.py has
    the same function; tests/test_places.py checks that they agree."""
    return re.sub(r"[^a-z']", "", unicodedata.normalize("NFC", s.lower().replace("’", "'"))).strip("'")


def _part_names(root):
    """{staff id: part name}"""
    out = {}
    for part in root.iter("Part"):
        name = (part.findtext("trackName") or part.findtext("Instrument/longName") or "").strip()
        for st in part.findall("Staff"):
            out[st.get("id")] = name
    return out


DUR = {"long": 16, "breve": 8, "whole": 4, "half": 2, "quarter": 1, "eighth": 0.5, "16th": 0.25,
       "32nd": 0.125, "64th": 0.0625, "128th": 0.03125}
GRACE = ("acciaccatura", "appoggiatura", "grace4", "grace16", "grace32",
         "grace8after", "grace16after", "grace32after")


def _syllables(voice):
    """[(beat, Lyrics)] of a measure's voice: where each sung chord starts in the bar, in quarter
    notes as written, counted as unroll.py counts it (tuplets, <location> moves, no time for
    grace notes)."""
    out, pos, tup = [], 0.0, 1.0
    for el in voice:
        if el.tag == "Tuplet":
            tup = int(el.findtext("normalNotes")) / int(el.findtext("actualNotes"))
        elif el.tag == "endTuplet":
            tup = 1.0
        elif el.tag == "location":
            n, _, d = (el.findtext("fractions") or "0").strip().partition("/")
            pos += 4 * int(n) / int(d or 1)
        elif el.tag in ("Chord", "Rest"):
            if el.tag == "Chord" and any(el.find(g) is not None for g in GRACE):
                continue
            if el.tag == "Chord":
                out.extend((round(pos, 4), lyr) for lyr in el.findall("Lyrics"))
            t = el.findtext("durationType")
            pos += DUR.get(t, 0) * (2 - 0.5 ** int(el.findtext("dots") or 0)) * tup
    return out


def words(score):
    """Every sung word of the score: [{"bar", "beat", "word", "part", "text", "line", "at"}], in
    each part's and verse's order. "beat" is where its first syllable starts in the bar, "text"
    the word as printed, "line" the lyrics around it and "at" its index there."""
    root = etree.parse(score).getroot()
    names = _part_names(root)
    out = []
    for staff in root.iter("Staff"):
        if staff.find("Measure") is None:
            continue
        streams = {}   # (voice, verse) -> [(bar, beat, syllable, syllabic)]
        for m_no, m in enumerate(staff.findall("Measure"), 1):
            for v_no, voice in enumerate(m.findall("voice")):
                for beat, lyr in _syllables(voice):
                    t = lyr.find("text")
                    text = "".join(t.itertext()) if t is not None else ""
                    streams.setdefault((v_no, int(lyr.findtext("no") or 0)), []).append(
                        (m_no, beat, text, lyr.findtext("syllabic") or "single"))
        for _, syls in sorted(streams.items()):
            ws, cur = [], None
            for bar, beat, text, syllabic in syls:
                if cur is None or syllabic in ("single", "begin"):
                    cur = {"bar": bar, "beat": beat, "text": text}
                    ws.append(cur)
                else:
                    cur["text"] += text
                if syllabic in ("single", "end"):
                    cur = None
            for i, w in enumerate(ws):
                key = word_key(w["text"])
                if key:
                    out.append({"bar": w["bar"], "beat": w["beat"], "word": key,
                                "part": names.get(staff.get("id"), ""), "text": w["text"],
                                "line": " ".join(x["text"] for x in ws[max(0, i - 4):i + 5]), "at": min(i, 4)})
    return out


def key(c):
    """A place's identity: (bar, beat, word)."""
    return (int(c["bar"]), round(float(c["beat"]), 3), c["word"])


def places(score):
    """Each place once, with the parts that start the word there: [{"bar", "beat", "word", "n",
    "parts", "line", "at"}] in bar order; "n" counts the places of the word in its bar (1 for the
    first beat it starts on), for people to read. Parts singing a word together share its place;
    one entering later on the same word has a place of its own."""
    out = {}
    for w in words(score):
        k = key(w)
        if k not in out:
            out[k] = {"bar": w["bar"], "beat": w["beat"], "word": w["word"], "parts": [],
                      "line": w["line"], "at": w["at"]}
        if w["part"] not in out[k]["parts"]:
            out[k]["parts"].append(w["part"])
    rows = [out[k] for k in sorted(out)]
    for r in rows:
        r["n"] = 1 + sum(1 for q in rows if q["bar"] == r["bar"] and q["word"] == r["word"] and q["beat"] < r["beat"])
    return rows


def load(song_dir):
    """The song's choices: {"places": [...], "everywhere": {...}} (empty when none were made)."""
    try:
        with open(os.path.join(song_dir, FILE), encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        d = {}
    return {"places": list(d.get("places") or []), "everywhere": dict(d.get("everywhere") or {})}


def save(song_dir, choices):
    path = os.path.join(song_dir, FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"places": choices.get("places", []), "everywhere": choices.get("everywhere", {})},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def stale(choices, score):
    """The place choices whose word the score no longer sings there."""
    here = {key(p) for p in places(score)}
    return [c for c in choices.get("places", []) if key(c) not in here]
