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
page's rows and no single row is the right answer.

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

import copy
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

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
        measures.append(measure)

    numbering = _voice_numbering(per_bar)
    for measure, notes, trailing in zip(measures, per_bar, tails):
        for element in _voiced(notes, numbering):
            measure.append(element)
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
    issue #173's 63 bands re-read), sorting by pitch is now strictly worse
    rather than a trade: 67.7% and 84 voice faults against 68.2% and 67. The
    only page it changes at all is Herää Suomi p3, where the two basses cross on
    the page and it swaps a column homr had numbered right -- 95.8% and 3 voice
    faults down to 85.6% and 20. On the older parses it bought Herää Suomi p1
    back in exchange; it no longer buys anything, because p1's flip has stopped
    being visible in the heights while remaining in the score (see
    :func:`assemble`).

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


def assemble(scans: Sequence[SystemScan], out_path: str) -> str:
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
    and there is no single row to put it on.

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
    crop's "voice 1" to the next crop's, and on Herää Suomi p1 homr really does
    number the two upper voices one way round in systems 1 and 3 and the other
    way round in 2 and 4. Sounding height is the only evidence available at that
    point, and it is not sufficient: on p3 of the same song the two basses cross
    and change places on the page, so a rule ranking by height would swap a
    column homr had right. Under the issue #141 rule a crop read with the voices
    the other way up is homr's to fix, since it is the parse disagreeing with
    the page rather than us disagreeing with the parse.

    Issue #192 re-measured that page on `main @ 6c3bbf4` and it is still 30
    voice faults, in the same five bars, all on staff 1; swapping that staff's
    two voices in systems 2 and 4 -- equivalently in 1 and 3 -- still takes the
    page to 1. What changed is that height can no longer see it. Issue #190
    found voice 1 is the higher-sounding of the two in all four systems, which
    is true; on the older parses it was not, because system 1's second voice was
    a scrap of 3 notes that happened to sit high. The engine now separates those
    voices properly, so the heights agree with homr's numbering everywhere and
    the flip survives underneath them -- which is a reason to keep refusing a
    height rule rather than to reach for one.
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

    for column in range(width):
        part = etree.SubElement(score, "part", id=f"P{column + 1}")
        _fill_column(part, scans, column, divisions, meters)

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
) -> None:
    number = 0
    prevailing_key: Optional[str] = None
    prevailing_clef: Optional[str] = None
    first = True

    for system, scan in enumerate(scans):
        staff = scan.staves[column] if column < scan.width else None

        for bar in range(scan.bars):
            number += 1
            meter = meters[system][bar]
            source = staff.measures[bar] if staff and bar < staff.bars else None
            if source is None:
                measure = _rest_measure(number, meter.ticks)
            else:
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
            first = False
            part.append(measure)


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
                           [length for length in lengths if length], divisions)
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
) -> _Meter:
    """The meter a span is really in: its own length, in homr's denominator.

    ``lengths`` is one entry per **bar** of the span, not one per staff.
    """
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
