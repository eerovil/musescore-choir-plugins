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

A fifth kind, `rhythm`, gives every note and rest of a bar a new length and leaves
the notes themselves alone. It is how a person's pick among the readings homr weighed
for an unsure bar is recorded (#269), so its lengths are homr's own spelling, one per
note or rest in order: `note_4` a quarter, `note_12` a triplet eighth, `note_4.` a
dotted quarter. `from` is the bar as it reads now, as above. Triplet brackets are
written again around the new lengths, ties and slurs in or into the bar are moved to
the notes they joined, and `rhythm?` comes off the bar's red `⚠` mark: a person has
read the lengths against the page. Whatever else the mark says, and cleaning's own
marks on the bar, stay (#290).

    {"kind": "rhythm", "staff": 9, "measure": 25,
     "from": ["[tuplet", "eighth:43", "eighth:48", "eighth:50", "tuplet]", ...],
     "to": ["note_12", "note_12", "note_12", "note_6", "note_12", "note_4", ...],
     "why": "picked reading c against the page"}

Three more kinds are how a person's answer to a problem listed on the Fix stage is
recorded (#290). `pitch` gives one note of a chord another pitch, picked among the
pitches homr weighed for it; `from` is the bar as it reads now, `was` the MIDI pitch
the note has, and `to` / `tpc` what it gets — the spelling is homr's, read off the
page, rather than derived. Its red note goes, and so do the words `pitch?` and
`accidental?` on the bar's red mark; anything else the mark says stays. A `slur`
may reach into a later bar with `end_measure` and `end_index` instead of `span`,
because a slur the scan ran from one singer into another usually crosses a barline.
And `unmark` takes one red mark off a bar — the answer "the page prints no slur here"
— and is content if the mark is already gone, because what it records is that
nothing in that bar needs doing.

    {"kind": "pitch", "staff": 3, "measure": 12, "index": 1, "from": [...],
     "was": 62, "to": 63, "tpc": 11, "why": "picked homr's reading b ..."}
    {"kind": "slur", "staff": 5, "measure": 19, "index": 3,
     "end_measure": 20, "end_index": 0, "why": "..."}
    {"kind": "unmark", "staff": 5, "measure": 19, "text": "slur to A2 bar 20 removed; ..."}

`bar` (#295) puts a whole bar of one voice as a person picked it among homr's
readings of it: lengths and pitches together, so one pick never undoes another. `to`
is one entry per note or rest, its length in homr's spelling and its MIDI pitches
with their spelling (`tpcs`), read off the page by homr rather than derived. When
the new bar has the same notes and rests in the same places, the lengths are
rewritten as `rhythm` does and the pitches set, so ties, slurs and words stay where
they were — unless a note that changes pitch is tied, since the tie would then join
two different pitches. Otherwise the bar is written afresh, as the second reading
of the bar can be: ties and slurs reaching into it from outside are cut, as when
MuseScore refuses a bar (`rejected_bars`), and its words are put back on its notes
in order, the extra ones dropped.

    {"kind": "bar", "staff": 3, "measure": 12, "from": [...],
     "to": [{"value": "note_4.", "pitches": [62], "tpcs": [16]},
            {"value": "note_8", "pitches": [64], "tpcs": [18]}], "why": "..."}

`repeat` (#312) opens a repeat at a bar: a start-repeat sign on every staff. It has
no `staff`, because a repeat sign is drawn across the whole system and MuseScore
keeps one only when every staff carries it. It is how a person answers "where does
the repeat ending at bar N go back to?" when the scan read the end sign and missed
the start — which homr does for a sign that opens a printed system. A bar that
already opens a repeat refuses it.

    {"kind": "repeat", "measure": 46, "why": "the page prints |: at bar 46"}

Most edits are none of those kinds, and the shapes that are missing are not
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


def _onset(body: etree._Element, chord: etree._Element) -> Fraction:
    for el, at, _ in _timeline(body):
        if el is chord:
            return at
    raise FixError("that chord is not in the bar")


def _slur_across(root: etree._Element, staff: int, measure: int, index: int,
                 end_measure: int, end_index: int) -> str:
    """Slur one chord to a chord in the same or a later bar of the same staff."""
    starts = _chords(root, staff, measure)
    ends = _chords(root, staff, end_measure)
    if not 0 <= index < len(starts):
        raise FixError(f"m{measure} has {len(starts)} chords, no index {index}")
    if not 0 <= end_index < len(ends):
        raise FixError(f"m{end_measure} has {len(ends)} chords, no index {end_index}")
    if (end_measure, end_index) <= (measure, index):
        raise FixError("a slur has to end after it starts")
    first, last = starts[index], ends[end_index]
    along = _onset(last.getparent(), last) - _onset(first.getparent(), first)
    bars = end_measure - measure

    def location(parent: etree._Element, side: str, sign: int) -> None:
        loc = etree.SubElement(etree.SubElement(parent, side), "location")
        if bars:
            etree.SubElement(loc, "measures").text = str(sign * bars)
        value = sign * along
        if value:
            etree.SubElement(loc, "fractions").text = f"{value.numerator}/{value.denominator}"

    head = etree.SubElement(first, "Spanner", type="Slur")
    etree.SubElement(etree.SubElement(head, "Slur"), "up").text = "up"
    location(head, "next", 1)
    tail = etree.SubElement(last, "Spanner", type="Slur")
    location(tail, "prev", -1)
    return f"slurred chord {index} to chord {end_index} of m{end_measure}"


def _unmark(root: etree._Element, staff: int, measure: int, text: str) -> str:
    """Take the red mark saying `text` off a bar. Nothing to take off is fine."""
    gone = 0
    for el in list(_measure(root, staff, measure).iter("StaffText")):
        if (el.findtext("text") or "") == "⚠ " + text:
            el.getparent().remove(el)
            gone += 1
    return f"took the red mark off ({text})" if gone else f"no red mark left ({text})"


def tpc_of(step: str, alter: int) -> int:
    """MuseScore's tpc for a spelt note: F C G D A E B are 13..19, a sharp is +7."""
    return 13 + _LETTERS.index(step) + 7 * int(alter)


def _set_pitch(root: etree._Element, staff: int, measure: int, index: int,
               expect: List[str], was: int, to: int, tpc: int) -> str:
    bar = _measure(root, staff, measure)
    found = _bar_tokens(bar)
    if found != list(expect):
        raise FixError(f"bar reads {found} now, but the fix was recorded against {list(expect)}")
    chords = _chords(root, staff, measure)
    if not 0 <= index < len(chords):
        raise FixError(f"the bar has {len(chords)} chords, no index {index}")
    note = next((n for n in chords[index].findall("Note")
                 if (n.findtext("pitch") or "").strip() == str(was)), None)
    if note is None:
        raise FixError(f"chord {index} has no note at pitch {was}")
    note.find("pitch").text = str(to)
    for tag in ("tpc", "tpc2"):
        node = note.find(tag)
        if node is not None:
            node.text = str(tpc)
    if note.find("tpc") is None:
        etree.SubElement(note, "tpc").text = str(tpc)
    for color in note.findall("color"):
        note.remove(color)
    return (f"set chord {index}'s {note_name(was)} to {note_name(to, tpc)}"
            + _answered(_strike_words(bar, _PITCH_WORDS)))


#: The words of homr's red mark each kind of pick answers. A pitch answers the note's
#: pitch and accidental; new lengths answer the rhythm; a whole bar read against the
#: page answers everything about its notes. `voice?` (which singer a note is) and
#: cleaning's own marks — a slur taken out, a bar cut back, a bar MuseScore refused —
#: are about something else in the bar and stay until that is answered too (#290).
_PITCH_WORDS = ("pitch?", "accidental?")
_RHYTHM_WORDS = ("rhythm?",)
_BAR_WORDS = ("rhythm?", "pitch?", "accidental?", "notes?", "unchecked?")

#: How homr worded a mark before its marks became short words (#280).
_OLD_HOMR_MARK = "check against the page"


def _strike_words(bar: etree._Element, words) -> List[str]:
    """Take `words` out of homr's red marks on the bar. Returns what was answered.

    A mark left with nothing to say goes. homr's older one-sentence mark is answered
    whole, as it always was. Any other sentence is cleaning's, about another problem,
    and is left alone.
    """
    struck: List[str] = []
    for el in list(bar.iter("StaffText")):
        node = el.find("text")
        text = node.text if node is not None and node.text else ""
        if not text.startswith("⚠ "):
            continue
        if text[2:].startswith(_OLD_HOMR_MARK):
            el.getparent().remove(el)
            struck.append(text[2:])
            continue
        said = text[2:].split(" ")
        if not all(w.endswith("?") for w in said):
            continue
        left = [w for w in said if w not in words]
        struck.extend(w for w in said if w in words and w not in struck)
        if len(left) == len(said):
            continue
        if left:
            node.text = "⚠ " + " ".join(left)
        else:
            el.getparent().remove(el)
    return struck


def _answered(struck: List[str]) -> str:
    return f" and took {' '.join(struck)} off the red mark" if struck else ""


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


# A bar's lengths rewritten, for `rhythm`. The lengths come in homr's spelling,
# because that is where the readings a person picks between come from.

_TYPES = {1: "whole", 2: "half", 4: "quarter", 8: "eighth", 16: "16th", 32: "32nd",
          64: "64th"}
_TYPE_NUMBER = {name: number for number, name in _TYPES.items()}


def _parse_value(value: str):
    """`note_12.` -> ("note", 12, 1): kind, homr's denominator, dots."""
    kind, _, rest = (value or "").partition("_")
    number = rest.rstrip(".")
    if kind not in ("note", "rest") or not number.isdigit() or int(number) < 1:
        raise FixError(f"not a length: {value!r}")
    return kind, int(number), len(rest) - len(number)


def value_length(value: str) -> Fraction:
    """How long a homr length lasts, as a fraction of a whole note."""
    _, number, dots = _parse_value(value)
    return Fraction(1, number) * _DOT.get(dots, Fraction(1))


def is_triplet(value: str) -> bool:
    return _parse_value(value)[1] % 3 == 0


def triplet_groups(values: List[str]) -> List[tuple]:
    """Where the triplet brackets go: `(first, last)` index pairs, last inclusive.

    A bracket opens at the first triplet length and closes as soon as it has run a
    whole number of quarters and lands on a quarter, which is how a run of triplet
    eighths is bracketed three by three and three triplet quarters as one. Raises
    when a run cannot be bracketed so: a plain note inside it, or the bar ending first.
    """
    groups = []
    start, total, at = None, Fraction(0), Fraction(0)
    quarter = Fraction(1, 4)
    for index, value in enumerate(values):
        length = value_length(value)
        if is_triplet(value):
            if start is None:
                start, total = index, Fraction(0)
            total += length
            if (total / quarter).denominator == 1 and ((at + length) / quarter).denominator == 1:
                groups.append((start, index))
                start = None
        elif start is not None:
            raise FixError("a triplet would not close on a beat")
        at += length
    if start is not None:
        raise FixError("a triplet would run past the end of the bar")
    return groups


def element_value(el: etree._Element, in_tuplet: bool) -> Optional[str]:
    """A Chord or Rest's length in homr's spelling, or None when it has none."""
    number = _TYPE_NUMBER.get((el.findtext("durationType") or "").strip())
    if number is None:
        return None
    dots = int((el.findtext("dots") or "0").strip() or 0)
    if in_tuplet:
        number = number * 3 // 2 if (number * 3) % 2 == 0 else None
        if number is None:
            return None
    return f"{'rest' if el.tag == 'Rest' else 'note'}_{number}" + "." * dots


def _timeline(body: etree._Element) -> List:
    """The voice's Chords and Rests with where each starts in the bar.

    Raises for what this cannot account for: a gap (`location`), or a tuplet that is
    not a plain triplet, because then an onset here would be a guess.
    """
    out = []
    at = Fraction(0)
    ratio = None
    for el in body:
        if el.tag == "location":
            raise FixError("the bar has a gap in it; rewrite it in MuseScore")
        if el.tag == "Tuplet":
            normal, actual = el.findtext("normalNotes"), el.findtext("actualNotes")
            if (normal, actual) != ("2", "3"):
                raise FixError("the bar has a tuplet that is not a triplet")
            ratio = Fraction(2, 3)
        elif el.tag == "endTuplet":
            ratio = None
        elif el.tag in ("Chord", "Rest"):
            if (el.findtext("durationType") or "").strip() == "measure":
                raise FixError("the bar holds a whole-bar rest")
            length = _length(el) * (ratio or 1)
            out.append((el, at, length))
            at += length
    return out


def _spanner_ends(staff: etree._Element):
    """Every tie or slur end in the staff's first voice: (location, measure, onset).

    A `location` is relative: `measures` bars on and `fractions` along from the bar
    position of the element it hangs off, which is what has to move when the notes
    of a bar change length.
    """
    for mi, measure in enumerate(staff.findall("Measure")):
        body = measure.find("voice") if measure.find("voice") is not None else measure
        at = Fraction(0)
        ratio = None
        for el in body:
            if el.tag == "Tuplet":
                ratio = Fraction(2, 3)
            elif el.tag == "endTuplet":
                ratio = None
            spanners = ([el] if el.tag == "Spanner" else
                        list(el.iter("Spanner")) if el.tag in ("Chord", "Rest") else [])
            for spanner in spanners:
                for side in ("next", "prev"):
                    location = spanner.find(f"{side}/location")
                    if location is not None:
                        yield location, mi, at
            if el.tag in ("Chord", "Rest"):
                at += _length(el) * (ratio or 1)


def _relative(location: etree._Element):
    measures = int((location.findtext("measures") or "0").strip() or 0)
    fractions = Fraction((location.findtext("fractions") or "0").strip() or 0)
    return measures, fractions


def _set_fractions(location: etree._Element, value: Fraction) -> None:
    node = location.find("fractions")
    if value == 0:
        if node is not None:
            location.remove(node)
        return
    if node is None:
        node = etree.SubElement(location, "fractions")
    node.text = f"{value.numerator}/{value.denominator}"


def _rewrite_rhythm(root: etree._Element, staff_id: int, measure_no: int,
                    expect: List[str], values: List[str]) -> str:
    """Give each Chord and Rest of a bar the length `values` names, in order."""
    measure = _measure(root, staff_id, measure_no)
    found = _bar_tokens(measure)
    if found != list(expect):
        raise FixError(f"bar reads {found} now, but the fix was recorded against {list(expect)}")
    body = measure.find("voice") if measure.find("voice") is not None else measure
    timeline = _timeline(body)
    if len(timeline) != len(values):
        raise FixError(f"the bar has {len(timeline)} notes and rests, the fix names "
                       f"{len(values)} lengths")
    for (el, _, _), value in zip(timeline, values):
        kind = _parse_value(value)[0]
        if (kind == "rest") != (el.tag == "Rest"):
            raise FixError(f"{value!r} is a {kind}, but that is a {el.tag.lower()}")
    total = sum((length for _, _, length in timeline), Fraction(0))
    new_total = sum((value_length(v) for v in values), Fraction(0))
    if new_total != total:
        raise FixError(f"the new lengths add up to {new_total} of a whole note, the bar "
                       f"to {total}")

    # Where every note starts before and after, so ties and slurs can be moved.
    onsets = {}
    at = Fraction(0)
    for (el, old, _), value in zip(timeline, values):
        onsets[old] = at
        at += value_length(value)
    staff = measure.getparent()
    measure_index = staff.findall("Measure").index(measure)
    moves = []
    for location, mi, own in list(_spanner_ends(staff)):
        bars, along = _relative(location)
        target_bar, target = mi + bars, own + along
        new_own = onsets[own] if mi == measure_index and own in onsets else own
        new_target = target
        if target_bar == measure_index:
            if target not in onsets:
                raise FixError("a tie or slur ends inside a note of this bar")
            new_target = onsets[target]
        if mi == measure_index and own not in onsets:
            raise FixError("a tie or slur starts inside a note of this bar")
        if new_target - new_own != along:
            moves.append((location, new_target - new_own))

    # The old brackets and beams go; triplet brackets are written again around the
    # new lengths, and beams are left to MuseScore.
    for el in list(body):
        if el.tag in ("Tuplet", "endTuplet", "Beam"):
            body.remove(el)
    groups = triplet_groups(values)
    for (el, _, _), value in zip(timeline, values):
        for mode in el.findall("BeamMode"):
            el.remove(mode)
        _, number, dots = _parse_value(value)
        written = number * 2 // 3 if number % 3 == 0 else number
        if written not in _TYPES:
            raise FixError(f"cannot write {value!r}")
        # Where the length was written, so the new one lands in MuseScore's own order.
        olds = el.findall("dots") + el.findall("durationType")
        position = min(el.index(old) for old in olds) if olds else 0
        for old in olds:
            el.remove(old)
        head = etree.Element("durationType")
        head.text = _TYPES[written]
        el.insert(position, head)
        if dots:
            dot = etree.Element("dots")
            dot.text = str(dots)
            el.insert(position, dot)
    for first, last in groups:
        total = sum((value_length(v) for v in values[first:last + 1]), Fraction(0))
        base = total / 2
        if base.numerator != 1 or base.denominator not in _TYPES:
            raise FixError("a triplet group of that length cannot be written")
        tuplet = etree.Element("Tuplet")
        etree.SubElement(tuplet, "normalNotes").text = "2"
        etree.SubElement(tuplet, "actualNotes").text = "3"
        etree.SubElement(tuplet, "baseNote").text = _TYPES[base.denominator]
        number_el = etree.SubElement(tuplet, "Number")
        etree.SubElement(number_el, "style").text = "Tuplet"
        etree.SubElement(number_el, "text").text = "3"
        start_el = timeline[first][0]
        body.insert(body.index(start_el), tuplet)
        timeline[last][0].addnext(etree.Element("endTuplet"))
    for location, fractions in moves:
        _set_fractions(location, fractions)
    return f"set the lengths to {list(values)}" + _answered(_strike_words(measure, _RHYTHM_WORDS))


def _same_shape(timeline: List, to: List[Dict]) -> bool:
    """Whether `to` is the bar's notes and rests in the same order, chord for chord."""
    if len(timeline) != len(to):
        return False
    for (el, _, _), new in zip(timeline, to):
        if (el.tag == "Rest") != (_parse_value(new["value"])[0] == "rest"):
            return False
        if el.tag == "Chord" and len(el.findall("Note")) != len(new.get("pitches") or []):
            return False
    return True


def _set_pitches(chord: etree._Element, pitches: List[int], tpcs: List[int]) -> bool:
    """Give a chord's notes these pitches, low to high. True when one moved."""
    notes = sorted(chord.findall("Note"), key=lambda n: int(n.findtext("pitch") or 0))
    moved = False
    for note, (pitch, tpc) in zip(notes, sorted(zip(pitches, tpcs))):
        moved = moved or int(note.findtext("pitch") or 0) != pitch
        note.find("pitch").text = str(pitch)
        for tag in ("tpc", "tpc2"):
            node = note.find(tag)
            if node is not None:
                node.text = str(tpc)
        if note.find("tpc") is None:
            etree.SubElement(note, "tpc").text = str(tpc)
        for color in note.findall("color"):
            note.remove(color)
    return moved


def _tied_and_moving(timeline: List, to: List[Dict]) -> bool:
    for (el, _, _), new in zip(timeline, to):
        if el.tag != "Chord":
            continue
        notes = sorted(el.findall("Note"), key=lambda n: int(n.findtext("pitch") or 0))
        for note, pitch in zip(notes, sorted(new.get("pitches") or [])):
            if int(note.findtext("pitch") or 0) != pitch and \
                    note.find("Spanner[@type='Tie']") is not None:
                return True
    return False


def _write_moment(new: Dict) -> etree._Element:
    kind, number, dots = _parse_value(new["value"])
    written = number * 2 // 3 if number % 3 == 0 else number
    if written not in _TYPES:
        raise FixError(f"cannot write {new['value']!r}")
    el = etree.Element("Rest" if kind == "rest" else "Chord")
    if dots:
        etree.SubElement(el, "dots").text = str(dots)
    etree.SubElement(el, "durationType").text = _TYPES[written]
    if kind == "note":
        pitches, tpcs = new.get("pitches") or [], new.get("tpcs") or []
        if not pitches or len(tpcs) != len(pitches):
            raise FixError("a note needs its pitches and their spellings")
        for pitch, tpc in sorted(zip(pitches, tpcs)):
            note = etree.SubElement(el, "Note")
            etree.SubElement(note, "pitch").text = str(int(pitch))
            etree.SubElement(note, "tpc").text = str(int(tpc))
    return el


def _replace_bar(root: etree._Element, staff_id: int, measure_no: int,
                 expect: List[str], to: List[Dict]) -> str:
    """Put a whole bar of one voice as `to` says: lengths and pitches together."""
    measure = _measure(root, staff_id, measure_no)
    found = _bar_tokens(measure)
    if found != list(expect):
        raise FixError(f"bar reads {found} now, but the fix was recorded against {list(expect)}")
    if not to:
        raise FixError("the fix names no notes")
    values = [new["value"] for new in to]
    # Everything that can refuse does so before the bar is touched.
    written = [_write_moment(new) for new in to]
    groups = triplet_groups(values)
    body = measure.find("voice") if measure.find("voice") is not None else measure
    timeline = _timeline(body)
    total = sum((length for _, _, length in timeline), Fraction(0))
    new_total = sum((value_length(v) for v in values), Fraction(0))
    if new_total != total:
        raise FixError(f"the new bar adds up to {new_total} of a whole note, the bar "
                       f"to {total}")
    if _same_shape(timeline, to) and not _tied_and_moving(timeline, to):
        said = _rewrite_rhythm(root, staff_id, measure_no, expect, values)
        for (el, _, _), new in zip(timeline, to):
            if el.tag == "Chord":
                _set_pitches(el, new.get("pitches") or [], new.get("tpcs") or [])
        said = said.replace("set the lengths to", "set the bar to").split(" and took ")[0]
        return (f"{said} {_bar_tokens(measure)}"
                + _answered(_strike_words(measure, _BAR_WORDS)))

    from .rejected_bars import _bar_lengths, _cut_spanners_into  # noqa: PLC0415 - a cycle

    staff = measure.getparent()
    measures = staff.findall("Measure")
    lengths = _bar_lengths(staff)
    words = [chord.find("Lyrics") for chord, _, _ in timeline if chord.tag == "Chord"]
    words = [w for w in words if w is not None]
    first = timeline[0][0]
    at = list(body).index(first)
    gone = ("Chord", "Rest", "Tuplet", "endTuplet", "Beam", "Spanner")
    for el in list(body):
        if el.tag in gone:
            if list(body).index(el) < at:
                at -= 1
            body.remove(el)
    for offset, el in enumerate(written):
        body.insert(at + offset, el)
    for first_i, last_i in groups:
        length = sum((value_length(v) for v in values[first_i:last_i + 1]), Fraction(0))
        base = length / 2
        if base.numerator != 1 or base.denominator not in _TYPES:
            raise FixError("a triplet group of that length cannot be written")
        tuplet = etree.Element("Tuplet")
        etree.SubElement(tuplet, "normalNotes").text = "2"
        etree.SubElement(tuplet, "actualNotes").text = "3"
        etree.SubElement(tuplet, "baseNote").text = _TYPES[base.denominator]
        number_el = etree.SubElement(tuplet, "Number")
        etree.SubElement(number_el, "style").text = "Tuplet"
        etree.SubElement(number_el, "text").text = "3"
        written[first_i].addprevious(tuplet)
        written[last_i].addnext(etree.Element("endTuplet"))
    chords = [el for el in written if el.tag == "Chord"]
    for chord, lyric in zip(chords, words):
        chord.insert(list(chord).index(chord.find("Note")), lyric)
    _cut_spanners_into(measures, lengths, measures.index(measure))
    return (f"wrote the bar afresh as {_bar_tokens(measure)}"
            + _answered(_strike_words(measure, _BAR_WORDS)))


# Reading a bar back out, so a fix can be *picked* rather than typed. The indexing
# and the token grammar are this module's, and a caller that worked them out for
# itself would be a second implementation of both — which is exactly how a fix ends
# up naming the wrong chord.

# MuseScore's tpc read the other way: which letter, and how many sharps or flats.
_LETTERS = "FCGDAEB"
_LETTER_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ACCIDENTALS = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}


def note_name(pitch: int, tpc: Optional[int] = None) -> str:
    """A pitch as a person reads it — "D4", "Eb4" — using the score's own spelling.

    The spelling matters here in a way it does not to the sound: this is shown to
    someone comparing the bar against the printed page, and an E flat offered as a
    D sharp is a note they have to translate before they can agree with it. Without
    a tpc, fall back to sharps.
    """
    if tpc is None or not 1 <= tpc <= 35:
        letter = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"][pitch % 12]
        return f"{letter}{pitch // 12 - 1}"
    # 13..19 are the naturals F C G D A E B; every seven up is one sharp more.
    letter = _LETTERS[(tpc - 13) % 7]
    alter = (tpc - 13) // 7
    # The octave follows the letter, not the sound: B sharp 3 sounds like C4.
    octave = (pitch - _LETTER_SEMITONES[letter] - alter) // 12 - 1
    return f"{letter}{_ACCIDENTALS.get(alter, '')}{octave}"


def read_bar(root: etree._Element, staff_id: int, measure_no: int) -> List[Dict]:
    """One bar's chords, in the numbering a recorded fix uses. Rests are not chords.

    `index` is what an `undot` or `slur` entry means by `index`, and `token` is the
    same word that entry's `from` list would carry, so what is shown and what is
    recorded cannot drift apart. `starts_slur` says a slur already begins there,
    which is the one thing a caller has to check before offering to add another.

    Raises `FixError` for a staff or measure that is not there, so a caller asking
    about a bar out of range gets the same answer as a fix recorded against one.
    """
    out: List[Dict] = []
    for index, chord in enumerate(_chords(root, staff_id, measure_no)):
        notes = []
        for note in chord.findall("Note"):
            try:
                pitch = int((note.findtext("pitch") or "").strip())
            except ValueError:
                continue
            try:
                tpc = int((note.findtext("tpc") or "").strip())
            except ValueError:
                tpc = None
            notes.append(note_name(pitch, tpc))
        out.append({
            "index": index,
            "token": _token(chord),
            "name": "+".join(notes) if notes else "?",
            "starts_slur": any(sp.find(".//next") is not None
                               for sp in chord.findall(".//Spanner[@type='Slur']")),
        })
    return out


def _start_repeat(root: etree._Element, measure_no: int) -> str:
    """Put a start-repeat sign on bar ``measure_no`` of every staff."""
    staves = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None]
    if not staves:
        raise FixError("the score has no staves")
    bars = []
    for staff in staves:
        measures = staff.findall("Measure")
        if measure_no < 1 or measure_no > len(measures):
            raise FixError(f"staff {staff.get('id')} has no measure {measure_no}")
        bar = measures[measure_no - 1]
        if bar.find("startRepeat") is not None:
            raise FixError("a repeat already starts here")
        bars.append(bar)
    for bar in bars:
        # MuseScore writes it ahead of the bar's voices, after any `irregular`.
        at = 0
        for index, child in enumerate(bar):
            if child.tag in ("irregular", "breakMultiMeasureRest"):
                at = index + 1
        bar.insert(at, etree.Element("startRepeat"))
    return f"repeat starts here on all {len(bars)} staves"


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
        if kind == "repeat":
            measure = int(fix["measure"])
            try:
                what = _start_repeat(root, measure)
            except FixError as exc:
                raise FixError(f"m{measure} (repeat): {exc}") from None
            done.append(f"m{measure}: {what} — {fix.get('why', 'no reason recorded')}")
            continue
        staff, measure = int(fix["staff"]), int(fix["measure"])
        try:
            if kind == "append":
                # No chord index: a bar this fix repairs may have no chords at all yet.
                what = _append_bar(_measure(root, staff, measure), fix.get("from", []),
                                   fix.get("add", []), int(fix.get("drop", 0)))
            elif kind == "rhythm":
                what = _rewrite_rhythm(root, staff, measure, fix.get("from", []),
                                       fix.get("to", []))
            elif kind == "bar":
                what = _replace_bar(root, staff, measure, fix.get("from", []),
                                    fix.get("to", []))
            elif kind == "pitch":
                what = _set_pitch(root, staff, measure, int(fix["index"]),
                                  fix.get("from", []), int(fix["was"]), int(fix["to"]),
                                  int(fix["tpc"]))
            elif kind == "unmark":
                what = _unmark(root, staff, measure, str(fix.get("text", "")))
            elif kind == "slur" and "end_measure" in fix:
                what = _slur_across(root, staff, measure, int(fix.get("index", 0)),
                                    int(fix["end_measure"]), int(fix.get("end_index", 0)))
            elif kind in ("undot", "slur"):
                index = int(fix.get("index", 0))
                chords = _chords(root, staff, measure)
                if index < 0 or index >= len(chords):
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
