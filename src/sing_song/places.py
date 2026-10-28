"""The places a song's parts sing each word, and the readings chosen for them (eeron-omat-taskit#144).

A place is (bar, word, n): the bar a word's first syllable is in (1-based, as unroll.py's "bar"),
the word as word_key() gives it, and which time that word starts in that bar (1 for the first).
It is not tied to a part: a reading chosen there is sung by every part singing that word there.

The choices are kept with the song in songs/<slug>/pronounce.json:

    {"places": [{"bar": 3, "word": "wind", "n": 1, "sounds": "W IH1 N D"}],
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


def words(score):
    """Every sung word of the score: [{"bar", "word", "n", "part", "text", "line"}], in each
    part's and verse's order. "text" is the word as printed, "line" the lyrics around it."""
    root = etree.parse(score).getroot()
    names = _part_names(root)
    out = []
    for staff in root.iter("Staff"):
        if staff.find("Measure") is None:
            continue
        streams = {}   # (voice, verse) -> [(bar, syllable, syllabic)]
        for m_no, m in enumerate(staff.findall("Measure"), 1):
            for v_no, voice in enumerate(m.findall("voice")):
                for chord in voice.findall("Chord"):
                    for lyr in chord.findall("Lyrics"):
                        t = lyr.find("text")
                        text = "".join(t.itertext()) if t is not None else ""
                        streams.setdefault((v_no, int(lyr.findtext("no") or 0)), []).append(
                            (m_no, text, lyr.findtext("syllabic") or "single"))
        for _, syls in sorted(streams.items()):
            ws, cur = [], None
            for bar, text, syllabic in syls:
                if cur is None or syllabic in ("single", "begin"):
                    cur = {"bar": bar, "text": text}
                    ws.append(cur)
                else:
                    cur["text"] += text
                if syllabic in ("single", "end"):
                    cur = None
            seen = {}
            for i, w in enumerate(ws):
                key = word_key(w["text"])
                if not key:
                    continue
                seen[(w["bar"], key)] = n = seen.get((w["bar"], key), 0) + 1
                line = [x["text"] for x in ws[max(0, i - 4):i + 5]]
                out.append({"bar": w["bar"], "word": key, "n": n, "part": names.get(staff.get("id"), ""),
                            "text": w["text"], "line": " ".join(line), "at": min(i, 4)})
    return out


def places(score):
    """Each place once, with the parts that sing a word there: [{"bar", "word", "n", "parts",
    "line", "at"}] in bar order."""
    out = {}
    for w in words(score):
        key = (w["bar"], w["word"], w["n"])
        if key not in out:
            out[key] = {"bar": w["bar"], "word": w["word"], "n": w["n"], "parts": [], "line": w["line"], "at": w["at"]}
        if w["part"] not in out[key]["parts"]:
            out[key]["parts"].append(w["part"])
    return [out[k] for k in sorted(out)]


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
    here = {(p["bar"], p["word"], p["n"]) for p in places(score)}
    return [c for c in choices.get("places", []) if (c.get("bar"), c.get("word"), c.get("n")) not in here]
