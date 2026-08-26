"""Adaptive spacing for scrolling scores.

Verovio naturally makes a dense measure wider than a sparse one. That is good
engraving on a page, but a scrolling score then speeds up and slows down because
equal musical time occupies different horizontal distances.

A hidden rest staff can put a rhythmic floor under Verovio's spacing. The floor
must not be global: a song made only of quarter notes should not be stretched just
because another song might contain 32nds. We therefore engrave the unmodified
MusicXML first, measure each bar's natural width per quarter, and compute the
smallest widths that keep neighbouring bars within ``MAX_WIDTH_RATIO``. Only bars
that need widening get progressively finer rest grids; the rest carry an invisible
whole-measure rest with no spacing of its own.

The spacer is injected after MuseScore has produced the MusicXML, so it never
reaches MIDI or audio, and the renderer crops it off the bottom afterwards.
"""

from __future__ import annotations

import copy
import math
from fractions import Fraction
from typing import List, Optional, Sequence

from lxml import etree

from .engrave import engrave
from .geometry import SVG_NS, measure_spans

SPACER_ID = "P-SCROLLVIDEO-SPACER"

# Finest grid the adaptive spacer is allowed to use. It is a ceiling, not a
# blanket default: sparse measures normally stay at level 0.
DEFAULT_PER_QUARTER = 8

# Maximum change in rendered width-per-quarter between neighbouring measures.
MAX_WIDTH_RATIO = 1.30

_TYPE = {1: "quarter", 2: "eighth", 4: "16th", 8: "32nd"}
_LEVELS = (0, 1, 2, 4, 8)
_TOLERANCE = 0.005


def _measure_length(measure: etree._Element) -> int:
    """Used length of a MusicXML measure in divisions.

    Multiple voices are written sequentially with ``backup`` elements, so summing
    every non-chord note can count the same bar twice. Following the MusicXML cursor
    and remembering its furthest point gives the actual measure length instead.
    """
    position = 0
    furthest = 0
    for child in measure:
        name = etree.QName(child).localname
        if name == "note":
            if child.find("chord") is not None:
                continue
            position += int(child.findtext("duration") or 0)
            furthest = max(furthest, position)
        elif name == "forward":
            position += int(child.findtext("duration") or 0)
            furthest = max(furthest, position)
        elif name == "backup":
            position -= int(child.findtext("duration") or 0)
    return furthest


def _source_measures(root: etree._Element):
    """First-part measures as (element, divisions, used length)."""
    first = root.find("part")
    if first is None:
        return None

    measures = []
    divisions = None
    time_quarters: Optional[Fraction] = None
    for source in first.findall("measure"):
        divisions_text = source.findtext("attributes/divisions")
        if divisions_text:
            divisions = int(divisions_text)
        if divisions is None:
            return None

        time = source.find("attributes/time")
        if time is not None:
            beats = time.findtext("beats")
            beat_type = time.findtext("beat-type")
            if beats and beat_type and beats.isdigit() and beat_type.isdigit():
                time_quarters = Fraction(int(beats) * 4, int(beat_type))

        length = _measure_length(source)
        if length <= 0 and time_quarters is not None:
            fallback = time_quarters * divisions
            if fallback.denominator == 1:
                length = fallback.numerator
        if length <= 0:
            return None
        measures.append((source, divisions, length))
    return measures


def _bar_widths(svg: str) -> List[float]:
    """Actual bar widths from the horizontal staff lines, not ink extents.

    ``measure_spans`` deliberately includes slurs, lyrics and other ink because it
    is also used for safe tile cropping. For spacing we want the bar itself. Keep
    only the first staff's direct paths in each measure; those are the horizontal
    staff lines, whose extent is the barline-to-barline width, and let the existing
    geometry code handle all ancestor transforms.
    """
    root = etree.fromstring(svg.encode())
    measures = [g for g in root.iter(f"{{{SVG_NS}}}g") if g.get("class") == "measure"]
    for measure in measures:
        staff = next((child for child in measure if child.get("class") == "staff"), None)
        if staff is None:
            return []
        for child in list(measure):
            if child is not staff:
                measure.remove(child)
        for child in list(staff):
            if etree.QName(child).localname != "path":
                staff.remove(child)

    widths = []
    for _measure, low, high in measure_spans(root):
        width = high - low
        if not math.isfinite(width) or width <= 0:
            return []
        widths.append(width)
    return widths


def _minimum_normalized_widths(widths: Sequence[float], quarters: Sequence[float],
                               max_ratio: float = MAX_WIDTH_RATIO) -> List[float]:
    """Smallest feasible width-per-quarter for every measure.

    We may only widen measures. For natural normalized widths ``n[i]``, every
    feasible solution must satisfy ``w[i] >= n[j] / R**abs(i-j)`` for every other
    measure ``j``. The maximum of those lower bounds is therefore the unique
    component-wise minimum. Two passes compute that envelope in linear time.
    """
    if max_ratio <= 1.0:
        raise ValueError(f"max_ratio must be > 1, got {max_ratio}")
    if len(widths) != len(quarters) or not widths:
        raise ValueError("widths and quarters must be non-empty and equally long")
    normalized = [float(width) / float(duration)
                  for width, duration in zip(widths, quarters)]
    target = list(normalized)
    for index in range(1, len(target)):
        target[index] = max(target[index], target[index - 1] / max_ratio)
    for index in range(len(target) - 2, -1, -1):
        target[index] = max(target[index], target[index + 1] / max_ratio)
    return target


def _allowed_levels(per_quarter: int) -> List[int]:
    if per_quarter not in _TYPE:
        raise ValueError(f"per_quarter must be one of {sorted(_TYPE)}, got {per_quarter}")
    return [level for level in _LEVELS if level <= per_quarter]


def _append_rest(measure: etree._Element, duration: int, *, rest_type: str | None = None,
                 measure_rest: bool = False, invisible: bool = False) -> None:
    note = etree.SubElement(measure, "note")
    if invisible:
        note.set("print-object", "no")
        note.set("print-spacing", "no")
    rest = etree.SubElement(note, "rest")
    if measure_rest:
        rest.set("measure", "yes")
    etree.SubElement(note, "duration").text = str(duration)
    etree.SubElement(note, "voice").text = "1"
    if rest_type:
        etree.SubElement(note, "type").text = rest_type


def _write_spacer_staff(musicxml_path: str, out_path: str, levels: Sequence[int],
                        per_quarter: int) -> Optional[str]:
    """Write one spacer grid level per source measure."""
    tree = etree.parse(musicxml_path)
    root = tree.getroot()
    part_list = root.find("part-list")
    measures = _source_measures(root)
    if part_list is None or measures is None or len(measures) != len(levels):
        return None

    spacer_divisions = math.lcm(per_quarter, *(divisions for _, divisions, _ in measures))

    score_part = etree.SubElement(part_list, "score-part")
    score_part.set("id", SPACER_ID)
    part_name = etree.SubElement(score_part, "part-name")
    part_name.set("print-object", "no")
    part_name.text = "Spacer"

    part = etree.SubElement(root, "part")
    part.set("id", SPACER_ID)
    for index, ((source, source_divisions, source_length), level) in enumerate(
            zip(measures, levels)):
        measure = etree.SubElement(part, "measure")
        for attribute in ("number", "implicit", "non-controlling"):
            if source.get(attribute) is not None:
                measure.set(attribute, source.get(attribute))

        source_time = source.find("attributes/time")
        if index == 0 or source_time is not None:
            attributes = etree.SubElement(measure, "attributes")
            if index == 0:
                etree.SubElement(attributes, "divisions").text = str(spacer_divisions)
            if source_time is not None:
                hidden_time = copy.deepcopy(source_time)
                hidden_time.set("print-object", "no")
                attributes.append(hidden_time)
            if index == 0:
                clef = etree.SubElement(attributes, "clef")
                clef.set("print-object", "no")
                etree.SubElement(clef, "sign").text = "percussion"

        measure_units = source_length * spacer_divisions // source_divisions
        if level == 0:
            # Keep the part temporally valid without putting a spacing floor under
            # a measure that was already narrow enough naturally.
            _append_rest(measure, measure_units, measure_rest=True, invisible=True)
            continue

        step = spacer_divisions // level
        slots, remainder = divmod(measure_units, step)
        for _ in range(slots):
            _append_rest(measure, step, rest_type=_TYPE[level])
        if remainder:
            forward = etree.SubElement(measure, "forward")
            etree.SubElement(forward, "duration").text = str(remainder)

    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return out_path


def add_spacer_staff(musicxml_path: str, out_path: str,
                     per_quarter: int = DEFAULT_PER_QUARTER,
                     max_ratio: float = MAX_WIDTH_RATIO) -> Optional[str]:
    """Append the minimum adaptive rest grid needed to cap adjacent width changes.

    ``per_quarter`` is the finest grid allowed, not the grid forced everywhere.
    Measures start at level 0 and are promoted through quarter/eighth/16th/32nd
    grids only until their measured width reaches the minimum feasible target.
    """
    allowed = _allowed_levels(per_quarter)
    tree = etree.parse(musicxml_path)
    measures = _source_measures(tree.getroot())
    if measures is None:
        return None

    natural = engrave(musicxml_path)
    natural_widths = _bar_widths(natural.svg)
    if len(natural_widths) != len(measures):
        # Geometry should be one measure group per MusicXML measure. If Verovio
        # ever changes that shape, retain the old safe behaviour rather than ship
        # uncapped scroll-speed jumps.
        return _write_spacer_staff(
            musicxml_path, out_path, [per_quarter] * len(measures), per_quarter)

    quarters = [Fraction(length, divisions) for _source, divisions, length in measures]
    target = _minimum_normalized_widths(natural_widths, quarters, max_ratio)
    current = [width / float(duration)
               for width, duration in zip(natural_widths, quarters)]
    levels = [0] * len(measures)

    for _round in range(len(allowed) - 1):
        changed = False
        for index, (actual, wanted) in enumerate(zip(current, target)):
            if actual >= wanted * (1.0 - _TOLERANCE):
                continue
            level_index = allowed.index(levels[index])
            if level_index + 1 < len(allowed):
                levels[index] = allowed[level_index + 1]
                changed = True
        if not changed:
            break

        if _write_spacer_staff(musicxml_path, out_path, levels, per_quarter) is None:
            return None
        candidate_widths = _bar_widths(engrave(out_path).svg)
        if len(candidate_widths) != len(measures):
            return _write_spacer_staff(
                musicxml_path, out_path, [per_quarter] * len(measures), per_quarter)
        current = [width / float(duration)
                   for width, duration in zip(candidate_widths, quarters)]

    # All-natural songs never entered the loop, so write their level-0 invisible
    # timing rests now. Measures that needed help keep only the coarsest grid that
    # reached their mathematically minimal target.
    return _write_spacer_staff(musicxml_path, out_path, levels, per_quarter)


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
