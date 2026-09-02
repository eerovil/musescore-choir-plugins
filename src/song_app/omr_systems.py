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
class SystemScan:
    """What came back for one printed system."""

    index: int                      # 1-based, the band's own index
    musicxml: str
    staves: List[Staff] = field(default_factory=list)

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
    produced = omr.read_page(
        image.path,
        out_dir=out_dir,
        log=log,
        label=f"song app homr system {image.index}",
        queue=queue,
        engine=engine,
    )
    staves = flatten(produced)
    if not staves:
        raise ScanError(f"System {image.index} came back with no staves ({produced}).")
    return SystemScan(index=image.index, musicxml=produced, staves=staves)


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

    ``<backup>`` and ``<forward>`` are dropped rather than filtered: they exist
    to move the cursor between staves *and* between voices, and telling those
    two apart after the fact is guesswork. Instead each bar is rebuilt from its
    notes -- grouped by voice, with a backup of the previous group's own length
    written between groups -- which is well defined whatever the input did.
    """
    measures: List[etree._Element] = []
    divisions = 0
    clef = key = time = None

    for source in part.findall("measure"):
        measure = etree.Element("measure", number=source.get("number") or "")
        notes: List[etree._Element] = []
        trailing: List[etree._Element] = []
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
                if _staff_of(child) == number:
                    notes.append(copy.deepcopy(child))
            elif tag in ("backup", "forward"):
                continue
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

        for element in _voiced(notes):
            measure.append(element)
        for element in trailing:
            measure.append(element)
        measures.append(measure)

    return Staff(measures=measures, divisions=divisions or 1,
                 clef=clef, key=key, time=time)


def _voiced(notes: List[etree._Element]) -> List[etree._Element]:
    """Notes regrouped voice by voice, with the backups that implies.

    Voices are renumbered from 1, because a voice number is only meaningful
    inside its part and this staff is about to become a part of its own -- a
    staff whose notes said ``<voice>5</voice>`` would otherwise arrive claiming
    to be the fifth voice of a part that has one.
    """
    groups: Dict[str, List[etree._Element]] = {}
    for note in notes:
        groups.setdefault((note.findtext("voice") or "1").strip(), []).append(note)

    out: List[etree._Element] = []
    spent = 0
    for n, (_voice, group) in enumerate(groups.items(), start=1):
        # Back up by the *previous* voice's own length, not by everything
        # written so far: each voice starts again at the head of the bar, so a
        # running total would wind the third voice back past the start of it.
        if out and spent:
            backup = etree.Element("backup")
            etree.SubElement(backup, "duration").text = str(spent)
            out.append(backup)
        spent = 0
        for note in group:
            _set(note, "voice", str(n))
            _drop(note, "staff")
            spent += _duration(note)
            out.append(note)
    return out


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

    Three seams are closed here, all of them consequences of each crop being its
    own document. ``divisions`` is unified across the score and every duration
    scaled to it; bars are numbered continuously rather than restarting at 1 in
    every system; and a key or time signature is written only where it says
    something that was not already true. A ``<print new-system="yes"/>`` marks
    each seam, so ``clean_score``'s per-system mode cuts the score where the page
    is cut.
    """
    if not scans:
        raise ScanError("Nothing to assemble: no systems were read.")

    width = max(scan.width for scan in scans)
    divisions = _common_divisions(scans)

    score = etree.Element("score-partwise", version="4.0")
    part_list = etree.SubElement(score, "part-list")
    for column in range(width):
        score_part = etree.SubElement(part_list, "score-part", id=f"P{column + 1}")
        # The name is positional on purpose. Nothing downstream should read a
        # voice out of it: which staff is which part is the grid's question.
        etree.SubElement(score_part, "part-name").text = f"Staff {column + 1}"

    for column in range(width):
        part = etree.SubElement(score, "part", id=f"P{column + 1}")
        _fill_column(part, scans, column, divisions)

    tree = etree.ElementTree(score)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tree.write(out_path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return out_path


def _fill_column(
    part: etree._Element,
    scans: Sequence[SystemScan],
    column: int,
    divisions: int,
) -> None:
    number = 0
    prevailing_key: Optional[str] = None
    prevailing_time: Optional[str] = None
    prevailing_clef: Optional[str] = None
    first = True

    for system, scan in enumerate(scans):
        staff = scan.staves[column] if column < scan.width else None
        beats = _measure_ticks(scan, divisions)

        for bar in range(scan.bars):
            number += 1
            source = staff.measures[bar] if staff and bar < staff.bars else None
            if source is None:
                measure = _rest_measure(number, beats)
            else:
                measure = _scaled(source, staff.divisions, divisions)
                measure.set("number", str(number))

            if system and bar == 0:
                measure.insert(0, etree.Element("print", {"new-system": "yes"}))

            if bar == 0:
                # Only the seam is rewritten. A bar in the middle of a crop is
                # left with whatever it declares, because that is a change the
                # page really prints -- B4's fifth system goes 3/4, 5/4, 4/4
                # inside one system, and correcting those away would be losing
                # music to tidy up a join.
                wanted = _signature(scan, staff)
                _merge_attributes(measure, _needed_attributes(
                    first, divisions, wanted,
                    (prevailing_key, prevailing_time, prevailing_clef),
                ))
                prevailing_key, prevailing_time, prevailing_clef = wanted
            else:
                _drop_global(measure)
                declared = measure.find("attributes")
                if declared is not None:
                    prevailing_key = _canonical(declared.find("key")) or prevailing_key
                    prevailing_time = _canonical(declared.find("time")) or prevailing_time
                    prevailing_clef = _canonical(declared.find("clef")) or prevailing_clef
            first = False
            part.append(measure)


def _signature(scan: SystemScan, staff: Optional[Staff]):
    """What this column's bar should be declaring: its own, else the system's.

    A resting column has no signature of its own, and inheriting the system's
    is the only honest answer -- an empty staff is silent, not in another key.
    """
    donor = staff
    if donor is None or (donor.key is None and donor.time is None and donor.clef is None):
        donor = scan.staves[0] if scan.staves else None
    key = _canonical(donor.key) if donor is not None else None
    time = _canonical(donor.time) if donor is not None else None
    clef = _canonical(staff.clef) if staff is not None and staff.clef is not None else None
    return key, time, clef


def _needed_attributes(first: bool, divisions: int, wanted, prevailing):
    """An ``<attributes>`` holding only what has changed, or ``None``.

    The first bar of a part always gets one -- ``divisions`` has to be declared
    somewhere and a part with no clef is unreadable. After that a re-declaration
    that repeats what is already in force is dropped, which is most of them:
    every crop re-declares its key and time because every crop is a document
    that has just begun.
    """
    key, time, clef = wanted
    was_key, was_time, was_clef = prevailing
    attrs = etree.Element("attributes")
    if first:
        etree.SubElement(attrs, "divisions").text = str(divisions)
    if key is not None and (first or key != was_key):
        attrs.append(etree.fromstring(key))
    if time is not None and (first or time != was_time):
        attrs.append(etree.fromstring(time))
    if clef is not None and (first or clef != was_clef):
        attrs.append(etree.fromstring(clef))
    if first and attrs.find("clef") is None:
        attrs.append(etree.fromstring("<clef><sign>G</sign><line>2</line></clef>"))
    if first and attrs.find("key") is None:
        attrs.append(etree.fromstring("<key><fifths>0</fifths></key>"))
    if first and attrs.find("time") is None:
        attrs.append(etree.fromstring(
            f"<time><beats>{_FALLBACK_TIME[0]}</beats>"
            f"<beat-type>{_FALLBACK_TIME[1]}</beat-type></time>"))
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


def _measure_ticks(scan: SystemScan, divisions: int) -> int:
    """How long a bar of this system is, for the columns that are resting."""
    for staff in scan.staves:
        canonical = _canonical(staff.time)
        if canonical is None:
            continue
        parsed = etree.fromstring(canonical)
        beats = _int(parsed.findtext("beats"))
        beat_type = _int(parsed.findtext("beat-type"))
        if beats and beat_type:
            return max(1, round(divisions * 4 * beats / beat_type))
    return divisions * 4


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
