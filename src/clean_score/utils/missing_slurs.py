#!/usr/bin/env python3
"""
Auto-fix OCR'd scores that dropped a slur from one voice but kept it on a parallel
voice (cross-voice mirror), analogous to `add_missing_ties` but for slurs.

Why this matters for lyrics: a note that is a slur *continuation* gets no syllable
slot (see lyric_txt `_is_slur_continuation`). When OCR loses a slur, those notes
become full slots, so every syllable after the melisma shifts onto the wrong note.
Restoring the slur from a parallel voice fixes both the notation and the lyric
alignment without any manual editing.

A slur lives on the Chord: the start chord carries `Spanner[@type='Slur']` with a
`<next>` (positive time offset to the end), the end chord carries one with a `<prev>`
(negative offset). The offsets are *relative time distances*, so for parallel voices
(same rhythm across the span) we can copy the donor's endpoints verbatim.
"""

import logging
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from lxml import etree

from .utils import resolve_duration

logger = logging.getLogger(__name__)


def _slur_with(chord: etree._Element, kind: str) -> Optional[etree._Element]:
    """Return the chord's Slur spanner that has a <next> ('next') or <prev> ('prev')."""
    for sp in chord.findall("Spanner[@type='Slur']"):
        if sp.find(kind) is not None:
            return sp
    return None


def _voice_chords(staff: etree._Element) -> Dict[int, List[Tuple[int, int, etree._Element]]]:
    """
    Map voice_index -> ordered [(measure_index, time_pos, chord)] using the same tick
    basis as loop_staff (resolve_duration over notated durations). Tuplet scaling is
    intentionally ignored: it is consistent across voices, so cross-voice keys still match.
    """
    out: Dict[int, List[Tuple[int, int, etree._Element]]] = {}
    measure_index = -1
    for measure in staff.findall("Measure"):
        measure_index += 1
        voice_index = -1
        for voice in measure.findall("voice"):
            voice_index += 1
            time_pos = 0
            for el in voice:
                if el.tag == "Chord":
                    out.setdefault(voice_index, []).append((measure_index, time_pos, el))
                if el.tag in ("Chord", "Rest"):
                    dt = el.find("durationType")
                    dots = el.find("dots")
                    time_pos += resolve_duration(
                        dt.text if dt is not None else "0",
                        dots.text if dots is not None else "0",
                    )
                elif el.tag == "location":
                    fr = el.find("fractions")
                    if fr is not None and fr.text:
                        time_pos += resolve_duration(fr.text)
    return out


def add_missing_slurs(root: etree._Element) -> int:
    """
    Copy a slur onto a voice that is missing it from a parallel voice that has it at
    the same tick span (same start, same end, same note count). Returns slurs added.
    """
    staves = root.findall(".//Score/Staff")
    if not staves:
        return 0

    # Donor slur spans across every voice, keyed by the start position.
    donors: Dict[Tuple[int, int], List[Dict]] = {}
    for staff in staves:
        for chords in _voice_chords(staff).values():
            open_start: Optional[Tuple[int, Tuple[int, int], etree._Element]] = None
            for idx, (mi, tp, ch) in enumerate(chords):
                end_sp = _slur_with(ch, "prev")
                if end_sp is not None and open_start is not None:
                    s_idx, s_key, s_sp = open_start
                    donors.setdefault(s_key, []).append(
                        {"end": (mi, tp), "start_sp": s_sp,
                         "end_sp": end_sp, "count": idx - s_idx + 1}
                    )
                    open_start = None
                start_sp = _slur_with(ch, "next")
                if start_sp is not None:
                    open_start = (idx, (mi, tp), start_sp)
    if not donors:
        return 0

    added = 0
    for staff in staves:
        for chords in _voice_chords(staff).values():
            pos_index = {(mi, tp): idx for idx, (mi, tp, _) in enumerate(chords)}
            for idx, (mi, tp, ch) in enumerate(chords):
                spans = donors.get((mi, tp))
                if not spans or _slur_with(ch, "next") is not None:
                    continue  # no donor here, or this voice already starts a slur here
                for span in spans:
                    end_idx = pos_index.get(span["end"])
                    if end_idx is None or end_idx - idx + 1 != span["count"]:
                        continue  # endpoint absent or note count differs -> not parallel
                    end_ch = chords[end_idx][2]
                    if _slur_with(end_ch, "prev") is not None:
                        continue
                    ch.append(deepcopy(span["start_sp"]))
                    end_ch.append(deepcopy(span["end_sp"]))
                    added += 1
                    logger.info(
                        "Mirrored missing slur onto staff %s measure %d (%d notes).",
                        staff.get("id", "?"), mi + 1, span["count"],
                    )
                    break
    return added
