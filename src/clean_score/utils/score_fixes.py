"""Apply score edits that a person or an AI authorised, from a recorded list.

Some OCR damage cannot be repaired automatically and should not be guessed at: a
dot the scanner invented, a melisma slur it dropped. The automatic passes decline
these on purpose — `overfull_measures` will not touch a note, and slurs are never
mirrored because they connect different pitches and cannot be pitch-checked.

What is left is a judgement about the printed page, and the answer belongs in the
song folder as data rather than in a one-off hand edit that the next rebuild
erases. Each entry carries a `why`, because six months later the diff will not say
why a note lost its dot.

    [{"kind": "undot", "staff": 3, "measure": 26, "index": 0, "why": "..."},
     {"kind": "slur",  "staff": 4, "measure": 32, "index": 0, "span": 1, "why": "..."},
     {"kind": "append", "staff": 2, "measure": 73,
      "from": ["eighth:50", "eighth:50", "eighth:50", "eighth:R"],
      "add":  ["quarter:R"],
      "why": "..."}]

`staff` and `measure` are 1-based and refer to the **cleaned** score, where each
staff carries one voice. `index` counts chords in that measure from 0. Applying is
strict: an entry that does not match raises, because a silently skipped fix would
leave the score looking repaired when it is not.

`append` works on the end of a bar, which is where every edit the other two kinds
cannot express has landed so far: the trailing rest an engraver drew once for two
voices, or the notes a scan dropped and padded over (`"drop": 1` takes the padding
rest off first). `from` is what the bar reads **now**, and the fix refuses to apply
unless it still reads that way — so a pipeline change that alters the bar fails the
build instead of quietly writing an old answer over a new one.

Tokens are `duration[.]:pitch` (`eighth:50`, `quarter.:60` for a dotted quarter),
`duration[.]:R` for a rest, and `+` between the notes of a chord. `[tuplet` and
`tuplet]` mark a triplet bracket: they appear in `from` and cannot be written. A
whole-bar rest reads as `measure:R` and cannot be written either — it needs the bar's
own length, which a fix has no way to know, so write the rests out instead. The
note's **spelling** (MuseScore's tpc) is derived from the pitch and is deliberately
not part of the token: the first fixes to carry one by hand got three of four wrong,
which puts a note on the wrong line while it still sounds right.

Most edits are none of those three kinds, and the shapes that are missing are not
exotic — taking one notehead off a chord, or turning a bar-length rest into a
whole-bar rest, both came up on one song in one sitting. So a fix can also just be
a **sentence**:

    {"kind": "text",
     "what": "B1 bar 40, last eighth: drop the D, keep the C. The page prints one
              head per bass voice and the basses cross here."}

Nothing here interprets it. `apply_fixes` leaves a `text` entry alone and
`free_text` hands the sentences back, so cleaning can say out loud that the score
is not fully repaired yet instead of either refusing to clean at all or skipping in
silence. Applying one is a person's job — or an agent asked to do it, which is how
the sentence came to be written in the first place. What the file guarantees is
that the judgement survives the next rebuild.
"""
import logging
from fractions import Fraction
from typing import Dict, List, Optional

from lxml import etree

logger = logging.getLogger(__name__)

_DUR = {
    "whole": Fraction(1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}


class FixError(ValueError):
    """A recorded fix does not match the score it was recorded against."""


def _measure(root: etree._Element, staff_id: int, measure_no: int) -> etree._Element:
    staves = [s for s in root.findall(".//Score/Staff")
              if s.get("id") == str(staff_id) and s.find("Measure") is not None]
    if not staves:
        raise FixError(f"no staff {staff_id}")
    measures = staves[0].findall("Measure")
    if measure_no < 1 or measure_no > len(measures):
        raise FixError(f"staff {staff_id} has no measure {measure_no}")
    return measures[measure_no - 1]


def _chords(root: etree._Element, staff_id: int, measure_no: int) -> List[etree._Element]:
    measure = _measure(root, staff_id, measure_no)
    body = measure.find("voice") if measure.find("voice") is not None else measure
    return [el for el in body if el.tag == "Chord"]


def _length(chord: etree._Element) -> Fraction:
    base = _DUR.get((chord.findtext("durationType") or "").strip(), Fraction(0))
    dots = int((chord.findtext("dots") or "0").strip() or 0)
    return base * _DOT.get(dots, Fraction(1))


def _undot(chord: etree._Element) -> str:
    dots = chord.find("dots")
    if dots is None:
        raise FixError("that chord has no dot to remove")
    chord.remove(dots)
    return "removed a dot"


def _slur(chords: List[etree._Element], start: int, span: int) -> str:
    """Slur chord `start` to chord `start + span`, so the later ones carry no syllable."""
    end = start + span
    if end >= len(chords):
        raise FixError(f"cannot slur {span} past chord {start}: only {len(chords)} chords")
    distance = sum(_length(chords[i]) for i in range(start, end))

    head = etree.SubElement(chords[start], "Spanner", type="Slur")
    etree.SubElement(etree.SubElement(head, "Slur"), "up").text = "up"
    loc = etree.SubElement(etree.SubElement(head, "next"), "location")
    etree.SubElement(loc, "fractions").text = str(distance)

    tail = etree.SubElement(chords[end], "Spanner", type="Slur")
    loc = etree.SubElement(etree.SubElement(tail, "prev"), "location")
    etree.SubElement(loc, "fractions").text = f"-{distance}"
    return f"slurred {span} note(s) from chord {start}"


# A bar as tokens, so a recorded fix can say what it expects to find and what it
# should read afterwards. Rests are "R"; a chord's notes join with "+". Tuplet
# brackets are marked, because two bars that differ only by one are different bars
# and a fix recorded against the other one must not quietly apply.


def _token(el: etree._Element) -> str:
    dur = (el.findtext("durationType") or "").strip()
    dots = int((el.findtext("dots") or "0").strip() or 0)
    dur += "." * dots
    if el.tag == "Rest":
        return f"{dur}:R"
    return f"{dur}:" + "+".join(n.findtext("pitch") or "?" for n in el.findall("Note"))


_MARKERS = {"Tuplet": "[tuplet", "endTuplet": "tuplet]"}


def _bar_tokens(measure: etree._Element) -> List[str]:
    body = measure.find("voice") if measure.find("voice") is not None else measure
    out: List[str] = []
    for el in body:
        if el.tag in ("Chord", "Rest"):
            out.append(_token(el))
        elif el.tag in _MARKERS:
            out.append(_MARKERS[el.tag])
    return out


def _write_token(voice: etree._Element, token: str) -> None:
    dur, _, notes = token.partition(":")
    dots = dur.count(".")
    dur = dur.replace(".", "")
    if dur == "measure":
        raise FixError(
            f"cannot write {token!r}: a measure rest needs the bar's own length, which a "
            "fix cannot know. Write the rests out (e.g. 'quarter:R'). Reading one in a "
            "'from' list is fine.")
    if dur not in _DUR:
        raise FixError(f"unknown duration {dur!r} in {token!r}")
    el = etree.SubElement(voice, "Rest" if notes.strip().upper() == "R" else "Chord")
    etree.SubElement(el, "durationType").text = dur
    if dots:
        etree.SubElement(el, "dots").text = str(dots)
    if el.tag == "Chord":
        for part in notes.split("+"):
            try:
                pitch_no = int(part)
            except ValueError:
                raise FixError(f"bad pitch {part!r} in {token!r}")
            note = etree.SubElement(el, "Note")
            etree.SubElement(note, "pitch").text = str(pitch_no)
            etree.SubElement(note, "tpc").text = str(_SPELLING[pitch_no % 12])


# MuseScore's tpc is the note's spelling: 14 is C and each step of one is a fifth
# up, so 15 is G and 21 is C sharp. Derived from the pitch, never written by hand —
# the first fixes to carry a hand-written spelling got three of four values wrong,
# which puts the note on the wrong line of the staff while sounding correct.
_SPELLING = {0: 14, 1: 21, 2: 16, 3: 23, 4: 18, 5: 13,
             6: 20, 7: 15, 8: 22, 9: 17, 10: 24, 11: 19}


def _append_bar(measure: etree._Element, expect: List[str], add: List[str],
                drop: int = 0) -> str:
    """Work on the end of a bar, leaving the rest of it exactly as it was.

    Every recorded fix so far concerns the end of a bar: the rest an engraver drew
    once for two voices, or the notes a scan dropped and padded over. Working there
    keeps whatever came earlier — a triplet bracket, a tie — which a fix that rewrote
    the bar from tokens could not carry.

    `drop` takes that many rests off the end first, for the common case where the scan
    padded with a rest in place of the notes it lost: appending alone would leave the
    padding and overfill the bar. Only rests: dropping a note is a musical decision
    that wants writing out, and a dropped note can leave a tie or a tuplet bracket
    pointing at nothing.
    """
    found = _bar_tokens(measure)
    if found != list(expect):
        raise FixError(f"bar reads {found} now, but the fix was recorded against {list(expect)}")
    body = measure.find("voice")
    if body is None:
        body = etree.SubElement(measure, "voice")
    if drop:
        items = [el for el in body if el.tag in ("Chord", "Rest")]
        if drop > len(items):
            raise FixError(f"cannot drop {drop} from a bar with {len(items)} notes/rests")
        going = items[-drop:]
        not_rests = [el.tag for el in going if el.tag != "Rest"]
        if not_rests:
            raise FixError(
                f"drop only takes rests off the end; this would remove {not_rests}. "
                "Removing a note is a musical decision — write the bar out by hand.")
        for el in going:
            body.remove(el)
    for token in add:
        _write_token(body, token)
    dropped = f"dropped the last {drop} and " if drop else ""
    return f"{dropped}added {list(add)} to the end of the bar"


def free_text(fixes: List[Dict]) -> List[str]:
    """The sentences among the recorded fixes, in file order.

    Nothing applies these; this is what lets a caller say they are still outstanding.
    An entry with no sentence in it raises rather than reading as nothing to do — an
    empty reminder and a repaired score look identical from here.
    """
    said: List[str] = []
    for n, fix in enumerate(fixes, start=1):
        if not isinstance(fix, dict) or fix.get("kind") != "text":
            continue
        text = (fix.get("what") or fix.get("why") or "").strip()
        if not text:
            raise FixError(
                f"the free-text fix at position {n} says nothing — give it a 'what', "
                "or take it out of the file")
        said.append(text)
    return said


def apply_fixes(root: etree._Element, fixes: List[Dict]) -> List[str]:
    """Apply each recorded fix. Returns one line per fix applied, for the build log.

    Free-text fixes are not applied — they are a sentence, not an instruction anything
    here can follow — so they are absent from the return value. `free_text` reads them.
    """
    free_text(fixes)  # a sentence that says nothing is a mistake worth catching early
    done: List[str] = []
    for fix in fixes:
        kind = fix.get("kind")
        if kind == "text":
            continue
        staff, measure = int(fix["staff"]), int(fix["measure"])
        try:
            if kind == "append":
                # No chord index: a bar this fix repairs may have no chords at all yet.
                what = _append_bar(_measure(root, staff, measure), fix.get("from", []),
                                   fix.get("add", []), int(fix.get("drop", 0)))
            elif kind in ("undot", "slur"):
                index = int(fix.get("index", 0))
                chords = _chords(root, staff, measure)
                if index >= len(chords):
                    raise FixError(
                        f"staff {staff} m{measure} has {len(chords)} chords, no index {index}")
                what = (_undot(chords[index]) if kind == "undot"
                        else _slur(chords, index, int(fix.get("span", 1))))
            else:
                raise FixError(f"unknown fix kind {kind!r}")
        except FixError as exc:
            # Say which entry, not just what went wrong: two bars of the same song can
            # read identically, and the message is all the reader gets.
            raise FixError(f"staff {staff} m{measure} ({kind}): {exc}") from None
        line = f"staff {staff} m{measure}: {what} — {fix.get('why', 'no reason recorded')}"
        logger.debug(line)
        done.append(line)
    return done
