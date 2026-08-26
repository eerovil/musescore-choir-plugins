"""Widening the bars that scroll too fast, and only those.

Verovio spaces a measure by what is *in* it: a bar of sixteenths with lyrics
under every note comes out far wider per beat than a bar of two half notes. Since
the scroll follows the notes, the video then speeds up and slows down with the
engraving, and on this repertoire that reaches a 5x step between one bar and the
next.

Two numbers describe a bar: how wide verovio drew it, and how long it lasts.
Their ratio — width per quarter note — *is* the speed the video scrolls through
that bar, which is why the rule is written on the ratio and not on raw width: a
3/4 bar beside a 4/4 one is narrower and should be. What a singer notices is not
a wide bar but a **step** in that speed, so what this module caps is the ratio
between neighbours, and the job is to find the narrowest score that keeps it.

That has a closed form. The smallest widths-per-beat ``x_i >= natural_i`` with
``x_i / x_j <= cap`` for neighbours are ``x_i = max_j natural_j / cap**|i - j|``:
every bar is pulled up by whichever other bar reaches furthest, and the pull dies
away geometrically, so one dense bar widens the few bars around it and the far end
of the song not at all. Being the smallest bar by bar, it is also the smallest in
total — there is no shorter strip that satisfies the cap.

Reaching a target is the trick `add_rest_track.qml` already used inside MuseScore:
a hidden staff of rests, injected into the MusicXML after MuseScore has produced it
— so it never reaches the MIDI or the audio — and cropped back off the bottom of
the rendered strip. What is new here is that the number of rests is chosen **per
bar** rather than as one grid over the whole song, and that a score already inside
the cap gets no spacer staff at all.

How wide a bar goes for a given number of rests is not something to work out in
advance. Verovio gives each separate moment in a bar a minimum width, so rests
laid on moments the music already has change nothing, and past that point what one
more is worth falls away as the bar fills up — about a fifth as much in a bar of
32nds as in a bar of quarters. So this measures instead: engrave, read the bars
back, work out from that engraving what a rest was worth in each bar, solve again,
and stop when solving twice running gives the same answer. Three or four engravings
of a score that needs widening, one for a score that does not.

Two things were got wrong here and are worth not repeating. The rests have to be
written as **real note values** — verovio reads what a rest is written as and not
its `<duration>`, so a rest of "one fifth of a bar" is taken for a whole rest and
quietly drags the whole part out of time with the audio, with nothing wrong in the
picture to show it. And the targets are settled **before** any rest is written and
never moved again: re-solving the cap against the widths a plan produced looks like
the way to tidy away the last few percent of rounding, and instead it walks
outwards bar by bar and inflates the entire score.
"""

from __future__ import annotations

import copy
import math
import os
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, List, Optional, Sequence, Tuple

from lxml import etree

SPACER_ID = "P-SCROLLVIDEO-SPACER"
SPACER_FILE = "spaced.musicxml"

# The biggest step in width-per-beat allowed between one bar and the next.
# `timing.smooth_scroll` averages the scroll speed over a couple of seconds, so it
# absorbs a step of this size; what it cannot absorb is the 3x-5x a dense bar
# produces on its own.
DEFAULT_MAX_RATIO = 1.3

# What one more moment in a bar is worth, in verovio units at our engraving scale.
# Only the opening guess, and the search reaches the same answer from well either
# side of it — every step after the first is sized by what the engraving did.
SLOT_WIDTH = 250.0

# Engravings spent settling the plan, on top of the one that measured the score.
# A score with one dense bar among sparse ones is the slow case, because every bar
# in the taper answers differently; it settles in four. Running out is not a
# failure, only a plan that had one more correction in it.
MAX_PASSES = 5

SVG_NS = "http://www.w3.org/2000/svg"
_STAFF_LINE = re.compile(r"^M\s*([-\d.]+)\s+[-\d.]+\s+L\s*([-\d.]+)\s")


def _lcm(values) -> int:
    out = 1
    for value in values:
        out = out * value // math.gcd(out, value)
    return out


def _noop(_msg: str) -> None:
    pass


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def measure_widths(svg: str) -> List[float]:
    """How wide verovio drew each measure, in the engraving's own units.

    Read off the staff lines rather than the barlines: verovio draws a measure's
    staff lines from its left edge to its right one, so a measure's width is one
    subtraction and the first measure needs no special case for having no barline
    on its left.
    """
    root = etree.fromstring(svg.encode())
    widths = []
    for measure in root.iter(_tag("g")):
        if measure.get("class") != "measure":
            continue
        span = None
        for staff in measure.iter(_tag("g")):
            if staff.get("class") != "staff":
                continue
            for path in staff.findall(_tag("path")):
                line = _STAFF_LINE.match(path.get("d", "") or "")
                if line:
                    span = (float(line.group(1)), float(line.group(2)))
                    break
            break
        if span is None:
            return []
        widths.append(span[1] - span[0])
    return widths


def measure_durations(musicxml_path: str) -> Optional[List[Fraction]]:
    """How long each measure lasts, in quarter notes, or None if unreadable.

    In quarters rather than divisions because divisions are a per-part unit that
    can change mid-piece, and the cap is a statement about music, not about how
    finely this file happens to count.
    """
    root = etree.parse(musicxml_path).getroot()
    first = root.find("part")
    if first is None:
        return None
    divisions = None
    lengths = []
    for measure in first.findall("measure"):
        declared = measure.findtext("attributes/divisions")
        if declared:
            divisions = int(declared)
        if not divisions:
            return None
        lengths.append(Fraction(_measure_length(measure), divisions))
    return lengths or None


def _measure_length(measure: etree._Element) -> int:
    """Length of a MusicXML measure in divisions, by following its cursor.

    A second voice is written after the first with a `backup` element winding the
    cursor back, so adding up every note counts a two-voice bar twice and reports it
    as twice as long as it is. Every target computed from that would then be half
    what it should be. Chord notes share their moment and do not advance at all.
    """
    position = furthest = 0
    for child in measure:
        name = etree.QName(child).localname
        if name == "note" and child.find("chord") is None or name == "forward":
            position += int(child.findtext("duration") or 0)
            furthest = max(furthest, position)
        elif name == "backup":
            position -= int(child.findtext("duration") or 0)
    return furthest


def capped_targets(widths: Sequence[float], durations: Sequence[Fraction],
                   max_ratio: float) -> List[float]:
    """The narrowest widths-per-beat that keep every neighbouring step inside the cap.

    ``x_i = max_j widths_j / durations_j / max_ratio**|i - j|``, computed as one
    sweep each way rather than the quadratic maximum. Nothing is ever narrowed:
    a bar keeps its natural width unless a bar near it is wider per beat than the
    cap allows it to be.
    """
    per_beat = [w / float(d) if d else 0.0 for w, d in zip(widths, durations)]
    out = list(per_beat)
    for i in range(1, len(out)):
        out[i] = max(out[i], out[i - 1] / max_ratio)
    for i in range(len(out) - 2, -1, -1):
        out[i] = max(out[i], out[i + 1] / max_ratio)
    return out


def target_widths(widths: Sequence[float], durations: Sequence[Fraction],
                  max_ratio: float) -> List[float]:
    """How wide each bar has to be drawn for the scroll to keep the cap."""
    return [target * float(duration) for target, duration
            in zip(capped_targets(widths, durations, max_ratio), durations)]


def measure_onsets(musicxml_path: str) -> List[int]:
    """How many separate moments each measure already strikes something on.

    The spacer only widens a bar once it asks for more moments than the music
    already has: a quarter rest laid over a quarter note is the same moment, and
    verovio spaces it once. So this is where a bar's widening starts from, and
    without it the search would spend its first engraving learning that the first
    few slots did nothing.
    """
    root = etree.parse(musicxml_path).getroot()
    counts: List[set] = []
    for part in root.findall("part"):
        divisions = None
        for index, measure in enumerate(part.findall("measure")):
            declared = measure.findtext("attributes/divisions")
            if declared:
                divisions = int(declared)
            if index >= len(counts):
                counts.append(set())
            if not divisions:
                continue
            at = Fraction(0)
            for note in measure.findall("note"):
                if note.find("chord") is None:
                    counts[index].add(at)
                    at += Fraction(int(note.findtext("duration") or 0), divisions)
    return [max(1, len(moments)) for moments in counts]


@dataclass
class _Bar:
    """One bar, and what is known about how it answers being given more rests.

    `width` is what it measured at `slots` rests, and `slope` what the last rest
    added to it. Anchoring the model on the last thing measured rather than on a
    slope averaged all the way back from the natural width is what keeps the search
    from swinging: an extra rest is not worth a fixed amount, it is worth less and
    less as the bar fills up, so a slope taken over a long interval is a straight
    line drawn through a curve and the next plan misses in whichever direction it
    was drawn.
    """

    natural: float          # what verovio drew before any rest was added
    onsets: int             # moments the music already has; rests below this add nothing
    slots: int
    width: float
    slope: float

    def at(self, count: int) -> float:
        """What this bar would measure with `count` rests, as best we know."""
        return max(self.natural, self.width + self.slope * (count - self.slots))

    def reach(self, want: float) -> int:
        """The fewest rests it would take to reach `want`.

        Read off the model around the last engraving, so it can ask for fewer as
        readily as for more: an early guess that overshot is walked back rather than
        kept, which is what makes the answer the narrowest one and not merely one
        that works.
        """
        if want <= self.natural + 1e-6 or self.slope <= 0:
            return self.onsets
        needed = self.slots + math.ceil((want - self.width) / self.slope - 1e-9)
        return max(self.onsets, needed)

    def seen(self, count: int, width: float) -> None:
        """Record an engraving, and what the rests between then and now were worth."""
        if count > self.slots and width > self.width:
            self.slope = (width - self.width) / (count - self.slots)
        if count <= self.onsets:
            self.natural = width       # no rest of ours widened it; this is its own width
        self.slots, self.width = count, width


def solve_plan(bars: Sequence[_Bar], wanted: Sequence[float]) -> List[int]:
    """The fewest rests each bar needs to reach its target, as best we know.

    The targets are settled before any rest is written and never move again. That is
    deliberate. Re-solving the cap against the widths the last plan produced looks
    like the obvious refinement — it would take the leftover of quantising away —
    and it runs away instead: a bar that lands a few percent past its target makes
    its neighbour want a few percent more, that one overshoots too, and the
    requirement walks outwards and never settles. Measured on a score with one dense
    bar it inflated the total strip by a third and was still growing.

    So the leftover stays. It is worth less than one rest per bar, which is a few
    percent of the bar's width here, and it shows up as a step a few percent past
    the cap rather than as a song-wide grid.
    """
    return [bar.reach(want) for bar, want in zip(bars, wanted)]


# What a rest of this many quarter notes is called. Verovio reads a note's written
# value, not its `<duration>`, so a slot has to be something a scribe could write:
# a rest of "one fifth of a bar" is read as a whole rest and drags every part after
# it out of time with the audio.
_VALUES = {
    Fraction(8): "breve", Fraction(4): "whole", Fraction(2): "half",
    Fraction(1): "quarter", Fraction(1, 2): "eighth", Fraction(1, 4): "16th",
    Fraction(1, 8): "32nd", Fraction(1, 16): "64th", Fraction(1, 32): "128th",
}


def _written(length: Fraction) -> Optional[Tuple[str, int]]:
    """What `length` quarter notes is written as: a value name and 0 or 1 dots."""
    if length in _VALUES:
        return _VALUES[length], 0
    plain = length * 2 / 3
    if plain in _VALUES:
        return _VALUES[plain], 1
    return None


def slot_durations(length: Fraction, count: int) -> List[Fraction]:
    """`count` written rests filling a bar of `length` quarters, as even as it gets.

    A bar is first written out in as few rests as it takes, and then the longest of
    them is halved over and over until there are enough. Halving a written value
    always gives another one — half a dotted half is a dotted quarter — so this
    reaches almost any count without ever writing a rest that cannot be read, and
    the result uses at most two lengths.

    It can come up short, and both ways matter. A bar cannot be cut past a 128th
    rest, so a very fine count comes back with fewer than asked — harmless, the
    engraving is measured afterwards either way. But a bar whose length is not a
    written value at all (an OCR-damaged bar summing to, say, seven thirds of a
    quarter) cannot be filled *exactly*, and filling it approximately would leave
    the spacer part a different length from the music and put every bar after it
    out of time with the audio. That comes back empty, and the caller writes the
    bar as one whole-measure rest instead — which adds nothing to its width, which
    is the right answer for a bar nobody can say the length of.
    """
    parts: List[Fraction] = []
    left = length
    for value in sorted(set(_VALUES) | {v * 3 / 2 for v in _VALUES}, reverse=True):
        while left >= value:
            parts.append(value)
            left -= value
        if not left:
            break
    if left:
        return []
    while len(parts) < count:
        longest = max(parts)
        half = longest / 2
        if _written(half) is None:
            break
        index = 0
        # Split every rest of the current longest value before moving on to a
        # shorter one, so the extra onsets spread through the bar in generations
        # rather than piling into its first beat.
        while index < len(parts) and len(parts) < count:
            if parts[index] == longest:
                parts[index:index + 1] = [half, half]
                index += 2
            else:
                index += 1
    return parts


def add_spacer_staff(musicxml_path: str, out_path: str,
                     slots: Sequence[int]) -> Optional[str]:
    """Write `musicxml_path` to `out_path` with a rest-only spacer part appended.

    `slots` is one count per measure of the first part. Returns out_path, or None
    when no spacer could be built (in which case the caller should engrave the
    original unchanged).
    """
    tree = etree.parse(musicxml_path)
    root = tree.getroot()
    first = root.find("part")
    part_list = root.find("part-list")
    if first is None or part_list is None:
        return None
    sources = first.findall("measure")
    if len(sources) != len(slots):
        return None

    durations = measure_durations(musicxml_path)
    if durations is None or len(durations) != len(slots):
        return None

    score_part = etree.SubElement(part_list, "score-part")
    score_part.set("id", SPACER_ID)
    # Everything about this staff that can be told not to print is: it is cropped
    # off the bottom of the strip, but its name, clef and time signature are drawn
    # in the left margin where the crop cannot reach them.
    part_name = etree.SubElement(score_part, "part-name")
    part_name.set("print-object", "no")
    part_name.text = "Spacer"

    part = etree.SubElement(root, "part")
    part.set("id", SPACER_ID)
    divisions = None
    for index, (source, length, count) in enumerate(zip(sources, durations, slots)):
        measure = etree.SubElement(part, "measure")
        if source.get("number"):
            measure.set("number", source.get("number"))
        rests = slot_durations(length, count) if length else []
        # A MusicXML part owns its divisions; this one counts in whatever unit makes
        # every rest it wrote a whole number — or, for a bar it could not write out,
        # the bar's own length.
        want = _lcm(rest.denominator for rest in rests) if rests else (
            length.denominator if length else None)
        attributes = None
        if want and want != divisions:
            attributes = etree.SubElement(measure, "attributes")
            etree.SubElement(attributes, "divisions").text = str(want)
            divisions = want
        if index == 0:
            if attributes is None:
                attributes = etree.SubElement(measure, "attributes")
            time = source.find(".//time")
            if time is not None:
                hidden = copy.deepcopy(time)
                hidden.set("print-object", "no")
                attributes.append(hidden)
            clef = etree.SubElement(attributes, "clef")
            clef.set("print-object", "no")
            etree.SubElement(clef, "sign").text = "percussion"
        if not rests:
            if length:
                # Nothing written could fill this bar exactly. A whole-measure rest
                # always can, needs no written value, and widens nothing.
                note = etree.SubElement(measure, "note")
                etree.SubElement(note, "rest").set("measure", "yes")
                etree.SubElement(note, "duration").text = str(int(length * divisions))
                etree.SubElement(note, "voice").text = "1"
            continue
        for rest in rests:
            written = _written(rest)
            if written is None:
                return None
            note = etree.SubElement(measure, "note")
            etree.SubElement(note, "rest")
            etree.SubElement(note, "duration").text = str(int(rest * divisions))
            etree.SubElement(note, "voice").text = "1"
            etree.SubElement(note, "type").text = written[0]
            for _ in range(written[1]):
                etree.SubElement(note, "dot")

    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path


def even_engraving(musicxml_path: str, tmp_dir: str, engrave: Callable,
                   *, max_ratio: float = DEFAULT_MAX_RATIO,
                   log: Callable[[str], None] = _noop) -> Tuple[object, bool]:
    """Engrave `musicxml_path` with as little added spacing as the cap allows.

    Returns the engraving and whether a spacer staff is on the page (and so has to
    be cropped off the bottom). A score already inside the cap comes back engraved
    exactly as verovio drew it: no spacer staff, no added width, and the same page
    it would have had before any of this existed.

    Otherwise it alternates between arithmetic and engraving. `solve_plan` works out
    a whole plan from what a rest is currently believed to be worth; engraving it
    says what a rest was really worth; the plan is solved again with that in hand.
    The first round guesses, the second knows, and it stops as soon as solving twice
    running gives the same answer.
    """
    engraving = engrave(musicxml_path)
    if not max_ratio or max_ratio <= 1.0:
        return engraving, False

    durations = measure_durations(musicxml_path)
    natural = measure_widths(engraving.svg)
    if durations is None or len(natural) != len(durations):
        log("Could not measure the engraved bars; engraving as it came out")
        return engraving, False

    wanted = target_widths(natural, durations, max_ratio)
    lurching = sum(1 for want, have in zip(wanted, natural) if want > have + 1e-6)
    if not lurching:
        return engraving, False
    log(f"{lurching} of {len(natural)} bars scroll too fast for their neighbours")

    bars = [_Bar(natural=width, onsets=count, slots=count, width=width,
                 slope=SLOT_WIDTH)
            for width, count in zip(natural, measure_onsets(musicxml_path))]
    slots = solve_plan(bars, wanted)
    best = None
    for _attempt in range(MAX_PASSES):
        # A bar that needs nothing is written as one bar-length rest rather than as
        # the moments it already has: fewer glyphs for verovio to place, same width.
        written = [count if count > bar.onsets else 1
                   for bar, count in zip(bars, slots)]
        path = add_spacer_staff(musicxml_path, os.path.join(tmp_dir, SPACER_FILE),
                                written)
        if path is None:
            log("Could not build the spacer staff; engraving as it came out")
            return engrave(musicxml_path), False
        engraving = engrave(path)
        widths = measure_widths(engraving.svg)
        if len(widths) != len(bars):
            break
        if all(have >= want - 1e-6 for have, want in zip(widths, wanted)):
            if best is None or sum(widths) < best[0]:
                best = (sum(widths), engraving)
        for bar, count, width in zip(bars, slots, widths):
            bar.seen(count, width)
        plan = solve_plan(bars, wanted)
        if plan == slots:
            break
        slots = plan
    if best is not None:
        return best[1], True
    missed = sum(1 for want, have in zip(wanted, widths) if want > have + 1e-6)
    log(f"{missed} bars are as wide as the rest staff can make them")
    return engraving, True


def visible_height(layout) -> float:
    """Page height in verovio units with the spacer staff (the bottom one) cut off.

    Cut just above the spacer's top staff line. The last singing staff's lyrics sit
    in the gap above that line, so the margin has to be small — only enough to clear
    the line's own stroke. A generous margin eats the lyrics instead.
    """
    if len(layout.staff_tops) < 2:
        return layout.height
    spacer_top = layout.staff_tops[-1]
    spacing = next((g.staff_spacing for g in layout.notes.values()
                    if g.staff_top == layout.staff_tops[-2]), None)
    margin = 0.15 * spacing if spacing else 0.0
    return max(1.0, min(layout.height, spacer_top - margin))
