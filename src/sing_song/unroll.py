"""One part's notes in the order the practice-track site plays them, timed by its clock.

    python3 unroll.py <score.mscx> <staff id> <timing.json> <out.json>

timing.json (song-app's publish bundle) lists [seconds, measure (0-based), beat] points in
playback order, repeats and endings included. The part is read measure by measure as
extract.py does; each pass through a measure is timed from its own points, and the n-th pass
sings verse n where the measure has one (else the first verse). A note with no syllable of its
own is marked "melisma" when a slur or an extender line carries the syllable over it; otherwise
the singer repeats the syllable (sing_ufr.py). Each sung note carries its "bar" (1-based) and
"beat", where it starts in the bar in quarter notes as written (before swing): its place, as
places.py finds it in the score.

LINE=high or LINE=low picks one line where the part splits (#45): high sings the top note of
each chord; low sings the bottom note, and voice 2 where a measure writes a full voice 2,
with voice 1's syllables at the same beats (not on a note tied from the one before). Unset: voice 1, bottom note (as before)."""
import json, os, sys
from lxml import etree
src, staff_id, timing, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
DUR = {"long": 16, "breve": 8, "whole": 4, "half": 2, "quarter": 1, "eighth": 0.5, "16th": 0.25,
       "32nd": 0.125, "64th": 0.0625, "128th": 0.03125}
def length(el):
    """A chord's or rest's length in quarter notes, before any tuplet. A value missing from DUR
    stops the part rather than counting as nothing: Es ist ein Ros's breve and long rests did,
    and its lower parts were sung from 0 s (musescore-choir-plugins#419). n dots add half, then
    a quarter, ... of the note: a double dot is 1.75, not 1.5."""
    t = el.findtext("durationType")
    if t == "measure": return bar
    if t not in DUR:
        bar_no = len(measures) + 1
        sys.exit(f"bar {bar_no}: unknown note value {t!r}")
    return DUR[t] * (2 - 0.5 ** int(el.findtext("dots") or 0))
root = etree.parse(src).getroot()
staff = [s for s in root.iter("Staff") if s.get("id") == staff_id and s.find("Measure") is not None][0]
measures, bar = [], 4.0
slur = 0          # slurs open at this point of the score: a note under one carries the syllable on
extender = False  # the last syllable has an extender line: its following notes carry it on
LINE = os.environ.get("LINE", "")
# grace notes take no beat time of their own: the syllable starts on them, quickly, and moves on
# to the main note (#45: Lempilintu's were sung as long eighths and pushed the bar late)
GRACE_BEFORE = ("acciaccatura", "appoggiatura", "grace4", "grace16", "grace32")
GRACE_AFTER = ("grace8after", "grace16after", "grace32after")
GRACE_SEC, GRACE_SHARE = 0.08, 0.25
def moved(el):
    """How far a <location> in a voice moves the beat cursor, in quarter notes: MuseScore writes
    one before a note that does not start where the one before it ended (#104)."""
    f = (el.findtext("fractions") or "0").strip()
    n, _, d = f.partition("/")
    return 4 * int(n) / int(d or 1)
def lyric_at(voice):
    """Voice 1's lyrics by beat position, for a voice 2 that has none of its own."""
    out, pos, tup = {}, 0.0, 1.0
    for el in voice:
        if el.tag == "Tuplet": tup = int(el.findtext("normalNotes")) / int(el.findtext("actualNotes")); continue
        if el.tag == "endTuplet": tup = 1.0; continue
        if el.tag == "location": pos += moved(el); continue
        if el.tag not in ("Chord", "Rest"): continue
        d = length(el)
        if el.findall("Lyrics"): out[round(pos, 4)] = el.findall("Lyrics")
        pos += d * tup
    return out
# Swing (eeron-omat-taskit#52): song-app's `swing` fix writes a system text carrying
# MuseScore's swing setting on the top staff. In a swinging bar an off-beat eighth starts
# two thirds of the way into its beat, as MuseScore plays it; beats themselves stay put.
top = next(s for s in root.iter("Staff") if s.find("Measure") is not None)
swinging, swings = False, []
for tm in top.findall("Measure"):
    for el in tm.iter("SystemText", "StaffText"):
        if el.find("swing") is not None: swinging = el.find("swing").get("unit") == "eighth"
    swings.append(swinging)
quarter_beat = True
def swing_bar(evs):
    """Move each off-beat eighth's start (and the end before it) to beat + 2/3. Like
    MuseScore, not where the note there or the one before it is shorter than an eighth,
    nor in a tuplet."""
    for prev, e in zip(evs, evs[1:]):
        k = int(e["a"])
        if (abs(e["a"] - k - 0.5) < 1e-6 and abs(prev["b"] - e["a"]) < 1e-6 and not e.get("tup")
                and e["b"] - e["a"] > 0.5 - 1e-6 and prev["b"] - prev["a"] > 0.5 - 1e-6):
            prev["b"] = e["a"] = k + 2 / 3
for m in staff.findall("Measure"):
    ts = m.find(".//TimeSig")
    if ts is not None: bar = int(ts.findtext("sigN")) * 4 / int(ts.findtext("sigD")); quarter_beat = ts.findtext("sigD") == "4"
    evs, pos, tup = [], 0.0, 1.0
    reached = 0.0                    # how far the voice's notes and rests have reached in the bar
    if m.find("voice") is None:       # an empty bar: rest through it (its own length when it has one)
        ln = m.get("len"); full = bar
        if ln and "/" in ln: full = int(ln.split("/")[0]) * 4 / int(ln.split("/")[1])
        measures.append([{"pitch": None, "a": 0.0, "b": full, "verses": {}}]); continue
    voices = m.findall("voice")
    voice, borrow = voices[0], None
    # the low line is voice 2 wherever it has notes, silent where it rests (Eero on #439: it
    # used to fall back to voice 1 in any bar voice 2 rested in, so a voice2 fix was never sung)
    if LINE == "low" and len(voices) > 1 and any(e.tag == "Chord" for e in voices[1]):
        voice, borrow = voices[1], lyric_at(voices[0])
    graces = []
    for el in voice:
        if el.tag == "Chord" and any(el.find(g) is not None for g in GRACE_BEFORE + GRACE_AFTER):
            if any(el.find(g) is not None for g in GRACE_BEFORE):
                gs = el.findall("Note"); graces.append(int((max if LINE == "high" else min)(gs, key=lambda x: int(x.findtext("pitch"))).findtext("pitch")))
            continue
        if el.tag == "Tuplet": tup = int(el.findtext("normalNotes")) / int(el.findtext("actualNotes")); continue
        if el.tag == "endTuplet": tup = 1.0; continue
        if el.tag == "location":
            # moves the beat cursor only: MuseScore also writes a pair, back and then forward again,
            # around a text or spanner placed between two notes, and that is no rest
            pos += moved(el); continue
        if el.tag not in ("Chord", "Rest"): continue
        if pos > reached + 1e-9:      # a gap a location leaves before a note or rest is silent (#104)
            evs.append({"pitch": None, "a": reached, "b": pos, "verses": {}, "tup": False})
        d = length(el)
        d *= tup
        if el.tag == "Rest": evs.append({"pitch": None, "a": pos, "b": pos + d, "verses": {}, "tup": tup != 1.0})
        else:
            ns = el.findall("Note")
            n = max(ns, key=lambda x: int(x.findtext("pitch"))) if LINE == "high" else min(ns, key=lambda x: int(x.findtext("pitch")))
            if LINE == "": n = ns[0]
            # a voice-2 note tied from the one before holds it: it borrows no word (musescore-choir-plugins#434)
            held = n.find("Spanner[@type='Tie']/prev") is not None
            lyrics = el.findall("Lyrics") or (borrow.get(round(pos, 4), []) if borrow is not None and not held else [])
            verses = {int(l.findtext("no") or 0): (l.findtext("text"), l.findtext("syllabic")) for l in lyrics}
            starts = len(el.findall("Spanner[@type='Slur']/next")); ends = len(el.findall("Spanner[@type='Slur']/prev"))
            for gi, g in enumerate(graces):  # the syllable on the first grace note, the rest held over
                evs.append({"pitch": g, "a": pos, "b": pos, "at": pos, "verses": verses, "tie": False, "grace": True,
                            "melisma": (slur > 0 or (not lyrics and extender)) if gi == 0 else True, "tup": tup != 1.0})
                verses = {}
            evs.append({"pitch": int(n.findtext("pitch")), "a": pos, "b": pos + d, "at": pos, "verses": verses,
                        "tie": n.find("Spanner[@type='Tie']/next") is not None,
                        "tied_back": n.find("Spanner[@type='Tie']/prev") is not None,
                        "melisma": (slur > 0 or (not lyrics and extender)) if not graces else True, "tup": tup != 1.0})
            graces = []
            if lyrics: extender = any((l.findtext("ticks") or l.findtext("ticks_f") or "0") not in ("0", "0/1") for l in lyrics)
            slur = max(0, slur + starts - ends)
        pos += d
        reached = max(reached, pos)
    if quarter_beat and len(measures) < len(swings) and swings[len(measures)]:
        swing_bar(evs)
    measures.append(evs)
pts = json.load(open(timing))["points"]
runs = []                                   # [(measure, [(beat, sec)...])] in playback order
for sec, m, b in pts:
    if not runs or runs[-1][0] != m or b < runs[-1][1][-1][0]: runs.append((m, []))
    runs[-1][1].append((b, sec))
end_of_song = json.load(open(timing)).get("duration")
seen, notes, tie_prev = {}, [], False
for k, (m, row) in enumerate(runs):
    nxt = runs[k + 1][1][0][1] if k + 1 < len(runs) else end_of_song
    if nxt is None: nxt = row[-1][1] + (row[-1][1] - row[-2][1] if len(row) > 1 else 0.5)
    def at(beat):
        for (b0, s0), (b1, s1) in zip(row, row[1:]):
            if b0 <= beat <= b1: return s0 + (s1 - s0) * (beat - b0) / (b1 - b0) if b1 > b0 else s0
        if beat <= row[0][0]: return row[0][1]
        last_b, last_s = row[-1]; span = max(1e-9, (measures[m][-1]["b"] if measures[m] else last_b + 1) - last_b)
        return last_s + (nxt - last_s) * (beat - last_b) / span
    p = seen.get(m, 0); seen[m] = p + 1
    verse = p if any(p in e["verses"] for e in measures[m]) else 0
    for e in measures[m]:
        sec = at(e["b"]) - at(e["a"])
        if e["pitch"] is None:
            notes.append({"pitch": None, "beats": e["b"] - e["a"], "syl": None, "sec": sec}); tie_prev = False; continue
        if e.get("tied_back") and not tie_prev and not e["verses"].get(verse):
            # tied from a note this pass never played (a repeat back to it, #74: Lemmen nosto's
            # bar 21): MuseScore leaves it silent, so the piano does, and so does the singer
            notes.append({"pitch": None, "beats": e["b"] - e["a"], "syl": None, "sec": sec}); continue
        syl, sylb = e["verses"].get(verse, (None, None))
        if tie_prev and syl is None and notes and notes[-1]["pitch"] is not None and not e.get("grace"):
            notes[-1]["beats"] += e["b"] - e["a"]; notes[-1]["sec"] += sec
        else:
            notes.append({"pitch": e["pitch"], "beats": e["b"] - e["a"], "syl": syl, "syllabic": sylb, "sec": sec,
                          "melisma": e["melisma"], "bar": m + 1, "beat": round(e["at"], 4),
                          **({"grace": True} if e.get("grace") else {})})
        tie_prev = e["tie"]
    gap = nxt - at(measures[m][-1]["b"] if measures[m] else 0)
    if gap > 1e-3:                          # a measure the score leaves short: rest to the next beat
        notes.append({"pitch": None, "beats": 0, "syl": None, "sec": gap}); tie_prev = False
k = 0                                        # grace notes borrow their time from the note they lead to
while k < len(notes):
    if not notes[k].get("grace"): k += 1; continue
    j = k
    while j < len(notes) and notes[j].get("grace"): j += 1
    if j < len(notes):
        nx = notes[j]; total = min(GRACE_SEC * (j - k), nx["sec"] * GRACE_SHARE)
        for n in notes[k:j]:
            n["sec"] = total / (j - k); n["beats"] = nx["beats"] * n["sec"] / nx["sec"] if nx["sec"] else 0
        nx["beats"] -= sum(n["beats"] for n in notes[k:j]); nx["sec"] -= total
    # kept marked: the singer ignores it, and check_onsets.py skips these and the note after
    # them, which starts late by the time they borrow
    k = j
notes = [n for n in notes if n["sec"] > 1e-4]        # a grace-length rest the clock gives no time
json.dump(notes, open(out, "w"), ensure_ascii=False, indent=0)
print(len(notes), "notes", round(sum(n["sec"] for n in notes), 3), "s", len(runs), "measures played")
