"""Fold a score's divisi lines into the parts they divide (#104).

song-app's cleaned score has one staff per line, and a line a page divides further is named as
its part plus a letter: Katedralen's eight sopranos are S1, S1b, S1c, S1d, S2, S2b, ... The site
shows the parts the choir chose (S1, S2, A1, ...), so each lettered line goes into its part's
staff as a further voice (2, 3, 4), bar by bar, and its own staff goes. A bar where the line
rests, or sings just what the part's own line sings, adds no voice. Every line is still sung
from the unfolded score: `fold()` says which staves each folded part was made from.

    fold(src, dst) -> [(part name, [staff ids in src, the part's own first])]
"""
import copy
import re

from lxml import etree

LETTERED = re.compile(r"^(.*\d)([b-h])$")
# what a line's bar carries over into the part's staff: its notes and what hangs on them, never
# a clef, key, time or tempo, which the part's own first voice already has
KEEP = {"Chord", "Rest", "Tuplet", "endTuplet", "Beam", "Spanner", "Dynamic", "Breath", "location"}
VOICES = 4   # MuseScore's voices per staff


def part_name(part):
    return (part.findtext("trackName") or "").strip()


def plan(names):
    """{lettered line's index: its part's index} for the names in staff order; a lettered name
    whose part is missing stays a part of its own."""
    where = {n: i for i, n in enumerate(names)}
    out = {}
    for i, n in enumerate(names):
        m = LETTERED.match(n)
        if m and m.group(1) in where:
            out[i] = where[m.group(1)]
    return out


def tied(note):
    """Which ways a note is tied: a held note is not the same sound as one struck again."""
    return tuple(sorted(k for sp in note.findall("Spanner") if sp.get("type") == "Tie"
                        for k in ("prev", "next") if sp.find(k) is not None))


def notes_of(voice):
    """A voice's sound, lyrics left out: lengths, dots, pitches and ties in order, with what moves
    a note in time (a location shift, a tuplet's ratio, a measure rest's own length)."""
    out = []
    for el in voice:
        if el.tag in ("Chord", "Rest"):
            out.append((el.tag, el.findtext("durationType"), el.findtext("dots"), el.findtext("duration"),
                        tuple(sorted((n.findtext("pitch"), tied(n)) for n in el.findall("Note")))))
        elif el.tag == "Tuplet":
            out.append((el.tag, el.findtext("actualNotes"), el.findtext("normalNotes")))
        elif el.tag == "endTuplet":
            out.append((el.tag,))
        elif el.tag == "location":
            out.append((el.tag, el.findtext("fractions"), el.findtext("measures")))
    return out


def resting(voice):
    return all(el.tag != "Chord" for el in voice)


def fold(src, dst):
    tree = etree.parse(src)
    score = tree.getroot().find("Score")
    parts = score.findall("Part")
    staves = [s for s in score.findall("Staff") if s.find("Measure") is not None or s.find("VBox") is not None]
    if len(parts) != len(staves):
        raise SystemExit(f"{src}: {len(parts)} parts for {len(staves)} staves, one staff per part expected")
    names = [part_name(p) for p in parts]
    into = plan(names)
    members = {i: [staves[i].get("id")] for i in range(len(parts)) if i not in into}
    for line, part in sorted(into.items()):
        members[part].append(staves[line].get("id"))
        base = staves[part].findall("Measure")
        bars = staves[line].findall("Measure")
        if len(base) != len(bars):
            raise SystemExit(f"{names[line]} has {len(bars)} bars, {names[part]} {len(base)}")
        for k, (mb, ml) in enumerate(zip(base, bars), 1):
            own = mb.findall("voice")
            for voice in ml.findall("voice"):
                if resting(voice) or any(notes_of(voice) == notes_of(v) for v in own):
                    continue
                if len(own) >= VOICES:
                    raise SystemExit(f"{names[part]} bar {k}: more than {VOICES} different lines")
                new = etree.SubElement(mb, "voice")
                if own:     # a measure may have no voice of its own (unroll.py allows it)
                    last = own[-1]
                    last.addnext(new)
                    new.text, new.tail = last.text, last.tail
                for el in voice:
                    if el.tag in KEEP:
                        new.append(copy.deepcopy(el))
                own.append(new)
    gone = set(into)
    for i in sorted(gone, reverse=True):
        score.remove(parts[i])
        score.remove(staves[i])
    # staff ids run 1, 2, ... again, in the parts and in the score
    for n, (p, s) in enumerate(zip(score.findall("Part"), [s for k, s in enumerate(staves) if k not in gone]), 1):
        p.find("Staff").set("id", str(n))
        s.set("id", str(n))
    tree.write(dst, encoding="UTF-8", xml_declaration=True)
    return [(names[i], members[i]) for i in range(len(parts)) if i not in gone]
