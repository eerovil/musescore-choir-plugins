"""Read a score one printed system at a time, and put the systems back together.

The whole-page route asks homr a question it cannot answer. Given a page, homr
assembles a part by taking staff index N out of *every* system it found, which
assumes the score is a rectangle -- the same staves, in the same order, in every
system. Choral engraving is not a rectangle: a part that rests through a system
is simply not printed, so a page really can be 2-3-2-3-3 staves. When the counts
disagree homr first deletes an edge system and then, failing that, breaks every
group into singletons, which is how B5 -- four vocal staves -- came out of a
whole-page scan as one monophonic line of 70 bars (issue #105).

Given **one system**, there is nothing to reconcile. The assumption becomes
vacuous rather than wrong. Measured on B5's three systems, cropped:

    system 1   4 staves found, grouped [4]   -> 4 x "Voice"
    system 2   4 staves found, grouped [2]   -> 2 x "Piano", two staves each
    system 3   4 staves found, grouped [3]   -> "Voice", "Piano", "Voice"

Four staves' worth of notes every time. The grouping moves around and the part
names are fiction -- homr says "Voice" and "Piano" and means neither -- but the
notes of a fused part carry ``<staff>1</staff>`` / ``<staff>2</staff>``, so
nothing is lost. **Grand-staff fusion is a labelling detail.** Flatten every
part into its staves and the grouping stops mattering, which is why this module
ignores ``part-name`` entirely.

So the division of labour is: **homr reports, the app assembles.** What homr is
asked for is "the staves of this band, in order". What comes back out of here is
one score, systems in order, one part per staff column.

**Which voice is absent from a short system is not decided here**, because it is
not recoverable from pixels -- you need the words, the range, or the piece. The
columns are filled from the top and the empty rows are measure rests; naming
them is ``clean_score``'s ``--per-system`` grid's job, and that grid already asks
a person, which is the only reliable answer.

Issue #195 measured what that refusal costs and what it does not. Scored before
the grid is answered, the four corpus pages whose systems print different staff
counts read 28.3% against a per-system 93.5%, and 592 of the 791 faults are
`size` -- but that is the intermediate being read as if it were the product.
Answered from the reviewed score's own per-band grouping the same four pages read
**77.2%**, and every point still missing is on the two pages where a crop read
one bar more than the page prints; up to that bar assembly costs nothing. A
better guess here would also not help: of the 9 narrow systems on those pages
only 2 are a voice resting, and 7 are divisi printed apart in one system and
together in the rest, where the narrow system's first staff carries two of the
page's rows and no single row is the right answer. Those percentages were scored
by the comparator issue #196 has since replaced and were not re-scored -- **the
gap between them included**. Both sides come off one rule, which makes 28.3%
against 77.2% an honest measurement under that rule; it does not make the gap
invariant under the new one, since a different ranking can move the two sides by
different amounts. The conclusion stands as plausible and unverified until
someone re-scores both with issue #196's rule.

**Bounds are a precondition.** This module is given the printed systems; it does
not look for them. Detecting them from the image was measured and abandoned in
issue #80 (staff-line detection died at half a degree of skew, and grouping by
bracket agreed with the score twice in nine songs), so they come from
``.systems.json`` -- an AI reading ``pdf_systems.page_images(grid=True)`` and a
person correcting the bands in the Systems viewer.

**Twenty seams instead of five.** Each crop is its own document: bar numbering
restarts at 1, ``divisions`` is whatever that run chose, and key and time are
re-declared. :func:`assemble` owns all of it -- one ``divisions`` for the whole
score, continuous bar numbers, a re-declaration dropped when it says what was
already true, and a system break written at each seam so the per-system grid
sees the same systems the page has.
"""

from __future__ import annotations

from fractions import Fraction

import copy
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from . import omr
from .pdf_systems import SystemBounds, SystemImage, crop_systems

Logger = Callable[[str], None]

#: What a crop is rasterised at before homr sees it, and it is **not** the 300
#: dpi the whole-page benchmark used.
#:
#: A crop is a much shorter image than the page it came from, and homr's staff
#: detection turns out to care. Rasterised at 300 dpi, B4's first system -- two
#: staves, plainly -- came back as one, and so did its fifth; at 200 dpi both
#: come back with the staves the page prints, and so does every other system of
#: all seven benchmark pages. Nothing else about the run changed. So this is a
#: measured default rather than a preference, and it is worth re-measuring
#: rather than nudging if a page ever comes back short of staves.
SCAN_DPI = int(os.getenv("OMR_SCAN_DPI", "200"))

#: The measure length assumed when a system declares no time signature at all.
_FALLBACK_TIME = (4, 4)

#: The largest numerator a bar's own length is allowed to talk us into. A bar
#: measured at 40 quarters is a parse that has gone wrong, not a 40/4 bar, and
#: the declared signature is the better answer there.
_MAX_BEATS = 32

#: How many **bars** a span needs before its own length is allowed to overrule a
#: declared numerator. One bar agreeing with itself is not evidence -- a voice
#: short of a note would rewrite the meter around its own mistake, which is the
#: failure this whole correction exists to avoid. Counting staves instead would
#: not do: two staves of one bar are one reading of one bar, so a shared misread
#: duration would reach the threshold on its own, and every one-bar meter change
#: the page prints (B4's last system goes 3/4, 5/4, 4/4) would be up for
#: rewriting by the bar it governs.
_MIN_BARS = 2


def _noop(_msg: str) -> None:
    pass


class ScanError(RuntimeError):
    """A system could not be read, or the systems could not be assembled."""


@dataclass
class Staff:
    """One staff of one printed system: its bars, already single-staff.

    A "part" as homr emits it may hold two of these. What survives from it is
    the notes and the barlines; the name does not, because it was never real.
    """

    measures: List[etree._Element]
    divisions: int
    clef: Optional[etree._Element] = None
    key: Optional[etree._Element] = None
    time: Optional[etree._Element] = None

    @property
    def bars(self) -> int:
        return len(self.measures)


@dataclass
class _Placed:
    """A note and the beat homr's own cursor put it on, in that part's divisions."""

    onset: int
    note: etree._Element


@dataclass
class SystemScan:
    """What came back for one printed system."""

    index: int                      # 1-based, the band's own index
    musicxml: str
    staves: List[Staff] = field(default_factory=list)
    #: What `omr.split_measure_rests` moved while reading this system. Carried
    #: back so the caller can write it down; a repair nobody is told about is
    #: worse than no repair (see `scan._read_one`).
    moved_rests: List["omr.MovedRest"] = field(default_factory=list)

    @property
    def width(self) -> int:
        return len(self.staves)

    @property
    def bars(self) -> int:
        return max((s.bars for s in self.staves), default=0)


# --- reading -------------------------------------------------------------


def read_systems(
    pdf_path: str,
    bounds: Sequence[SystemBounds],
    out_dir: str,
    log: Logger = _noop,
    dpi: int = SCAN_DPI,
    queue: bool = True,
    engine: Optional[omr.Engine] = None,
) -> List[SystemScan]:
    """Crop each printed system and read it, in order.

    **One heavy slot per system, not one per song.** :mod:`omr` already made the
    page the unit of the lease; a system is the same argument taken one step
    further, and if anything better: the hold is ~20s rather than ~30s, so a
    render or a test suite waiting behind it waits less, and an interruption
    costs the band in flight rather than the page. The systems already read are
    on disk. ``queue=False`` is for a caller that is holding a lease itself.

    Nothing is retried and nothing is skipped: a band homr cannot read raises,
    because a score silently missing one of its systems is worse than a scan
    that stopped and said so.
    """
    if not bounds:
        raise ScanError(
            "No printed systems to read. Bounds come from .systems.json — set them "
            "in the Systems viewer (or with pdf_systems.save_bounds) before scanning."
        )

    images = crop_systems(pdf_path, list(bounds), out_dir, dpi=dpi)
    scans: List[SystemScan] = []
    for n, image in enumerate(images, start=1):
        log(f"System {image.index} of {len(images)}: reading")
        scans.append(read_system(image, out_dir, log=log, queue=queue, engine=engine))
        log(f"System {image.index}: {scans[-1].width} staves, {scans[-1].bars} bars")
    return scans


def read_system(
    image: SystemImage,
    out_dir: str,
    log: Logger = _noop,
    queue: bool = True,
    engine: Optional[omr.Engine] = None,
    bar_length: Optional[Fraction] = None,
) -> SystemScan:
    """Read one cropped system and flatten what comes back into staves.

    ``engine`` picks which homr reads it (:func:`omr.engines`); the installed
    one otherwise.
    """
    moved: List[omr.MovedRest] = []
    produced = omr.read_page(
        image.path,
        out_dir=out_dir,
        log=log,
        label=f"song app homr system {image.index}",
        queue=queue,
        engine=engine,
        repairs=moved,
        bar_length=bar_length,
    )
    staves = flatten(produced)
    if not staves:
        raise ScanError(f"System {image.index} came back with no staves ({produced}).")
    return SystemScan(index=image.index, musicxml=produced, staves=staves,
                      moved_rests=moved)


# --- flattening ----------------------------------------------------------


def flatten(musicxml_path: str) -> List[Staff]:
    """The staves of one system's MusicXML, in reading order.

    Parts are walked in document order and each is split on the ``<staff>`` its
    notes carry, so a two-staff "Piano" contributes two staves exactly where a
    pair of "Voice" parts would have. The name is never read.
    """
    root = etree.parse(musicxml_path).getroot()
    staves: List[Staff] = []
    for part in root.findall("part"):
        staves.extend(flatten_part(part))
    return staves


def flatten_part(part: etree._Element) -> List[Staff]:
    """The staves one ``<part>`` holds, top to bottom.

    A fused grand staff contributes two here exactly where a pair of separate
    parts would contribute one each, which is what makes the grouping homr
    chose stop mattering.
    """
    return [_extract_staff(part, number) for number in _staff_numbers(part)]


def _staff_numbers(part: etree._Element) -> List[int]:
    """Which staves this part actually holds.

    ``<staves>`` is what the part *declares*; the ``<staff>`` on its notes is
    what it *has*, and the two disagree often enough in an OMR parse that both
    are consulted. An empty staff is still a staff: it is a line on the page and
    the grid has to be able to point at it.
    """
    declared = 0
    for attrs in part.iter("attributes"):
        text = attrs.findtext("staves")
        if text and text.strip().isdigit():
            declared = max(declared, int(text.strip()))
    used = {int(n.text.strip()) for n in part.iter("staff")
            if n.text and n.text.strip().isdigit()}
    return sorted(set(range(1, declared + 1)) | used) or [1]


def _extract_staff(part: etree._Element, number: int) -> Staff:
    """One staff of a part, rebuilt as a part of its own.

    ``<backup>`` and ``<forward>`` are not kept as they stand -- they move the
    cursor between staves as well as between voices, and this staff is about to
    lose the other staves -- but **what they say about onsets is**. Each bar is
    read by following the cursor homr wrote (a note advances it, a ``backup``
    winds it back, a ``forward`` moves it on), which gives every note the beat
    homr put it on; the staff is then written back out voice by voice with the
    steps that put each note back at that beat.

    That is the whole of issue #172. The rebuild used to start every voice again
    at the head of the bar, so a voice homr placed later in the bar slid to beat
    one -- measured over 61 systems in issue #166, it cost 18.8 points of
    note accuracy, and this is the largest single loss that map found.

    **The voices are renumbered once for the staff, not once per bar**, and that
    is issue #187. See :func:`_voice_numbering`.
    """
    measures: List[etree._Element] = []
    divisions = 0
    clef = key = time = None
    per_bar: List[List[_Placed]] = []
    tails: List[List[etree._Element]] = []
    ends: List[int] = []

    for source in part.findall("measure"):
        measure = etree.Element("measure", number=source.get("number") or "")
        notes: List[_Placed] = []
        trailing: List[etree._Element] = []
        at = 0            # where homr's cursor stands
        onset = 0         # where the note being read starts
        for child in source:
            tag = child.tag
            if tag == "attributes":
                attrs = _staff_attributes(child, number)
                divisions = divisions or _int(attrs.findtext("divisions"))
                clef = clef if clef is not None else attrs.find("clef")
                key = key if key is not None else attrs.find("key")
                time = time if time is not None else attrs.find("time")
                if len(attrs):
                    measure.append(attrs)
            elif tag == "note":
                # A note with <chord/> sounds with the one before it rather than
                # after it: it neither advances the cursor nor takes an onset of
                # its own.
                if child.find("chord") is None:
                    onset = at
                    at += _duration(child)
                if _staff_of(child) == number:
                    notes.append(_Placed(onset, copy.deepcopy(child)))
            elif tag == "backup":
                at = max(0, at - _int(child.findtext("duration")))
            elif tag == "forward":
                at += _int(child.findtext("duration"))
            elif tag == "print":
                continue
            elif tag in ("direction", "harmony", "figured-bass"):
                if _staff_of(child) == number:
                    kept = copy.deepcopy(child)
                    _drop(kept, "staff")
                    measure.append(kept)
            elif tag == "barline":
                # A barline belongs after the music it closes, and the notes
                # have not been written yet.
                trailing.append(copy.deepcopy(child))
            else:
                measure.append(copy.deepcopy(child))

        per_bar.append(notes)
        tails.append(trailing)
        # Where this staff's music ends, read off its own notes: another staff of
        # the part running long must not stretch this one.
        ends.append(max((p.onset + _duration(p.note) for p in notes), default=0))
        measures.append(measure)

    numbering = _voice_numbering(per_bar)
    for measure, notes, trailing, end in zip(measures, per_bar, tails, ends):
        voiced = _voiced(notes, numbering)
        for element in voiced:
            measure.append(element)
        # MuseScore puts a barline where the cursor stands, not at the bar's end.
        # A voice that stops early -- its last rest printed once for both voices,
        # so written into the other -- would otherwise close the bar part-way
        # through, and the music after it reads as a bar of its own (#354).
        short = end - _cursor(voiced)
        if trailing and short > 0:
            move = etree.SubElement(measure, "forward")
            etree.SubElement(move, "duration").text = str(short)
        for element in trailing:
            measure.append(element)

    return Staff(measures=measures, divisions=divisions or 1,
                 clef=clef, key=key, time=time)


def _voice_numbering(per_bar: Sequence[Sequence["_Placed"]]) -> Dict[str, int]:
    """What each of homr's voice labels is called on the way out, staff-wide.

    Renumbering from 1 is necessary -- a voice number means nothing outside its
    part and this staff is becoming one, so a note saying ``<voice>5</voice>``
    would arrive claiming to be the fifth voice of a part that has one. But it
    has to be **one decision for the staff**, and issue #187 is what it cost to
    make it once per bar.

    Two ways a per-bar decision moved a singer. The order the notes are written
    in is homr's interleaving rather than a fact about the music, so a bar homr
    happened to write lower-voice-first swapped the two; and a bar where only
    one of the two voices sings compacted whichever singer that was down to
    voice 1. Both put every note at the right pitch on the right beat in the
    wrong part, which no health check sees -- both voices are well-formed and
    the bar adds up -- and which a singer meets as somebody else's line in their
    practice track.

    **The order is homr's own numbering** (:func:`_voice_key`), and not which
    voice sounds highest. That is a deliberate refusal to make a claim: this
    function's whole job is to stop the assembler *scrambling* what homr said,
    and re-sorting by pitch would be a second, different claim -- that voice 1
    is the upper line -- which is false wherever two voices cross.

    Re-measured for issue #192 on the engine this host runs (`main @ 6c3bbf4`,
    issue #173's 63 bands re-read), sorting by pitch scores 67.7% and 84 voice
    faults against 68.2% and 67, and the only page it changes at all is Herää
    Suomi p3, where the two basses cross on the page and it swaps a column homr
    had numbered right -- 95.8% and 3 voice faults down to 85.6% and 20.
    **Those figures were scored by the comparator issue #196 has since replaced**
    -- it ranked a staff's voices by which was seen first, which one invented and
    never-scored moment can reverse (see :func:`assemble`) -- and they were not
    re-derived, so read the p3 swap as large rather than as exactly 10.2 points.
    What does not move with them is the reason for the refusal -- where two
    voices cross, "voice 1 is the upper line" is false of the page itself, so it
    is not a rule this function may adopt whatever the corpus digits settle at.
    Issue #196 reached that same conclusion from the harness's side, on its own
    settled figures: it refused mean height as a *comparator* rank because on
    `heraa-suomi-final-s10` the crossing basses leave the reference's own two
    voices 0.1 of a step apart, and shipped a bar count instead.

    Homr's numbering is also the better claim on the merits where the two
    disagree: over those 63 parses it puts the higher-sounding voice first in
    341 of the 357 bars carrying two, against 316 for the order the notes
    happen to be written in.
    """
    labels = {(placed.note.findtext("voice") or "1").strip()
              for notes in per_bar for placed in notes}
    return {label: n for n, label in enumerate(sorted(labels, key=_voice_key), start=1)}


def _voiced(notes: List["_Placed"], numbering: Dict[str, int]) -> List[etree._Element]:
    """Notes regrouped voice by voice, each put back where homr had it.

    ``numbering`` says what each of homr's voice labels is called on the way
    out; it is decided once for the whole staff, by :func:`_voice_numbering`.

    The steps between them are **arithmetic on the onsets read out of homr's own
    cursor**, not an assumption about where a voice begins: a ``backup`` where
    the next note sounds earlier than the cursor stands, a ``forward`` where it
    sounds later. Two voices homr started together still start together, because
    their first notes have the same onset -- but two voices homr wrote one after
    the other stay one after the other, which is what this used to lose.
    """
    groups: Dict[str, List[_Placed]] = {}
    for placed in notes:
        voice = (placed.note.findtext("voice") or "1").strip()
        groups.setdefault(voice, []).append(placed)

    out: List[etree._Element] = []
    at = 0
    for voice, group in sorted(groups.items(), key=lambda item: _voice_key(item[0])):
        n = numbering.get(voice, 1)
        for placed in group:
            step = placed.onset - at
            # A chord note follows its leader immediately and shares its onset,
            # so stepping to that onset would wind back over the leader itself.
            stacked = placed.note.find("chord") is not None and bool(out) \
                and out[-1].tag == "note"
            if step and not stacked:
                move = etree.Element("backup" if step < 0 else "forward")
                etree.SubElement(move, "duration").text = str(abs(step))
                out.append(move)
                at = placed.onset
            note = placed.note
            _set(note, "voice", str(n))
            _drop(note, "staff")
            # `_duration` is 0 for a chord or grace note, so the cursor advances
            # only for a note that really takes time -- the same rule the reading
            # side applies, and it has to stay the same rule: a cursor that moved
            # across a chord would think it stood a whole note further on than
            # the XML does, and back the *next* note up to somewhere earlier than
            # homr put it.
            at += _duration(note)
            out.append(note)
    return out


def _cursor(elements: Sequence[etree._Element]) -> int:
    """Where the cursor stands after ``elements``, by the same rule they were read by."""
    at = 0
    for element in elements:
        if element.tag == "note":
            at += _duration(element)
        elif element.tag == "backup":
            at -= _int(element.findtext("duration"))
        elif element.tag == "forward":
            at += _int(element.findtext("duration"))
    return at


def _voice_key(voice: str):
    """Order two of homr's voice labels the way MusicXML numbering means them.

    A number sorts as a number, so ``10`` follows ``2`` rather than ``1``;
    anything that is not a number sorts after the numbers by its text, so a
    label nobody anticipated still lands in the same place in every bar. Being
    the *same* order in every bar is the whole property -- which voice is
    called 1 matters far less than that it is called 1 throughout.

    Measured over the 63 band parses issue #173 cached: homr's own numbering
    puts the higher-sounding voice first in 295 of the 312 bars that carry two,
    against 270 for the order the notes are written in, and the written order
    disagrees with the bar before it 25 times.
    """
    return (0, int(voice), "") if voice.isdigit() else (1, 0, voice)


def _staff_attributes(attributes: etree._Element, number: int) -> etree._Element:
    """The part of an ``<attributes>`` that belongs to one staff.

    ``<staves>`` goes (there is one now), and anything numbered for another
    staff goes with it. What survives loses its number, for the same reason the
    voices are renumbered.
    """
    out = etree.Element("attributes")
    for child in attributes:
        if child.tag == "staves":
            continue
        which = child.get("number")
        if which is not None and which.strip().isdigit() and int(which) != number:
            continue
        kept = copy.deepcopy(child)
        if kept.get("number") is not None:
            del kept.attrib["number"]
        out.append(kept)
    return out


# --- assembling ----------------------------------------------------------


def assemble(scans: Sequence[SystemScan], out_path: str,
             log: Optional[Logger] = None) -> str:
    """Write the systems out as one score, one part per staff column.

    Columns are filled **from the top**: a system of two staves puts them in
    columns 1 and 2 and leaves the rest resting. That is not a claim about which
    voice is missing -- it is a refusal to make one. The per-system grid asks a
    person, per system and per staff, and top-alignment is the shape it expects.

    **So this file is a positional intermediate and reading it as the product
    gives a wrong number** (#195). On the four corpus pages whose systems print
    different staff counts it scores 28.3% against a per-system 93.5%; answered
    the way an operator answers it, the same pages score 77.2%, and what is left
    is two pages where a crop read one bar more than the page prints rather than
    anything about rows. Guessing the row instead would not close it: 7 of those
    pages' 9 narrow systems are divisi printed apart in one system and together in
    the rest, so the narrow system's first staff carries two of the page's rows
    and there is no single row to put it on. Both percentages were scored by the
    comparator issue #196 has since replaced and were not re-scored, **and so was
    the gap between them**. One comparator reading two files is a fair comparison
    under that comparator and not a guarantee that a different ranking would move
    the two sides by the same amount, so the gap is a historical measurement that
    nobody has checked against issue #196's rule.

    **Repeat signs and volta brackets belong to the whole system** (#312), so one
    staff reading one is written on every staff of it -- see
    :func:`_system_barlines`. ``log`` hears about each copy that was needed.

    Three seams are closed here, all of them consequences of each crop being its
    own document. ``divisions`` is unified across the score and every duration
    scaled to it; bars are numbered continuously rather than restarting at 1 in
    every system; and a key or time signature is written only where it says
    something that was not already true. A ``<print new-system="yes"/>`` marks
    each seam, so ``clean_score``'s per-system mode cuts the score where the page
    is cut.

    The **meter is written here rather than taken from the crop** -- see
    :func:`_meter_plan`. homr cannot read a numerator (it has no token for one)
    and infers it from how long its decoded bars came out, one crop at a time;
    this is where both the bars either side of a seam and every staff at once are
    visible, so it is the only place the inference can be corrected.

    **A column's voices are not renumbered again here, and that is issue #187's
    other half.** A staff's voices are settled once, when it is flattened
    (:func:`_voice_numbering`), so the flip this card was opened for -- the same
    singer changing voice from bar to bar and, across a join, from system to
    system -- is gone before assembly sees it. What assembly could still be
    asked to do is reconcile *homr's* numbering between two crops, and it is
    deliberately not asked to. Each crop is read on its own, so nothing ties one
    crop's "voice 1" to the next crop's. Sounding height is the only evidence
    available at that point, and it is not sufficient: on Herää Suomi p3 the two
    basses cross and change places on the page, so a rule ranking by height
    would swap a column homr had right. Under the issue #141 rule a crop really
    read with the voices the other way up is homr's to fix, since it is the
    parse disagreeing with the page rather than us disagreeing with the parse.

    **Herää Suomi p1 was cited here as an example of that and is not one.**
    Issues #192 and #194 recorded homr numbering that page's two upper voices
    one way round in systems 1 and 3 and the other way round in 2 and 4, at 30
    voice faults; issue #190 retracted it. Each of the four crops agrees with
    its own reference -- 0, 0, 1 and 0 voice faults, system 4 note-for-note
    perfect -- homr numbers voice 1 the higher line in all four, and the bars
    the page-level score faults are correct on both sides. The 30 belong to
    `fixturecheck.compare._voice_rank`, which ranks a staff's voices by which is
    seen first: the first moment of staff 1 there is one note homr wrote a
    quarter early into voice 2, in a moment the reference does not have and the
    comparison never scores, and that one moment reverses the ranking for the
    whole page. Issue #196 fixed it (`eerovil/homr#37`, merged 2026-09-06): the
    harness now ranks a staff's voices by which is the higher line in more of its
    bars, and on that rule p1 stands at 70.9% with 1 voice fault and the corpus
    totals are re-scored. What is real there is bar 1's early entry, which is a
    rhythm misread and the fork's.

    None of that weakens issue #187's refusal to sort this assembler by height;
    it sharpens it. The *harness* needs a whole-staff rule, because it has to line
    one file's voices up against another's and first-seen is not a property of the
    music -- and it refused mean height too, for the same crossing-voices reason.
    The assembler must not rank by height at all, for the p3 reason above.
    """
    if not scans:
        raise ScanError("Nothing to assemble: no systems were read.")

    width = max(scan.width for scan in scans)
    divisions = _common_divisions(scans)
    meters = _meter_plan(scans, divisions)

    score = etree.Element("score-partwise", version="4.0")
    part_list = etree.SubElement(score, "part-list")
    for column in range(width):
        score_part = etree.SubElement(part_list, "score-part", id=f"P{column + 1}")
        # The name is positional on purpose. Nothing downstream should read a
        # voice out of it: which staff is which part is the grid's question.
        etree.SubElement(score_part, "part-name").text = f"Staff {column + 1}"

    barlines = [_system_barlines(scan, log) for scan in scans]
    for column in range(width):
        part = etree.SubElement(score, "part", id=f"P{column + 1}")
        _fill_column(part, scans, column, divisions, meters, barlines)
    _join_slurs(score, scans)

    tree = etree.ElementTree(score)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tree.write(out_path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return out_path


def _fill_column(
    part: etree._Element,
    scans: Sequence[SystemScan],
    column: int,
    divisions: int,
    meters: Sequence[Sequence["_BarMeter"]],
    barlines: Optional[Sequence[Dict[int, Dict[str, etree._Element]]]] = None,
) -> None:
    number = 0
    prevailing_key: Optional[str] = None
    prevailing_clef: Optional[str] = None
    first = True
    # The notes sounding at the end of the previous system, and its key there.
    ending: List[_Sounding] = []
    ending_fifths = 0

    for system, scan in enumerate(scans):
        staff = scan.staves[column] if column < scan.width else None
        fifths = _fifths(staff.key) if staff is not None and staff.key is not None else ending_fifths

        for bar in range(scan.bars):
            number += 1
            meter = meters[system][bar]
            source = staff.measures[bar] if staff and bar < staff.bars else None
            if source is None:
                measure = _rest_measure(number, meter.ticks)
            else:
                # The key in force at this bar, for the line-break check below: a
                # system may change key part-way, and its last bar is in the new one.
                declared_key = source.find("attributes/key")
                if declared_key is not None:
                    fifths = _fifths(declared_key)
                measure = _scaled(source, staff.divisions, divisions)
                measure.set("number", str(number))

            if system and bar == 0:
                measure.insert(0, etree.Element("print", {"new-system": "yes"}))

            if bar == 0:
                wanted = _signature(scan, staff)
                _merge_attributes(measure, _needed_attributes(
                    first, divisions, wanted, meter,
                    (prevailing_key, prevailing_clef),
                ))
                prevailing_key, prevailing_clef = wanted
            else:
                _drop_global(measure)
                # The crop's own numerator is never kept, wherever it stands: it
                # was inferred from bars this document could see and the plan has
                # read the same bars with the seam in view.
                _drop_time(measure)
                if meter.declare:
                    _declare_time(measure, meter.force)
                declared = measure.find("attributes")
                if declared is not None:
                    prevailing_key = _canonical(declared.find("key")) or prevailing_key
                    prevailing_clef = _canonical(declared.find("clef")) or prevailing_clef
            if barlines is not None:
                _set_barlines(measure, barlines[system].get(bar, {}))
            if system and bar == 0 and source is not None:
                _mark_lost_accidental(measure, ending, ending_fifths, fifths)
            first = False
            part.append(measure)
            if bar == scan.bars - 1:
                ending = _sounding(measure)[1] if source is not None else []
                ending_fifths = fifths


# --- repeats ------------------------------------------------------------

#: What a barline says that is about the music rather than its drawing.
_FORM = ("repeat", "ending")
_BARLINE_ORDER = {"bar-style": 0, "ending": 2, "repeat": 3}


def _system_barlines(
    scan: SystemScan, log: Optional[Logger] = None,
) -> Dict[int, Dict[str, etree._Element]]:
    """The repeat and volta barlines of one system, bar by bar, read off every staff.

    A repeat sign and a volta bracket are drawn across the whole system, so one
    staff reading one is enough evidence for all of them -- and the converter
    needs all of them: MuseScore 3 keeps a start repeat only when every part
    carries it (#312: Kantajani bar 27, read on two staves of four, came out of
    the conversion with no repeat at all, and with it copied onto the other two
    it was kept). homr reads a sign that opens a system on some staves and not
    others often enough that this is the common case, not a corner.

    What is collected is the union, per bar and per side: each ``<repeat>`` and
    ``<ending>`` any staff wrote there, once. The barline's style is left to
    whichever staff drew it, since a style is a drawing and not a form.
    """
    found: Dict[int, Dict[str, etree._Element]] = {}
    seen: Dict[Tuple[int, str], int] = {}
    for staff in scan.staves:
        for bar, measure in enumerate(staff.measures):
            for barline in measure.findall("barline"):
                side = barline.get("location") or "right"
                marks = [child for child in barline if child.tag in _FORM]
                if not marks:
                    continue
                seen[(bar, side)] = seen.get((bar, side), 0) + 1
                union = found.setdefault(bar, {}).setdefault(
                    side, etree.Element("barline", location=side))
                for mark in marks:
                    if not any(_same(mark, kept) for kept in union):
                        union.append(copy.deepcopy(mark))
    if log is not None:
        for (bar, side), count in sorted(seen.items()):
            if count < scan.width:
                what = ", ".join(_describe(mark) for mark in found[bar][side])
                log(f"System {scan.index} bar {bar + 1}: {what} read on {count} of "
                    f"{scan.width} staves, written on all.")
    return found


def _same(a: etree._Element, b: etree._Element) -> bool:
    return a.tag == b.tag and dict(a.attrib) == dict(b.attrib)


def _describe(mark: etree._Element) -> str:
    if mark.tag == "repeat":
        return "start repeat" if mark.get("direction") == "forward" else "end repeat"
    return f"volta {mark.get('number') or ''} {mark.get('type') or ''}".strip()


def _set_barlines(measure: etree._Element, wanted: Dict[str, etree._Element]) -> None:
    """Give a bar every repeat and volta its system has there, each where it belongs.

    A left barline opens the bar: it goes after any ``print`` and ``attributes``
    and before the first note, which is where a score writes it. Flattening puts
    every barline after the notes, which is right for the right-hand one only.
    """
    for side, union in wanted.items():
        barline = next((b for b in measure.findall("barline")
                        if (b.get("location") or "right") == side), None)
        if barline is None:
            barline = etree.Element("barline", location=side)
            measure.append(barline)
        for mark in union:
            if not any(_same(mark, kept) for kept in barline if kept.tag in _FORM):
                barline.append(copy.deepcopy(mark))
        # MusicXML's order: the drawing first, then the volta, then the repeat.
        barline[:] = sorted(barline, key=lambda child: _BARLINE_ORDER.get(child.tag, 1))
    for barline in measure.findall("barline"):
        if barline.get("location") != "left":
            continue
        measure.remove(barline)
        at = 0
        for index, child in enumerate(measure):
            if child.tag in ("print", "attributes"):
                at = index + 1
            else:
                break
        measure.insert(at, barline)

#: How many bars at a system's end may hold the start of a slur that runs into
#: the next system: homr keeps a loose start there (``EDGE_BARS`` in the fork's
#: ``slur_resolution``), and the two must agree.
EDGE_BARS = 2


@dataclass
class _LooseEnd:
    note: etree._Element
    slur: etree._Element
    voice: str


def _join_slurs(score: etree._Element, scans: Sequence[SystemScan]) -> None:
    """Join the two halves of a slur the page carries over a system break.

    Each system is read on its own, so a slur crossing a break comes back as a
    start in the last bar of one crop with no stop, and a stop in the first bar
    of the next with no start: homr keeps exactly those loose ends and drops
    every other (eerovil/musescore-choir-plugins#318). Here, where both systems
    are in view, they are paired and given a slur number no other slur in those
    two bars uses, so the stream stays unambiguous.

    **Paired only within one column of two systems that print the same number
    of staves.** Where the count changes, column N is not the same printed staff
    on both sides of the break -- Illan viimeinen tango's lower staff of system 6
    is staff 3 of system 7 -- and a slur from one part into another cannot be
    written. Within a column the halves pair in order when the two sides hold
    the same number of halves, and otherwise voice to voice, a start taking the
    first stop in its voice.

    **A tie over the break comes back the same way**, because homr's model
    reads ties and slurs as one kind of arc and only turns a pair into a tie
    once it sees both ends. Measured on the six songs of #274, 22 of the 30
    pairs joined here were a held note tied over the line, so a pair from the
    system's last note to the next system's first note at the same pitch is
    written as a tie (the whole written pitch: Shakkitarina's B natural slurred
    to B flat over the break is a slur).

    **A half without a partner is never dropped silently** on a guess that it
    was a tie: the same pitch across the break may be another staff's or another
    voice's note, and a slur that disappears unmarked is the failure #318 exists
    to stop. The one quiet case is a half on a note that already carries a tie
    the same way, which is the same arc written twice.

    Any other half without a partner is taken out and its note marked red under
    `⚠ slur?`, the way homr marks its own doubts: either the page carries a slur
    across the break that the other side lost, or homr read a slur that is not
    there, and only a person looking at the page can say which.
    """
    parts = score.findall("part")
    starts_at: List[int] = []
    at = 0
    for scan in scans:
        starts_at.append(at)
        at += scan.bars
    for column, part in enumerate(parts):
        _join_column(part, scans, column, starts_at)


def _join_column(part, scans, column, starts_at) -> None:
    measures = part.findall("measure")
    loose_starts: Dict[int, List[_LooseEnd]] = {}
    loose_stops: Dict[int, List[_LooseEnd]] = {}
    for system, first in enumerate(starts_at):
        block = measures[first:first + scans[system].bars]
        open_: Dict[str, List[_LooseEnd]] = {}
        # Stops that closed nothing, past the first bar: each is the other half
        # of a pair inside one bar that flattening put out of document order (a
        # slur from one voice into the other), and its start is no half of
        # anything over the break.
        orphans: List[Tuple[etree._Element, str]] = []
        for index, measure in enumerate(block):
            for note in measure.findall("note"):
                for slur in note.findall("notations/slur"):
                    number = slur.get("number", "1")
                    end = _LooseEnd(note, slur, note.findtext("voice") or "1")
                    if slur.get("type") == "start":
                        open_.setdefault(number, []).append(end)
                    elif slur.get("type") == "stop":
                        if open_.get(number):
                            open_[number].pop()
                        elif index == 0:
                            loose_stops.setdefault(system, []).append(end)
                        else:
                            orphans.append((measure, number))
        # Each orphaned stop sets aside only its own partner: the first start
        # left open in the same bar with the same number. Any other start in
        # that bar is still a half to join, or to mark.
        set_aside = set()
        for measure, number in orphans:
            partner = next((end for end in open_.get(number, [])
                            if end.note.getparent() is measure
                            and id(end) not in set_aside), None)
            if partner is not None:
                set_aside.add(id(partner))
        # Only the edge bars: homr keeps a loose start only in the system's last
        # EDGE_BARS bars (a slur may cross one barline before the edge).
        edge = {id(m) for m in block[-EDGE_BARS:]}
        loose_starts[system] = [end for ends in open_.values() for end in ends
                                if id(end.note.getparent()) in edge
                                and id(end) not in set_aside]

    for system in range(-1, len(scans)):
        starts = loose_starts.get(system, [])
        # The system's last bar: only its last note can start a tie over the
        # break. A start a bar earlier is a slur, whatever its pitch.
        closing = (measures[starts_at[system] + scans[system].bars - 1]
                   if 0 <= system < len(scans) else None)

        def ends_system(note: etree._Element) -> bool:
            return note.getparent() is closing and _on_edge(note, last=True)

        stops = loose_stops.get(system + 1, [])
        same_shape = (0 <= system < len(scans) - 1 and column < scans[system].width
                      and scans[system].width == scans[system + 1].width)
        pairs: List[Tuple[_LooseEnd, _LooseEnd]] = []
        if same_shape and starts and stops:
            starts = sorted(starts, key=_reading_order)
            stops = sorted(stops, key=_reading_order)
            taken = set()
            # A half on the system's last note and one on the next system's
            # first note at the same written pitch are a held note tied over
            # the line; pair those first, so a slur beside the tie does not
            # take its stop.
            for start in starts:
                if not ends_system(start.note):
                    continue
                match = [stop for stop in stops if id(stop) not in taken
                         and _on_edge(stop.note, last=False)
                         and _written_pitch(stop.note) == _written_pitch(start.note)]
                if match:
                    taken.update((id(start), id(match[0])))
                    pairs.append((start, match[0]))
            rest = [start for start in starts if id(start) not in taken]
            left = [stop for stop in stops if id(stop) not in taken]
            if len(rest) == len(left):
                pairs += list(zip(rest, left))
            else:
                for start in rest:
                    # The first stop in the voice, as homr's own pairing and
                    # MuseScore's would close it.
                    match = [stop for stop in left
                             if stop.voice == start.voice and id(stop) not in taken]
                    if match:
                        taken.add(id(match[0]))
                        pairs.append((start, match[0]))
        paired = {id(end) for pair in pairs for end in pair}
        tied_over: List[Tuple[_LooseEnd, _LooseEnd]] = []
        for start, stop in pairs:
            if (ends_system(start.note) and _on_edge(stop.note, last=False)
                    and _written_pitch(start.note) == _written_pitch(stop.note)):
                _remove_slur(start)
                _remove_slur(stop)
                _tie(start.note, "start")
                _tie(stop.note, "stop")
                tied_over.append((start, stop))
                continue
            _number_pair(start.slur, stop.slur)
        for end in starts:
            if id(end) in paired:
                continue
            # A slur and a tie both running over the line to one note come back
            # as two starts and one stop: homr writes one arc mark per note.
            # The slur ends where the tie does -- written, and marked, since
            # the stop was inferred rather than read.
            shared = next((stop for start, stop in tied_over
                           if start.voice == end.voice
                           and _reading_order(end) < _reading_order(start)), None)
            if shared is not None:
                notations = shared.note.find("notations")
                stop_slur = etree.SubElement(notations, "slur", type="stop")
                _number_pair(end.slur, stop_slur)
                _mark_note(end.note, "slur?")
                continue
            _drop_loose_end(end, already_tied=_has_tie(end.note, "start"))
        for end in stops:
            if id(end) not in paired:
                _drop_loose_end(end, already_tied=_has_tie(end.note, "stop"))


def _number_pair(start: etree._Element, stop: etree._Element) -> None:
    """Give a joined slur a number no other slur in the bars it spans uses.

    Every bar from the start's through the stop's, not just those two: a start
    may stand a bar before the system's last, and a slur in that last bar
    sharing the number would close it or overlap it."""
    first = start.getparent().getparent().getparent()
    last = stop.getparent().getparent().getparent()
    part = first.getparent()
    bars = list(part.findall("measure"))
    span = bars[bars.index(first):bars.index(last) + 1]
    used = {slur.get("number") for measure in span for slur in measure.iter("slur")
            if slur is not start and slur is not stop}
    number = next(str(n) for n in range(1, 17) if str(n) not in used)
    start.set("number", number)
    stop.set("number", number)


def _reading_order(end: "_LooseEnd") -> Tuple[int, int]:
    """Where a half stands in its part: the bar, then the note within it."""
    measure = end.note.getparent()
    part = measure.getparent()
    return list(part).index(measure), list(measure).index(end.note)


def _written_pitch(note: etree._Element) -> Tuple[str, str, int]:
    """Step, octave and alteration: a slur from B natural to B flat is a slur."""
    return (*_step_octave(note), _int(note.findtext("pitch/alter")))


def _step_octave(note: etree._Element) -> Tuple[str, str]:
    return note.findtext("pitch/step") or "", note.findtext("pitch/octave") or ""


def _edge_notes(measure: etree._Element, last: bool) -> List[etree._Element]:
    """The pitched notes that end (or open) each voice of the bar."""
    per_voice: Dict[str, List[etree._Element]] = {}
    for note in measure.findall("note"):
        if note.find("pitch") is None or note.find("grace") is not None:
            continue
        group = per_voice.setdefault(note.findtext("voice") or "1", [])
        if note.find("chord") is not None and group:
            group[-1].append(note)
        else:
            group.append([note])
    picked = [groups[-1 if last else 0] for groups in per_voice.values() if groups]
    return [note for chord in picked for note in chord]


def _on_edge(note: etree._Element, last: bool) -> bool:
    return any(note is other for other in _edge_notes(note.getparent(), last))


#: Where <tie> goes among a note's children (MusicXML's own order).
_BEFORE_TIE = {"grace", "cue", "chord", "pitch", "unpitched", "rest", "duration"}


def _tie(note: etree._Element, kind: str) -> None:
    index = 0
    for i, child in enumerate(note):
        if isinstance(child.tag, str) and child.tag in _BEFORE_TIE:
            index = i + 1
    note.insert(index, etree.Element("tie", type=kind))
    notations = note.find("notations")
    if notations is None:
        notations = etree.SubElement(note, "notations")
    etree.SubElement(notations, "tied", type=kind)


def _remove_slur(end: _LooseEnd) -> None:
    notations = end.slur.getparent()
    notations.remove(end.slur)
    if len(notations) == 0:
        end.note.remove(notations)


def _has_tie(note: etree._Element, kind: str) -> bool:
    return any(tie.get("type") == kind for tie in note.findall("tie"))


def _drop_loose_end(end: _LooseEnd, already_tied: bool = False) -> None:
    """Take out a half with no partner, marking its note -- unless the note
    already carries a tie the same way, which is the same arc said twice."""
    _remove_slur(end)
    if not already_tied:
        _mark_note(end.note, "slur?")


def _mark_note(note: etree._Element, word: str) -> None:
    """The note goes red and `⚠ <word>` stands above it, as homr marks a doubt."""
    measure = note.getparent()
    _colour_red(note)
    children = list(measure)
    at = children.index(note)
    while at > 0 and children[at].find("chord") is not None:
        at -= 1
    direction = etree.Element("direction", placement="above")
    words = etree.SubElement(etree.SubElement(direction, "direction-type"), "words")
    words.set("color", "#FF0000")
    words.text = f"⚠ {word}"
    measure.insert(at, direction)


#: Order the key signature adds accidentals in: sharps from the left, flats from
#: the right.
_SHARP_ORDER = "FCGDAEB"


@dataclass
class _Sounding:
    step: str
    octave: str
    alter: int
    note: Optional[etree._Element] = field(default=None, compare=False)


def _key_alter(step: str, fifths: int) -> int:
    if fifths > 0 and step in _SHARP_ORDER[:fifths]:
        return 1
    if fifths < 0 and step in _SHARP_ORDER[::-1][:-fifths]:
        return -1
    return 0


def _fifths(key: etree._Element) -> int:
    return _int(key.findtext("fifths"))


def _sounding(measure: etree._Element) -> Tuple[List[_Sounding], List[_Sounding]]:
    """The pitched notes that open the bar and the ones still sounding at its end."""
    cursor = last = 0
    placed: List[Tuple[int, int, _Sounding]] = []
    for element in measure:
        if element.tag == "backup":
            cursor -= _duration(element)
        elif element.tag == "forward":
            cursor += _duration(element)
        elif element.tag == "note":
            duration = 0 if element.find("grace") is not None else _duration(element)
            if element.find("chord") is None:
                last, cursor = cursor, cursor + duration
            pitch = element.find("pitch")
            if pitch is not None:
                placed.append((last, last + duration, _Sounding(
                    pitch.findtext("step") or "", pitch.findtext("octave") or "",
                    _int(pitch.findtext("alter")), element)))
    end = max((stop for _, stop, _ in placed), default=0)
    return ([note for start, _, note in placed if start == 0],
            [note for _, stop, note in placed if stop == end])


#: Where <notehead> goes among a note's children (MusicXML's own order).
_BEFORE_NOTEHEAD = {
    "grace", "cue", "chord", "pitch", "unpitched", "rest", "tie", "duration",
    "instrument", "footnote", "level", "voice", "type", "dot", "accidental",
    "time-modification", "stem",
}


def _colour_red(note: etree._Element) -> None:
    note.set("color", "#FF0000")
    head = note.find("notehead")
    if head is None:
        head = etree.Element("notehead")
        head.text = "normal"
        index = 0
        for i, child in enumerate(note):
            if isinstance(child.tag, str) and child.tag in _BEFORE_NOTEHEAD:
                index = i + 1
        note.insert(index, head)
    head.set("color", "#FF0000")


def _mark_lost_accidental(
    measure: etree._Element, ending: Sequence[_Sounding], before: int, after: int
) -> None:
    """Mark a system's first bar where a note carried over the line break may have
    lost the accidental it was printed with.

    A note tied over a line break keeps its accidental and the page does not print
    it again. Each system is read on its own, so homr gives the continuation the
    key's pitch: on Shakkitarina system 3 (eerovil/musescore-choir-plugins#274)
    three staves end system 2 on a printed E♮ tied over, and system 3 opens on an
    E♭. homr drops the tie at the page edge, so nothing here can tell such a note
    from a new bar that really starts on the key's E♭ -- this only puts the red
    check-against-the-page mark on the bar, the way homr marks its own doubts,
    and changes no note.
    """
    for opening in _sounding(measure)[0]:
        for held in ending:
            if (held.step, held.octave) != (opening.step, opening.octave):
                continue
            if held.alter == _key_alter(held.step, before):
                continue  # nothing printed beside it to carry
            if opening.alter == held.alter or opening.alter != _key_alter(opening.step, after):
                continue
            direction = etree.Element("direction", placement="above")
            words = etree.SubElement(etree.SubElement(direction, "direction-type"), "words")
            words.set("color", "#FF0000")
            words.text = "⚠ accidental?"
            at = next((i for i, el in enumerate(measure)
                       if el.tag not in ("print", "attributes")), len(measure))
            if opening.note is not None:
                # The note itself goes red and the word stands above it, the way
                # homr marks its own doubts.
                _colour_red(opening.note)
                children = list(measure)
                at = children.index(opening.note)
                while at > 0 and children[at].find("chord") is not None:
                    at -= 1
            measure.insert(at, direction)
            return


def _signature(scan: SystemScan, staff: Optional[Staff]):
    """What this column's bar should be declaring: its own, else the system's.

    A resting column has no signature of its own, and inheriting the system's
    is the only honest answer -- an empty staff is silent, not in another key.
    The meter is not asked for here: it belongs to the score rather than to a
    column, and :func:`_meter_plan` has already decided it for every bar.
    """
    donor = staff
    if donor is None or (donor.key is None and donor.time is None and donor.clef is None):
        donor = scan.staves[0] if scan.staves else None
    key = _canonical(donor.key) if donor is not None else None
    clef = _canonical(staff.clef) if staff is not None and staff.clef is not None else None
    return key, clef


def _needed_attributes(first: bool, divisions: int, wanted, meter: "_BarMeter", prevailing):
    """An ``<attributes>`` holding only what has changed, or ``None``.

    The first bar of a part always gets one -- ``divisions`` has to be declared
    somewhere and a part with no clef is unreadable. After that a re-declaration
    that repeats what is already in force is dropped, which is most of them:
    every crop re-declares its key because every crop is a document that has
    just begun. The meter comes from the plan, which has already answered the
    same question for the whole score at once.
    """
    key, clef = wanted
    was_key, was_clef = prevailing
    attrs = etree.Element("attributes")
    if first:
        etree.SubElement(attrs, "divisions").text = str(divisions)
    if key is not None and (first or key != was_key):
        attrs.append(etree.fromstring(key))
    if first or meter.declare:
        attrs.append(etree.fromstring(meter.force.xml()))
    if clef is not None and (first or clef != was_clef):
        attrs.append(etree.fromstring(clef))
    if first and attrs.find("clef") is None:
        attrs.append(etree.fromstring("<clef><sign>G</sign><line>2</line></clef>"))
    if first and attrs.find("key") is None:
        attrs.insert(1, etree.fromstring("<key><fifths>0</fifths></key>"))
    return attrs if len(attrs) else None


def _merge_attributes(measure: etree._Element, attrs: Optional[etree._Element]) -> None:
    """Replace the bar's opening declarations with the ones it should carry.

    The crop's own are always taken out, whether or not anything replaces them:
    they say "this document begins here", and it does not any more. Anything
    else the bar declared -- a transposition, staff details -- is kept, because
    nothing about the seam makes it untrue.
    """
    keep: List[etree._Element] = []
    for existing in measure.findall("attributes"):
        for child in existing:
            if child.tag not in ("divisions", "key", "time", "clef", "staves"):
                keep.append(copy.deepcopy(child))
        measure.remove(existing)
    if keep:
        attrs = attrs if attrs is not None else etree.Element("attributes")
        for child in keep:
            attrs.append(child)
    if attrs is None:
        return
    index = 1 if len(measure) and measure[0].tag == "print" else 0
    measure.insert(index, attrs)


def _drop_global(measure: etree._Element) -> None:
    """Take ``divisions`` off a bar in the middle of a system.

    There is one for the whole score now, declared in the first bar of the
    part. Everything else the bar declares is a change the page prints.
    """
    for attributes in measure.findall("attributes"):
        for child in list(attributes):
            if child.tag in ("divisions", "staves"):
                attributes.remove(child)
        if not len(attributes):
            measure.remove(attributes)


def _rest_measure(number: int, ticks: int) -> etree._Element:
    measure = etree.Element("measure", number=str(number))
    note = etree.SubElement(measure, "note")
    etree.SubElement(note, "rest", measure="yes")
    etree.SubElement(note, "duration").text = str(ticks)
    etree.SubElement(note, "voice").text = "1"
    return measure


# --- the meter ------------------------------------------------------------


@dataclass(frozen=True)
class _Meter:
    beats: int
    beat_type: int

    def ticks(self, divisions: int) -> int:
        return max(1, round(divisions * 4 * self.beats / self.beat_type))

    def xml(self) -> str:
        return (f"<time><beats>{self.beats}</beats>"
                f"<beat-type>{self.beat_type}</beat-type></time>")


@dataclass
class _BarMeter:
    """What one bar of the assembled score is in, and whether it says so."""

    force: _Meter
    declare: bool
    ticks: int


def _meter_plan(
    scans: Sequence[SystemScan], divisions: int
) -> List[List[_BarMeter]]:
    """The meter in force at every bar, and where it is written down.

    **homr cannot read a numerator.** Its vocabulary holds only
    ``timeSignature/<denominator>``; the number of beats does not exist in what
    the model can emit and is inferred afterwards from how long the decoded bars
    came out (issue #174). So the denominator is a reading and the numerator is a
    guess -- and it is a guess made one crop at a time, with no sight of the bars
    before the crop began. A crop holding 2/4, 2/4, 4/4, 4/4 has no median that is
    right about any of it.

    Here both are visible: the bars either side of a seam, and every staff of a
    system at once. So the numerator is taken from **the length of the bars the
    signature governs**, keeping the denominator homr actually read, and a seam
    that measures the same as the meter already in force carries it rather than
    restating a fresh guess.

    Two things it deliberately will not do, because the point of the correction
    is a score that still says where it is wrong:

    * It needs :data:`_MIN_BARS` **bars** -- not staff copies of one bar -- and a
      strict majority among them before it overrules a declared numerator. A
      single voice short of a note cannot rewrite the meter around its own
      mistake: it stays a bar that contradicts the signature, which is what the
      health check reports. And a bar whose staves do not agree on how long it is
      is no observation at all, so a span made of such bars keeps what it was
      given.
    * A whole-measure rest is not a sample. homr writes one a whole note long
      whatever the meter, so counting it would drag every span towards 4/4.
    """
    plan: List[List[Optional[_BarMeter]]] = [
        [None] * scan.bars for scan in scans
    ]
    points = [
        (system, bar, _declared_time(scan, bar))
        for system, scan in enumerate(scans)
        for bar in range(scan.bars)
        if bar == 0 or _declared_time(scan, bar) is not None
    ]

    running: Optional[_Meter] = None
    for n, (system, bar, declared) in enumerate(points):
        following = points[n + 1] if n + 1 < len(points) else None
        stop = (following[1] if following and following[0] == system
                else scans[system].bars)
        lengths = [_bar_length_agreed(scans[system], over, divisions)
                   for over in range(bar, stop)]
        meter = _reconcile(declared, running,
                           [length for length in lengths if length], divisions,
                           seam=bar == 0 and system > 0
                           and _time_is_guessed(scans[system], bar))
        ticks = meter.ticks(divisions)
        plan[system][bar] = _BarMeter(meter, running is None or meter != running, ticks)
        for over in range(bar + 1, stop):
            plan[system][over] = _BarMeter(meter, False, ticks)
        running = meter

    fallback = _Meter(*_FALLBACK_TIME)
    return [
        [bar or _BarMeter(fallback, False, fallback.ticks(divisions)) for bar in system]
        for system in plan
    ]


def _reconcile(
    declared: Optional[_Meter],
    running: Optional[_Meter],
    lengths: Sequence[int],
    divisions: int,
    seam: bool = False,
) -> _Meter:
    """The meter a span is really in: its own length, in homr's denominator.

    ``lengths`` is one entry per **bar** of the span, not one per staff.

    At a ``seam`` -- the head of a system after the first whose signature homr
    guessed rather than read (:func:`_time_is_guessed`) -- a span measuring
    exactly the meter already in force carries that meter. homr guesses in
    quarters: Vieläkö huvittaisi carries 2/2 into three systems and each came
    back 4/4, which the score then wrote as a meter change at every seam
    (eerovil/musescore-choir-plugins#274). A signature homr read off the page is
    a change even at the same length -- the same song's 4/4 at bar 26 -- and a
    span of a different length is a change whatever was declared.
    """
    if seam and running is not None and _agreed(lengths) == running.ticks(divisions):
        return running
    beat_type = (declared or running or _Meter(*_FALLBACK_TIME)).beat_type
    # A signature that restates the meter already in force carries no numerator
    # of its own -- homr writes one at the head of every crop and again wherever
    # its own decoding wobbled, and "the same as before" is not a reading of the
    # page. One bar may overrule that. Anything else -- a change, or the score's
    # opening declaration, where there is nothing already in force -- needs
    # _MIN_BARS, so a one-bar meter change the page really prints survives being
    # measured against the single bar it governs.
    least = 1 if declared is not None and declared == running else _MIN_BARS
    measured = _agreed(lengths, least)
    if measured is not None:
        beats = measured * beat_type / (divisions * 4)
        if beats == int(beats) and 1 <= int(beats) <= _MAX_BEATS:
            return _Meter(int(beats), beat_type)
    return declared or running or _Meter(*_FALLBACK_TIME)


def _agreed(values: Sequence[int], least: int = 1) -> Optional[int]:
    """The value a strict majority agrees on, if there are enough of them."""
    if len(values) < least:
        return None
    counts: Dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    value, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return value if count * 2 > len(values) else None


def _time_is_guessed(scan: SystemScan, bar: int) -> bool:
    """Whether every signature this bar declares is homr's own fallback.

    homr writes a signature it decoded off the page into the attributes it opens
    with the clef and key, and one it had to make up -- a crop that prints none --
    into the first attributes of the measure, beside ``<divisions>``. A bar with
    no signature at all has nothing guessed about it.
    """
    found = False
    for staff in scan.staves:
        if bar >= staff.bars:
            continue
        for attributes in staff.measures[bar].findall("attributes"):
            if attributes.find("time") is None:
                continue
            found = True
            if attributes.find("divisions") is None or attributes.find("clef") is not None:
                return False
    return found


def _declared_time(scan: SystemScan, bar: int) -> Optional[_Meter]:
    """The signature this system's bar declares, if any staff declares one."""
    for staff in scan.staves:
        if bar >= staff.bars:
            continue
        for attributes in staff.measures[bar].findall("attributes"):
            time = attributes.find("time")
            if time is None:
                continue
            beats = _int(time.findtext("beats"))
            beat_type = _int(time.findtext("beat-type"))
            if beats and beat_type:
                return _Meter(beats, beat_type)
    return None


def _bar_length_agreed(scan: SystemScan, bar: int, divisions: int) -> Optional[int]:
    """How long **this bar** is, in the score's divisions: one answer, or none.

    The staves of one bar are copies of one reading, so they are reduced to a
    single observation here rather than counted separately -- otherwise a
    two-staff system would reach the confidence threshold within one bar, and a
    duration misread the same way on both staves would carry it.
    """
    lengths: List[int] = []
    for staff in scan.staves:
        if bar >= staff.bars:
            continue
        length = _bar_length(staff.measures[bar])
        if length:
            lengths.append(max(1, round(length * divisions / max(1, staff.divisions))))
    # No majority means the staves disagree about how long the bar is, and a bar
    # nobody agrees on says nothing about the meter it is in.
    return _agreed(lengths)


def _bar_length(measure: etree._Element) -> Optional[int]:
    """How far the music in one bar reaches, following the cursor.

    ``None`` when the bar says nothing about its own length: an empty bar, or one
    holding only a whole-measure rest, which homr writes a whole note long
    whatever the meter is.
    """
    at = 0
    reached = 0
    for child in measure:
        if child.tag == "note":
            if child.find("chord") is None:
                at += _duration(child)
            rest = child.find("rest")
            if rest is None or rest.get("measure") != "yes":
                reached = max(reached, at)
        elif child.tag == "backup":
            at = max(0, at - _int(child.findtext("duration")))
        elif child.tag == "forward":
            at += _int(child.findtext("duration"))
    return reached or None


def _drop_time(measure: etree._Element) -> None:
    for attributes in measure.findall("attributes"):
        for time in attributes.findall("time"):
            attributes.remove(time)
        if not len(attributes):
            measure.remove(attributes)


def _declare_time(measure: etree._Element, meter: _Meter) -> None:
    attributes = measure.find("attributes")
    if attributes is None:
        attributes = etree.Element("attributes")
        measure.insert(1 if len(measure) and measure[0].tag == "print" else 0,
                       attributes)
    attributes.append(etree.fromstring(meter.xml()))
    _ordered(attributes)


#: MusicXML fixes the order inside ``<attributes>``; a ``<time>`` appended after
#: a ``<clef>`` is a document MuseScore refuses to open.
_ATTRIBUTE_ORDER = ("divisions", "key", "time", "staves", "part-symbol",
                    "instruments", "clef")


def _ordered(attributes: etree._Element) -> None:
    ranked = sorted(
        enumerate(attributes),
        key=lambda pair: (_ATTRIBUTE_ORDER.index(pair[1].tag)
                          if pair[1].tag in _ATTRIBUTE_ORDER
                          else len(_ATTRIBUTE_ORDER), pair[0]),
    )
    for child in [child for _, child in ranked]:
        attributes.append(child)


def _common_divisions(scans: Sequence[SystemScan]) -> int:
    divisions = 1
    for scan in scans:
        for staff in scan.staves:
            divisions = _lcm(divisions, max(1, staff.divisions))
    return divisions


def _scaled(measure: etree._Element, was: int, now: int) -> etree._Element:
    """A copy of a bar with every duration expressed in the score's divisions."""
    out = copy.deepcopy(measure)
    if was == now or was <= 0:
        return out
    factor = now / was
    for node in out.iter("duration"):
        value = _int(node.text)
        node.text = str(max(1, round(value * factor)))
    return out


# --- small shared helpers ------------------------------------------------


def _staff_of(element: etree._Element) -> int:
    text = element.findtext("staff")
    return int(text.strip()) if text and text.strip().isdigit() else 1


def _duration(note: etree._Element) -> int:
    if note.tag != "note" or note.find("chord") is not None or note.find("grace") is not None:
        return 0
    return _int(note.findtext("duration"))


def _canonical(element: Optional[etree._Element]) -> Optional[str]:
    if element is None:
        return None
    return etree.tostring(element, encoding="unicode").strip()


def _int(text: Optional[str]) -> int:
    try:
        return int(float((text or "").strip()))
    except ValueError:
        return 0


def _set(parent: etree._Element, tag: str, value: str) -> None:
    node = parent.find(tag)
    if node is None:
        node = etree.SubElement(parent, tag)
    node.text = value


def _drop(parent: etree._Element, tag: str) -> None:
    for node in parent.findall(tag):
        parent.remove(node)


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // math.gcd(a, b) if a and b else max(a, b, 1)
