"""
Export/import lyrics between MuseScore .mscx (XML) and a plain TXT format.
Uses XML spanner (slur) info so that only the first note of a slur gets a token;
slur-continuation notes get no token (no syllable, no underscore).

Format:
  # Measure N
  staffNum [syllable_count]: token1 token2 ...
Tokens are space-separated; hyphen merges syllables (e.g. il-man). Underscore _
means lyric-eligible note with no lyric. The number in brackets is the syllable count
for that voice in that measure (helps LLMs keep count when fixing text). Lyrics are ineligible for export when inside a spanner (slur/tie continuation) or in a verse other than 1;
those positions get no token on export and verse 1 lyrics are cleared from them on import.
Verse 1 only, voice 0. Rests get no token.

JSON format (line-by-line): array of objects with "measure_start" (int) and part keys (e.g. S1, S2, A1, A2) whose values are lyric lines. Tokens are distributed across measures using the score. Use a .json path with import_file to import this format.

This module owns lyric placement end to end — format normalization, target routing,
chord eligibility, syllable distribution, XML placement and the diagnostics that come
out of it — for all three callers: the CLI file adapters, the AI-JSON paste, and the
song app's manual editor. Its interface:

    export_lyrics(root) -> str                     the TXT projection of the score
    place_lyrics(root, source, replace=, split=)   put lyrics in; returns LyricImport
    editor_grid(root) -> EditorGrid                what the manual editor renders
    slot_counts(root) -> {staff: {measure: n}}     notes that take a syllable
    blocks_from_cells(grid, cells) -> [block]      that editor's cells as lyric JSON
    export_file(...) / import_file(...)            the .txt/.json file adapters

Mismatches (a line whose syllables do not fit its chords, a measure_start that could
not be inferred) come back as `Mismatch` records on the result — measure range, target
staff ids, kind, counts and a ready-made sentence — so callers never parse text to
learn what happened.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .utils.per_system import system_ranges

# --------------------------------------------------------------------------- #
# What a caller gets back
# --------------------------------------------------------------------------- #

TOO_MANY = "too_many"          # more syllables than eligible chords in the range
TOO_FEW = "too_few"            # fewer syllables than eligible chords
NO_SYSTEMS = "no_systems"      # null measure_start, but the score has no system breaks
NO_SYSTEM_FOR_LINE = "no_system_for_line"   # more null lines than printed systems
BLOCK_COUNT = "block_count"    # lines vs systems disagree, so the fill may be off


@dataclass(frozen=True)
class Mismatch:
    """One thing the caller should look at, in fields rather than prose."""

    kind: str
    message: str
    measure_start: int = 0          # 1-based; 0 when not tied to a measure range
    measure_end: int = 0            # 1-based inclusive
    staff_ids: Tuple[int, ...] = ()
    syllables: int = 0
    slots: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "measure_start": self.measure_start,
            "measure_end": self.measure_end,
            "staff_ids": list(self.staff_ids),
            "syllables": self.syllables,
            "slots": self.slots,
        }


@dataclass(frozen=True)
class LyricImport:
    """The outcome of placing lyrics: what was placed, and what did not fit."""

    mismatches: List[Mismatch] = field(default_factory=list)
    filled_measure_starts: List[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True)
class EditorPart:
    """One lyric-bearing output part, as the manual editor lists it."""

    id: int
    name: str


@dataclass(frozen=True)
class EditorSystem:
    """One printed system, as 1-based inclusive measure numbers."""

    index: int
    start: int
    end: int


@dataclass(frozen=True)
class EditorGrid:
    """The manual editor's projection: a text cell per (printed system, part)."""

    parts: List[EditorPart] = field(default_factory=list)
    systems: List[EditorSystem] = field(default_factory=list)
    cells: Dict[int, Dict[str, str]] = field(default_factory=dict)
    capacities: Dict[int, Dict[str, int]] = field(default_factory=dict)

    def text(self, system_index: int, part_name: str) -> str:
        return self.cells.get(system_index, {}).get(part_name, "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parts": [{"name": p.name, "id": p.id} for p in self.parts],
            "systems": [{"index": s.index, "start": s.start, "end": s.end}
                        for s in self.systems],
            "cells": {str(si): dict(cells) for si, cells in self.cells.items()},
            "capacities": {str(si): dict(counts)
                           for si, counts in self.capacities.items()},
        }

# Default mapping from JSON part keys (e.g. S1, A2) to staff id. Overridable.
_DEFAULT_PART_TO_STAFF: Dict[str, int] = {"S1": 1, "S2": 2, "A1": 3, "A2": 4}


# Duration type to ticks (fraction of whole). Division from score multiplies this.
_DURATION_MAP = {
    "whole": 1,
    "half": 1 / 2,
    "quarter": 1 / 4,
    "eighth": 1 / 8,
    "16th": 1 / 16,
    "32nd": 1 / 32,
    "64th": 1 / 64,
    "128th": 1 / 128,
}


def _get_division(score: etree._Element) -> int:
    el = score.find(".//Division")
    return int(el.text.strip()) if el is not None and el.text else 480


def _resolve_duration_ticks(
    duration_type: str, dots: str, division: int
) -> int:
    if "/" in duration_type:
        try:
            num, den = map(int, duration_type.split("/"))
            return int(division * 4 * num / den)
        except ValueError:
            return 0
    frac = _DURATION_MAP.get(duration_type.lower(), 0)
    if frac == 0:
        return 0
    ticks = int(division * 4 * frac)
    d = dots.strip() if dots else "0"
    if d == "1":
        ticks += ticks // 2
    elif d == "2":
        ticks += (ticks // 2) + (ticks // 4)
    elif d == "3":
        ticks += (ticks // 2) + (ticks // 4) + (ticks // 8)
    return ticks


def _is_slur_continuation(chord: etree._Element) -> bool:
    """True if this chord is under a slur but not the first note of the slur (has prev, no lyric slot)."""
    for spanner in chord.findall(".//Spanner[@type='Slur']"):
        if spanner.find(".//prev") is not None:
            return True
    return False


def _is_tie_continuation(chord: etree._Element) -> bool:
    """True if this chord is the continuation of a tie (Tie spanner with prev; often on Note)."""
    for spanner in chord.findall(".//Spanner[@type='Tie']"):
        if spanner.find(".//prev") is not None:
            return True
    return False


def _is_continuation_no_lyric(chord: etree._Element) -> bool:
    """True if this chord should get no lyric token (slur or tie continuation)."""
    return _is_slur_continuation(chord) or _is_tie_continuation(chord)


def _has_slur_start(chord: etree._Element) -> bool:
    """True if this chord starts a slur (has Slur spanner with next)."""
    for spanner in chord.findall(".//Spanner[@type='Slur']"):
        if spanner.find(".//next") is not None:
            return True
    return False


def _has_tie_start(chord: etree._Element) -> bool:
    """True if this chord starts a tie (has Tie spanner with next; often on Note)."""
    for spanner in chord.findall(".//Spanner[@type='Tie']"):
        if spanner.find(".//next") is not None:
            return True
    return False


def _is_verse1(no_el: Optional[etree._Element]) -> bool:
    """Verse 1 = omit <no> (no element or empty). <no>1</no> = verse 2."""
    if no_el is None:
        return True
    return ((no_el.text or "").strip() == "") if no_el.text is not None else True


def _get_verse1_lyric(chord: etree._Element) -> Optional[Tuple[str, str]]:
    """Returns (syllabic, text) for verse 1 (omit no), or verse 2 (no=1) if verse 1 is missing. None if no lyrics."""
    verse1: Optional[Tuple[str, str]] = None
    verse2: Optional[Tuple[str, str]] = None
    for lyrics in chord.findall(".//Lyrics"):
        no_el = lyrics.find("no")
        no = (no_el.text or "").strip() if no_el is not None else ""
        syllabic_el = lyrics.find("syllabic")
        text_el = lyrics.find("text")
        syllabic = (syllabic_el.text or "").strip() if syllabic_el is not None else "single"
        text = (text_el.text or "").strip() if text_el is not None else ""
        pair = (syllabic, text)
        if _is_verse1(no_el) and verse1 is None:
            verse1 = pair
        elif no == "1":
            verse2 = pair
    if verse1 is not None and (verse1[1] or verse1[0]):
        return verse1
    if verse2 is not None:
        return verse2
    return verse1


def _token_from_lyric(syllabic: str, text: str) -> str:
    """One token string; may end with '-' for begin/middle. Ineligibility (spanner, verse != 1) is handled by caller."""
    t = (text or "").strip()
    suffix = ""
    if syllabic in ("begin", "middle"):
        suffix = "-"
    return t + suffix


def _merge_tokens(tokens: List[str]) -> str:
    """Merge hyphenated syllables and join with space."""
    if not tokens:
        return ""
    result: List[str] = []
    cur = tokens[0]
    for i in range(1, len(tokens)):
        nxt = tokens[i]
        if cur.endswith("-"):
            cur = cur.rstrip("-") + "-" + nxt.lstrip("-").strip()
        elif nxt.startswith("-"):
            cur = cur.rstrip() + "-" + nxt.lstrip("-").strip()
        else:
            result.append(cur)
            cur = nxt
    result.append(cur)
    return " ".join(result)


def _tokenize_line(line: str) -> List[str]:
    """Split a line into tokens (space-separated, hyphen-merged). Same logic as _parse_txt."""
    tokens: List[str] = []
    for part in line.split():
        part = part.strip()
        if not part:
            continue
        if part == "_":
            tokens.append("_")
        elif tokens and tokens[-1].endswith("-"):
            tokens[-1] = tokens[-1].rstrip("-") + "-" + part
        else:
            tokens.append(part)
    return tokens


def _get_chord_counts_per_measure(score: etree._Element) -> Dict[int, Dict[int, int]]:
    """
    Return by_staff[staff_id][measure_1based] = number of lyric-eligible chords (voice 0).
    Same eligibility as export: no chord for Rest; skip slur/tie continuation and middle.
    Only considers Staff elements that contain Measure children (skips Part-level stub Staffs).
    """
    out: Dict[int, Dict[int, int]] = {}
    staffs = score.findall(".//Staff")
    staffs = [s for s in staffs if s.find(".//Measure") is not None]
    for staff in staffs:
        staff_id = int(staff.get("id", "0"))
        measure_index = -1
        slur_active = False
        tie_active = False
        for measure in staff.findall(".//Measure"):
            measure_index += 1
            voices = measure.findall("voice")
            if not voices:
                continue
            voice = voices[0]
            count = 0
            for el in voice:
                if el.tag == "Chord":
                    if _is_continuation_no_lyric(el):
                        if _is_slur_continuation(el):
                            slur_active = False
                        if _is_tie_continuation(el):
                            tie_active = False
                        continue
                    if slur_active and not _has_slur_start(el) and not _is_slur_continuation(el):
                        continue
                    if tie_active and not _has_tie_start(el) and not _is_tie_continuation(el):
                        continue
                    count += 1
                    if _has_slur_start(el):
                        slur_active = True
                    if _has_tie_start(el):
                        tie_active = True
                elif el.tag in ("Rest", "location"):
                    continue
            out.setdefault(staff_id, {})[measure_index + 1] = count
    return out


def _read_lyrics_staff_map(score_root: etree._Element) -> Dict[int, List[int]]:
    """
    Read the printed-staff -> output-staff map persisted by clean_score in the
    'lyricsStaffMap' metaTag (format "1:1,2;2:3;3:4,5;4:6"). Returns {} if absent.
    """
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return {}
    meta = None
    for m in score.findall("metaTag"):
        if m.get("name") == "lyricsStaffMap":
            meta = m
            break
    if meta is None or not (meta.text or "").strip():
        return {}
    result: Dict[int, List[int]] = {}
    for entry in meta.text.strip().split(";"):
        if ":" not in entry:
            continue
        printed_str, outs_str = entry.split(":", 1)
        try:
            printed = int(printed_str)
        except ValueError:
            continue
        outs = [int(o) for o in outs_str.split(",") if o.strip().isdigit()]
        if outs:
            result[printed] = outs
    return result


def _read_lyrics_system_map(
    score_root: etree._Element,
) -> Optional[List[Dict[str, Any]]]:
    """
    Read the per-system printed-staff -> output-staff map written by clean_score
    --per-system in the 'lyricsSystemMap' metaTag (JSON: a list of
    {"start", "end", "map": {printed: [output_ids]}} with 1-based measure ranges).
    Returns None if absent/unparseable (callers then fall back to lyricsStaffMap).
    """
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return None
    for m in score.findall("metaTag"):
        if m.get("name") == "lyricsSystemMap" and (m.text or "").strip():
            try:
                raw = json.loads(m.text)
            except ValueError:
                return None
            result: List[Dict[str, Any]] = []
            for entry in raw:
                try:
                    pmap = {
                        int(k): [int(x) for x in v]
                        for k, v in (entry.get("map") or {}).items()
                    }
                    result.append(
                        {"start": int(entry["start"]), "end": int(entry["end"]), "map": pmap}
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            return result or None
    return None


def _map_for_measure(
    system_map: List[Dict[str, Any]], measure_start: int
) -> Dict[int, List[int]]:
    """Pick the per-system staff map whose measure range contains measure_start."""
    for entry in system_map:
        if entry["start"] <= measure_start <= entry["end"]:
            return entry["map"]
    return {}


def _read_part_name_map(score_root: etree._Element) -> Dict[str, int]:
    """
    Map a part NAME (trackName, e.g. "T1", "B") to its output staff id, read from the
    score's Parts. Lets lyric JSON address voices by name — robust to printed-staff
    order (e.g. an ossia T3 printed on top), which staff_number/position can't handle.
    Keys are upper-cased for case-insensitive lookup.
    """
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return {}
    result: Dict[str, int] = {}
    for part in score.findall("Part"):
        name = (part.findtext("trackName") or "").strip()
        stub = part.find("Staff")
        if name and stub is not None and stub.get("id"):
            result[name.upper()] = int(stub.get("id"))
    return result


def _resolve_targets(values: List[Any], name_map: Dict[str, int]) -> List[int]:
    """Turn a parts list of ints and/or part-name strings into output staff ids."""
    targets: List[int] = []
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            targets.append(v)
        elif isinstance(v, str):
            s = v.strip()
            if s.isdigit():
                targets.append(int(s))
            elif s.upper() in name_map:
                targets.append(name_map[s.upper()])
    return targets


def _derive_target_staves(
    staff_number: Optional[int],
    position: Optional[str],
    staff_map: Dict[int, List[int]],
    positions_in_block: set,
) -> List[int]:
    """
    Map a printed (staff_number, position) to output staff ids using the persisted map.
    Divisi is decided per block (a staff may sing unison in one passage and split in another):
      - printed staff -> 1 output staff: any position maps to that staff.
      - printed staff -> 2 output staves, both 'above' and 'below' present in this block:
        'above' -> upper output, 'below' -> lower output (true divisi with separate text).
      - printed staff -> 2 output staves, only one position in this block: map to BOTH
        (the two voices sing the same words in unison for this passage).
    Falls back to [staff_number] when there is no map entry (e.g. unsplit score, no metaTag).
    """
    if staff_number is None:
        return []
    outs = staff_map.get(staff_number)
    if not outs:
        return [staff_number]
    if len(outs) == 1:
        return [outs[0]]
    if "above" in positions_in_block and "below" in positions_in_block:
        return [outs[0]] if position == "above" else [outs[1]]
    return list(outs)


def _convert_lyrics_format_to_legacy(
    data: List[Dict[str, Any]],
    staff_map: Optional[Dict[int, List[int]]] = None,
    system_map: Optional[List[Dict[str, Any]]] = None,
    name_map: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """
    Convert new JSON format (measure_start, lyrics[{text, part(s), staff_number, position}])
    to legacy format (measure_start, "1": "...", "2": "..."), keyed by output staff id.

    Target output staves are resolved in priority order:
      1. explicit "part"/"parts" — a list (or scalar) of output staff ids and/or part
         NAMES ("T1", "B"); names map via `name_map` (the score's trackNames). Robust to
         printed-staff order, so this is preferred for per-system scores.
      2. otherwise (staff_number, position) via the staff map. With `system_map`
         (clean_score --per-system) the printed numbering shifts per system, so each
         block uses the map for the system covering its measure_start; else the single
         `staff_map` ('lyricsStaffMap') is used for all blocks.
    Lyrics for verses other than 1 are ignored. If multiple lyrics in a block resolve to
    the same output staff, texts are space-joined.
    """
    name_map = name_map or {}
    if not data or not isinstance(data[0], dict):
        return data
    first = data[0]
    if "lyrics" not in first or not isinstance(first.get("lyrics"), list):
        return data  # already legacy
    staff_map = staff_map or {}
    result: List[Dict[str, Any]] = []
    for block in data:
        measure_start = block.get("measure_start")
        if measure_start is None:
            continue
        block_map = (
            _map_for_measure(system_map, measure_start)
            if system_map is not None
            else staff_map
        )
        block_lyrics = [ly for ly in (block.get("lyrics") or []) if isinstance(ly, dict)]
        # Positions used per printed staff in THIS block (decides divisi vs unison locally).
        positions_by_staff: Dict[int, set] = {}
        for lyric in block_lyrics:
            sn = lyric.get("staff_number")
            if isinstance(sn, int):
                positions_by_staff.setdefault(sn, set()).add(lyric.get("position"))
        part_texts: Dict[int, List[str]] = {}
        for lyric in block_lyrics:
            verse = lyric.get("verse")
            if verse not in (None, 1, "1"):
                continue  # practice track: verse 1 only
            text = (lyric.get("text") or "").strip()
            parts = lyric.get("parts")
            if parts is None and lyric.get("part") is not None:
                parts = lyric.get("part")
            if not isinstance(parts, list):
                parts = [parts] if parts is not None else []
            if parts:
                targets = _resolve_targets(parts, name_map)
            else:
                targets = _derive_target_staves(
                    lyric.get("staff_number"),
                    lyric.get("position"),
                    block_map,
                    positions_by_staff.get(lyric.get("staff_number"), set()),
                )
            for part_num in targets:
                part_texts.setdefault(part_num, []).append(text)
        legacy: Dict[str, Any] = {"measure_start": measure_start}
        for part_num in sorted(part_texts.keys()):
            legacy[str(part_num)] = " ".join(part_texts[part_num])
        result.append(legacy)
    return result


def _parse_json_txt(
    json_str: str,
    staff_map: Optional[Dict[int, List[int]]] = None,
    system_map: Optional[List[Dict[str, Any]]] = None,
    name_map: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """
    Parse JSON format: array of objects with 'measure_start' (int) and part keys (e.g. S1, S2, A1, A2),
    or new format with 'measure_start' and 'lyrics' array (items with 'text', 'part'/'parts',
    'staff_number', 'position'). staff_map maps printed staff -> output staves; system_map
    (per-system mode) overrides it per measure range; name_map maps part names -> staff ids.
    Returns list of legacy {"measure_start": N, "1": "text", ...} keyed by output staff id.
    """
    data = json.loads(json_str)
    if not isinstance(data, list) or len(data) == 0:
        return []
    first = data[0]
    if not isinstance(first, dict) or "measure_start" not in first:
        return []
    return _convert_lyrics_format_to_legacy(
        data, staff_map=staff_map, system_map=system_map, name_map=name_map
    )


def _json_lines_to_by_measure(
    json_data: List[Dict[str, Any]],
    chord_counts: Dict[int, Dict[int, int]],
    part_to_staff: Optional[Dict[str, int]] = None,
) -> Tuple[Dict[int, Dict[int, List[str]]], List[Mismatch]]:
    """
    Convert line-by-line JSON (measure_start + part text per line) into by_measure[measure][staff_id] = tokens.
    Part keys can be "1", "2" (staff id) or names mapped via part_to_staff. Expands each line to syllables,
    distributes by chord count, then converts each measure's chunk back to tokens.

    Returns the by-measure tokens plus one `Mismatch` per (range, kind) whose syllable
    count did not match the chords available there.
    """
    if part_to_staff is None:
        part_to_staff = _DEFAULT_PART_TO_STAFF
    by_measure: Dict[int, Dict[int, List[str]]] = {}
    token_mismatches: List[Tuple[int, int, int, str, int, int]] = []  # (m_start, m_end, staff_id, kind, n_syl, slots)
    # Collect part keys from all rows so a part that appears only in later measures (e.g. "4") is not ignored
    all_part_keys = {k for row in json_data for k in row.keys() if k != "measure_start"}
    part_keys = sorted(
        all_part_keys,
        key=lambda k: (0, int(k)) if isinstance(k, str) and k.isdigit() else (1, str(k)),
    )
    for part_key in part_keys:
        # Allow numeric part names: "1", "2" etc. map directly to staff id
        if isinstance(part_key, str) and part_key.isdigit():
            staff_id = int(part_key)
        else:
            staff_id = part_to_staff.get(part_key)
        if staff_id is None:
            continue
        counts = chord_counts.get(staff_id, {})
        lines: List[Tuple[int, List[str]]] = []
        for row in json_data:
            m_start = row.get("measure_start")
            if m_start is None:
                continue
            text = row.get(part_key)
            if text is None or not isinstance(text, str):
                text = ""
            text = (text or "").strip()
            tokens = _tokenize_line(text)
            lines.append((int(m_start), tokens))
        lines.sort(key=lambda x: x[0])
        prev_trailing_hyphen = False
        for line_idx, (m_start, tokens) in enumerate(lines):
            # Use the next line that has content for this part as the exclusive end measure
            # (so we don't end the range at an empty row and cram too many syllables into one measure)
            next_start = None
            for j in range(line_idx + 1, len(lines)):
                if lines[j][1]:  # non-empty tokens
                    next_start = lines[j][0]
                    break
            if next_start is None:
                next_start = max(counts.keys()) + 1 if counts else m_start + 1
            syllables = _tokens_to_syllables(tokens, first_syllabic_continuation=prev_trailing_hyphen)
            prev_trailing_hyphen = _last_token_ends_with_hyphen(tokens)
            m_end = next_start
            syl_offset = 0
            last_m = None
            for m in range(m_start, m_end):
                if syl_offset >= len(syllables):
                    break
                n_slots = counts.get(m, 0)
                if n_slots <= 0:
                    continue
                chunk = syllables[syl_offset : syl_offset + n_slots]
                syl_offset += len(chunk)
                if chunk:
                    by_measure.setdefault(m, {})[staff_id] = _syllables_to_tokens(chunk)
                    last_m = m
            # Too many syllables: keep the excess instead of dropping it — append to the
            # last filled measure so import crams it onto that measure's last note (the
            # mismatch stays visible for the user to fix, rather than silently vanishing).
            if syl_offset < len(syllables) and last_m is not None:
                by_measure[last_m][staff_id].extend(_syllables_to_tokens(syllables[syl_offset:]))
                syl_offset = len(syllables)
            # Record mismatch when syllable count doesn't match chord slots in this line's range
            if tokens:
                total_slots = sum(counts.get(m, 0) for m in range(m_start, m_end))
                n_syllables = len(syllables)
                if n_syllables > total_slots:
                    token_mismatches.append((m_start, m_end, staff_id, TOO_MANY, n_syllables, total_slots))
                elif n_syllables < total_slots:
                    token_mismatches.append((m_start, m_end, staff_id, TOO_FEW, n_syllables, total_slots))
    # One record per distinct (range, kind), with every staff it affects listed.
    key_to_staffs: Dict[Tuple[int, int, str, int, int], List[int]] = {}
    for m_start, m_end, staff_id, kind, n_syl, slots in token_mismatches:
        key_to_staffs.setdefault((m_start, m_end, kind, n_syl, slots), []).append(staff_id)
    mismatches: List[Mismatch] = []
    for (m_start, m_end, kind, n_syl, slots), staff_ids in sorted(key_to_staffs.items()):
        ids = tuple(sorted(set(staff_ids)))
        staffs_str = ", ".join(str(s) for s in ids)
        if kind == TOO_MANY:
            message = (
                f"Measures {m_start}-{m_end - 1} (staffs {staffs_str}): too many tokens "
                f"({n_syl} syllables, {slots} slots); the extra are kept on the last note — fix the count."
            )
        else:
            message = (
                f"Measures {m_start}-{m_end - 1} (staffs {staffs_str}): too few tokens "
                f"({n_syl} syllables, {slots} slots); some slots will be empty."
            )
        mismatches.append(Mismatch(
            kind=kind, message=message,
            measure_start=m_start, measure_end=m_end - 1,
            staff_ids=ids, syllables=n_syl, slots=slots,
        ))
    return by_measure, mismatches


def _iter_voice0_chords(staff: etree._Element, division: int):
    """Yield (measure_index, chord_el, is_rest, is_slur_continuation) for voice 0 only."""
    staff_id = int(staff.get("id", "0"))
    measure_index = -1
    for measure in staff.findall(".//Measure"):
        measure_index += 1
        voices = measure.findall("voice")
        if not voices:
            continue
        voice = voices[0]
        time_pos = 0
        for el in voice:
            if el.tag == "Chord":
                slur_cont = _is_continuation_no_lyric(el)
                yield (measure_index, el, False, slur_cont)
                dur_el = el.find(".//durationType")
                dots_el = el.find(".//dots")
                dur = _resolve_duration_ticks(
                    dur_el.text if dur_el is not None and dur_el.text else "quarter",
                    dots_el.text if dots_el is not None and dots_el.text else "0",
                    division,
                )
                time_pos += dur
            elif el.tag == "Rest":
                yield (measure_index, el, True, False)
                dur_el = el.find(".//durationType")
                dots_el = el.find(".//dots")
                dur = _resolve_duration_ticks(
                    dur_el.text if dur_el is not None and dur_el.text else "quarter",
                    dots_el.text if dots_el is not None and dots_el.text else "0",
                    division,
                )
                time_pos += dur
            elif el.tag == "location":
                frac_el = el.find(".//fractions")
                if frac_el is not None and frac_el.text:
                    time_pos += _resolve_duration_ticks(frac_el.text, "0", division)


def _lyrics_by_measure_staff(score_root: etree._Element) -> Dict[int, Dict[int, List[str]]]:
    """Build by_measure_staff[measure_index][staff_id] = [tokens] from the score.

    Tokens are the TXT-format syllable tokens (begin/middle end with '-', '_' for an
    eligible chord with no lyric). Voice 0, verse 1; slur/tie-continuation notes are
    skipped. Shared by the TXT export and the manual-editor prefill.
    """
    _add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return {}
    staffs = score.findall(".//Staff") or score_root.findall(".//Staff")
    by_measure_staff: Dict[int, Dict[int, List[str]]] = {}
    for staff in staffs:
        staff_id = int(staff.get("id", "0"))
        measure_index = -1
        slur_active = False
        tie_active = False
        for measure in staff.findall(".//Measure"):
            measure_index += 1
            voices = measure.findall("voice")
            if not voices:
                continue
            voice = voices[0]
            measure_tokens: List[str] = []
            for el in voice:
                if el.tag == "Chord":
                    if _is_continuation_no_lyric(el):
                        if _is_slur_continuation(el):
                            slur_active = False
                        if _is_tie_continuation(el):
                            tie_active = False
                        continue
                    if slur_active and not _has_slur_start(el) and not _is_slur_continuation(el):
                        continue  # middle of slur: ineligible, no token
                    if tie_active and not _has_tie_start(el) and not _is_tie_continuation(el):
                        continue  # middle of tie: ineligible, no token
                    lyric = _get_verse1_lyric(el)
                    if lyric is not None:
                        syllabic, text = lyric
                        measure_tokens.append(_token_from_lyric(syllabic, text))
                    else:
                        measure_tokens.append("_")
                    if _has_slur_start(el):
                        slur_active = True
                    if _has_tie_start(el):
                        tie_active = True
            by_measure_staff.setdefault(measure_index, {})[staff_id] = measure_tokens
    return by_measure_staff


def export_lyrics(score_root: etree._Element) -> str:
    """
    Export lyrics from a MuseScore score element (root of parsed .mscx) to TXT format.
    Only voice 0, verse 1. Slur-continuation notes get no token.
    """
    by_measure_staff = _lyrics_by_measure_staff(score_root)
    lines: List[str] = []
    measure_indices = sorted(by_measure_staff.keys())
    for mi in measure_indices:
        lines.append(f"# Measure {mi + 1}")
        staff_ids = sorted(by_measure_staff[mi].keys())
        for sid in staff_ids:
            tokens = by_measure_staff[mi][sid]
            merged = _merge_tokens(tokens)
            n_syllables = len(tokens)
            lines.append(f"{sid} [{n_syllables}]: {merged}")
    return "\n".join(lines) if lines else ""


def _parse_txt(txt: str) -> List[Dict[str, Any]]:
    """
    Parse TXT format into a list of blocks: each has 'measure' (1-based) and 'staff_lines' { staff_id: list of tokens (split, not merged) }.
    """
    blocks: List[Dict[str, Any]] = []
    current_measure: Optional[int] = None
    staff_lines: Dict[int, List[str]] = {}

    for raw_line in txt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if current_measure is not None and staff_lines:
                blocks.append({"measure": current_measure, "staff_lines": staff_lines})
                staff_lines = {}
            m = re.match(r"#\s*Measure\s+(\d+)", line, re.IGNORECASE)
            if m:
                current_measure = int(m.group(1))
            continue
        colon = line.find(":")
        if colon < 0:
            continue
        left = line[:colon].strip()
        # Optional syllable count: "1 [2]" or "1"
        m_staff = re.match(r"^(\d+)(?:\s*\[\d+\])?$", left)
        if not m_staff:
            continue
        try:
            staff_id = int(m_staff.group(1))
        except ValueError:
            continue
        rest = line[colon + 1 :].strip()
        # Split on spaces but merge tokens that are hyphen-connected (syllables)
        tokens: List[str] = []
        for part in rest.split():
            if part == "_":
                tokens.append("_")
            elif tokens and tokens[-1].endswith("-"):
                tokens[-1] = tokens[-1].rstrip("-") + "-" + part
            else:
                tokens.append(part)
        staff_lines[staff_id] = tokens
    if current_measure is not None and staff_lines:
        blocks.append({"measure": current_measure, "staff_lines": staff_lines})
    return blocks


def _clear_verse1_lyrics(chord: etree._Element) -> None:
    """Remove all verse 1 Lyrics from chord (verse 1 = omit <no>)."""
    for lyrics in list(chord.findall(".//Lyrics")):
        if _is_verse1(lyrics.find("no")):
            chord.remove(lyrics)


def _set_lyric(chord: etree._Element, syllabic: str, text: str, no: str = "1") -> None:
    """Set or replace verse 1 lyric on chord. Verse 1 = omit <no>. Removes all existing verse 1 lyrics first."""
    for lyrics in list(chord.findall(".//Lyrics")):
        if _is_verse1(lyrics.find("no")):
            chord.remove(lyrics)
    lyric_el = etree.Element("Lyrics")
    s_el = etree.SubElement(lyric_el, "syllabic")
    s_el.text = syllabic
    t_el = etree.SubElement(lyric_el, "text")
    t_el.text = text
    # Verse 1: omit <no>. Do not add <no>1</no> (that would be verse 2).
    chord.append(lyric_el)


def _tokens_to_syllables(
    tokens: List[str], first_syllabic_continuation: bool = False
) -> List[Tuple[str, str]]:
    """
    Expand tokens (e.g. "il-man", "kuu-ta", "ja") into a list of (syllabic, text) per chord.

    A syllable's state is just how it joins its neighbours: whether a word carries on
    into it from the left, and whether it carries on out to the right. A trailing hyphen
    says "carries on", and that applies between any two tokens -- not only at a measure
    boundary. `first_syllabic_continuation` supplies the answer for the very first token,
    which has no predecessor here (the previous measure ended mid-word).

    Getting this wrong is not cosmetic: a syllable that continues a word but is written
    `end` (or worse, `single`) splits one word into two, so `lai-ne-hil-le` came back as
    `lai-ne-hil` plus a stray `le`.
    """
    out: List[Tuple[str, str]] = []
    continues_from_previous = first_syllabic_continuation
    for tok in tokens:
        if tok == "_":
            out.append(("_", ""))
            continues_from_previous = False
            continue
        raw_trailing_hyphen = tok.strip().endswith("-")
        # strip("-") not rstrip: a leading hyphen is how a caller writes "this continues
        # the previous line", and it must not survive into the syllable text.
        parts = [p.strip() for p in tok.strip().strip("-").split("-") if p.strip()]
        if not parts:
            continue
        for i, p in enumerate(parts):
            joins_left = continues_from_previous if i == 0 else True
            joins_right = (i < len(parts) - 1) or raw_trailing_hyphen
            if joins_left:
                out.append(("middle" if joins_right else "end", p))
            else:
                out.append(("begin" if joins_right else "single", p))
        continues_from_previous = raw_trailing_hyphen
    return out


def _last_token_ends_with_hyphen(tokens: List[str]) -> bool:
    """True if the last token (when not underscore) ends with a hyphen (syllabic begin/middle)."""
    if not tokens:
        return False
    last = tokens[-1].strip()
    return last != "_" and last.endswith("-")


def _syllables_to_tokens(syllables: List[Tuple[str, str]]) -> List[str]:
    """
    Convert (syllabic, text) pairs back to tokens (e.g. begin+end -> "a-b", single -> "a", begin-only -> "a-").

    A `middle` keeps its trailing hyphen. It is the only way this text can say the word
    is not finished, and the JSON import cuts a line into per-measure chunks -- so a
    chunk that begins mid-word used to come back bare and read as a fresh word.
    """
    tokens: List[str] = []
    for syllabic, text in syllables:
        if syllabic == "_":
            tokens.append("_")
            continue
        piece = text + "-" if syllabic in ("begin", "middle") else text
        if tokens and tokens[-1] != "_" and tokens[-1].endswith("-"):
            tokens[-1] = tokens[-1] + piece
        else:
            tokens.append(piece)
    return tokens


def _remove_verse2_plus(score_root: etree._Element) -> None:
    """Remove all Lyrics with <no> (verse 2 = no=1, verse 3 = no=2, ...) so only verse 1 (omit no) remains."""
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    for lyrics in list(score.findall(".//Lyrics")):
        if not _is_verse1(lyrics.find("no")):
            parent = lyrics.getparent()
            if parent is not None:
                parent.remove(lyrics)


def _count_remaining_eligible_chords(
    voice_children: List[etree._Element],
    from_index: int,
    slur_active: bool,
    tie_active: bool,
) -> int:
    """Count how many chords in voice_children[from_index:] will receive a lyric (same rules as import loop)."""
    count = 0
    sa, ta = slur_active, tie_active
    for i in range(from_index, len(voice_children)):
        el = voice_children[i]
        if el.tag != "Chord":
            continue
        if _is_continuation_no_lyric(el):
            if _is_slur_continuation(el):
                sa = False
            if _is_tie_continuation(el):
                ta = False
            continue
        if sa and not _has_slur_start(el) and not _is_slur_continuation(el):
            continue
        if ta and not _has_tie_start(el) and not _is_tie_continuation(el):
            continue
        count += 1
        if _has_slur_start(el):
            sa = True
        if _has_tie_start(el):
            ta = True
    return count


def _clear_all_verse1_lyrics(score_root: etree._Element) -> None:
    """Remove every verse 1 Lyrics element from the whole score (full-replace import)."""
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    for chord in score.findall(".//Chord"):
        _clear_verse1_lyrics(chord)


def _import_txt_into_mscx(
    score_root: etree._Element,
    txt: Optional[str] = None,
    by_measure: Optional[Dict[int, Dict[int, List[str]]]] = None,
    clear_existing: bool = False,
) -> LyricImport:
    """
    Import TXT lyrics into the score (in-place). Verse 1, voice 0. Slur-continuation chords are skipped (no lyric).
    Hyphenated tokens (e.g. il-man) are expanded to begin/end syllables on consecutive chords.
    If the previous measure's last syllable was begin/middle (trailing hyphen), the next measure's
    first syllable is set as "end". Verse 2 and higher are removed.

    Provide either txt (plain # Measure N / staff: tokens format) or by_measure (precomputed
    {measure_1based: {staff_id: [tokens]}}). If both are None, nothing is applied.

    clear_existing: when True, remove all verse 1 lyrics from the whole score before applying
    (full replace). Use when the score already has lyrics (e.g. OCR from MusicXML) that the
    imported lyrics should fully supersede. When False, measures/staves not in the input keep
    their existing lyrics (partial edit).
    """
    _add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return LyricImport()
    if clear_existing:
        _clear_all_verse1_lyrics(score_root)
    if by_measure is None:
        if txt is None:
            return LyricImport()
        blocks = _parse_txt(txt)
        by_measure = {b["measure"]: b["staff_lines"] for b in blocks}

    staffs = score.findall(".//Staff")
    if not staffs:
        staffs = score_root.findall(".//Staff")
    # Only process Staff elements that contain measures (skip Part/Staff layout stubs)
    staffs = [s for s in staffs if s.find(".//Measure") is not None]

    for staff in staffs:
        staff_id = int(staff.get("id", "0"))
        measure_index = -1
        slur_active = False
        tie_active = False
        for measure in staff.findall(".//Measure"):
            measure_index += 1
            one_based = measure_index + 1
            voices = measure.findall("voice")
            if not voices:
                continue
            voice = voices[0]
            voice_children = list(voice)
            # Whether we will place lyrics in this measure (partial JSON may omit measures)
            placing = (
                one_based in by_measure and staff_id in by_measure[one_based]
            )
            if placing:
                staff_tokens = by_measure[one_based][staff_id]
                prev_measure_tokens = (by_measure.get(one_based - 1) or {}).get(staff_id) or []
                first_syllabic_continuation = _last_token_ends_with_hyphen(prev_measure_tokens)
                syllables = _tokens_to_syllables(staff_tokens, first_syllabic_continuation=first_syllabic_continuation)
                syl_index = [0]
            else:
                syllables = []
                syl_index = [0]

            for el_idx, el in enumerate(voice_children):
                if el.tag != "Chord":
                    continue
                if _is_continuation_no_lyric(el):
                    if placing:
                        _clear_verse1_lyrics(el)
                    if _is_slur_continuation(el):
                        slur_active = False
                    if _is_tie_continuation(el):
                        tie_active = False
                    continue
                if slur_active and not _has_slur_start(el) and not _is_slur_continuation(el):
                    if placing:
                        _clear_verse1_lyrics(el)
                    continue  # middle of slur
                if tie_active and not _has_tie_start(el) and not _is_tie_continuation(el):
                    if placing:
                        _clear_verse1_lyrics(el)
                    continue  # middle of tie
                if not placing:
                    if _has_slur_start(el):
                        slur_active = True
                    if _has_tie_start(el):
                        tie_active = True
                    continue
                if syl_index[0] >= len(syllables):
                    for lyrics in list(el.findall(".//Lyrics")):
                        no_el = lyrics.find("no")
                        if _is_verse1(no_el):
                            el.remove(lyrics)
                    if _has_slur_start(el):
                        slur_active = True
                    if _has_tie_start(el):
                        tie_active = True
                    continue
                syllables_left = len(syllables) - syl_index[0]
                eligible_remaining = _count_remaining_eligible_chords(
                    voice_children, el_idx, slur_active, tie_active
                )
                if syllables_left > eligible_remaining and eligible_remaining > 0:
                    # Cram remaining syllables onto this chord so JSON can "force" text (e.g. öt-tä. in one slot)
                    chunk = syllables[syl_index[0] : syl_index[0] + syllables_left]
                    merged_tokens = _syllables_to_tokens(chunk)
                    merged_text = " ".join(merged_tokens).strip() if merged_tokens else ""
                    if merged_text:
                        _set_lyric(el, "single", merged_text, "1")
                    syl_index[0] += syllables_left
                else:
                    syllabic, text = syllables[syl_index[0]]
                    syl_index[0] += 1
                    if syllabic == "_":
                        for lyrics in list(el.findall(".//Lyrics")):
                            no_el = lyrics.find("no")
                            if _is_verse1(no_el):
                                el.remove(lyrics)
                    else:
                        _set_lyric(el, syllabic, text, "1")
                if _has_slur_start(el):
                    slur_active = True
                if _has_tie_start(el):
                    tie_active = True

    _remove_verse2_plus(score_root)
    # Clear verse 1 lyrics from any chord that is inside spanner (ineligible)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is not None:
        for chord in score.findall(".//Chord"):
            if _is_continuation_no_lyric(chord):
                _clear_verse1_lyrics(chord)
    return LyricImport()


def _apply_split_to_by_measure(
    by_measure: Dict[int, Dict[int, List[str]]],
    split: List[int],
) -> Dict[int, Dict[int, List[str]]]:
    """
    Expand by_measure so that each split part is duplicated to two consecutive staff ids.
    E.g. split [3, 4]: input part 3 -> output staffs 3 and 4 (same content), input part 4 -> staffs 5 and 6.
    """
    if not split:
        return by_measure
    new_by_measure: Dict[int, Dict[int, List[str]]] = {}
    for measure, staff_lines in by_measure.items():
        new_by_measure[measure] = {}
        for staff_id, tokens in staff_lines.items():
            if staff_id in split:
                i = split.index(staff_id)
                output_ids = [staff_id + i, staff_id + i + 1]
            else:
                output_ids = [staff_id]
            for out_id in output_ids:
                new_by_measure[measure][out_id] = list(tokens)
    return new_by_measure


def _fill_missing_measure_starts(
    json_str: str, score_root: etree._Element
) -> Tuple[str, List[Mismatch], List[int]]:
    """Infer null `measure_start` values from the score's printed systems.

    The JSON has one block per printed line (system) in order. Blocks whose
    `measure_start` is null (no measure number printed) are assigned the start
    measure of the system at the same position. Explicit values are left alone.
    Returns the (possibly rewritten) JSON string, any diagnostics, and the measure
    numbers that were filled in.
    """
    try:
        data = json.loads(json_str)
    except ValueError:
        return json_str, [], []
    if not isinstance(data, list):
        return json_str, [], []
    blocks = [b for b in data if isinstance(b, dict) and "measure_start" in b]
    if not any(b.get("measure_start") is None for b in blocks):
        return json_str, [], []  # nothing to infer

    starts = [r.start for r in system_ranges(score_root)]
    if not starts:
        return json_str, [Mismatch(
            kind=NO_SYSTEMS,
            message="Lyric lines have null measure_start but the score has no system "
                    "breaks to infer from; those lines were skipped.",
        )], []

    notes: List[Mismatch] = []
    filled: List[int] = []
    for i, block in enumerate(blocks):
        if block.get("measure_start") is None:
            if i < len(starts):
                block["measure_start"] = starts[i]
                filled.append(starts[i])
            else:
                notes.append(Mismatch(
                    kind=NO_SYSTEM_FOR_LINE,
                    message=f"Lyric line {i + 1} has null measure_start and there is no "
                            f"system {i + 1} to infer from; it was skipped.",
                ))
    if filled and len(blocks) != len(starts):
        notes.append(Mismatch(
            kind=BLOCK_COUNT,
            message=f"Auto-filled {len(filled)} null measure_start value(s), but the number "
                    f"of lyric lines ({len(blocks)}) doesn't match the score's systems "
                    f"({len(starts)}) — verify the alignment.",
        ))
    return json.dumps(data), notes, filled


def _import_json_txt_into_mscx(
    score_root: etree._Element,
    source: Any,
    part_to_staff: Optional[Dict[str, int]] = None,
    split: Optional[List[int]] = None,
    clear_existing: bool = False,
) -> LyricImport:
    """
    Import line-by-line JSON lyrics into the score. JSON format: array of objects with
    'measure_start' (measure number where the line starts) and either part keys whose values are
    lyric lines, or a 'lyrics' array of {text, staff_number, position, parts}. staff_number/position
    are mapped to output staves via the score's 'lyricsStaffMap' (written by clean_score); an
    explicit 'parts' list overrides that mapping.
    Part keys can be numeric ("1", "2", ...) for staff id directly, or names (e.g. S1, A1) mapped
    via part_to_staff (default S1->1, S2->2, A1->3, A2->4).
    If split is given (e.g. [3, 4]), those input parts are each duplicated to two staves:
    part 3 -> staffs 3 and 4, part 4 -> staffs 5 and 6.
    clear_existing: remove all existing verse 1 lyrics first (full replace).

    `source` is the JSON text or the already-parsed blocks (what the manual editor builds).
    """
    _add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return LyricImport()
    json_str = source if isinstance(source, str) else json.dumps(source)
    staff_map = _read_lyrics_staff_map(score_root)
    system_map = _read_lyrics_system_map(score_root)
    name_map = _read_part_name_map(score_root)
    json_str, notes, filled = _fill_missing_measure_starts(json_str, score_root)
    json_data = _parse_json_txt(
        json_str, staff_map=staff_map, system_map=system_map, name_map=name_map
    )
    if not json_data:
        return LyricImport(mismatches=notes, filled_measure_starts=filled)
    chord_counts = _get_chord_counts_per_measure(score)
    by_measure, mismatches = _json_lines_to_by_measure(json_data, chord_counts, part_to_staff)
    if split:
        by_measure = _apply_split_to_by_measure(by_measure, split)
    _import_txt_into_mscx(score_root, by_measure=by_measure, clear_existing=clear_existing)
    return LyricImport(mismatches=notes + mismatches, filled_measure_starts=filled)


def _add_rests_to_empty_measures(score_root: etree._Element) -> None:
    """
    Add a full-measure rest to any voice that has no Chord and no Rest in that measure.
    Modifies the score in place. Uses the measure's time signature (or 4/4 if none).
    """
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    staffs = score.findall(".//Staff")
    if not staffs:
        staffs = score_root.findall(".//Staff")
    for staff in staffs:
        time_sig_n = 4
        time_sig_d = 4
        for measure in staff.findall(".//Measure"):
            time_sig_el = measure.find(".//TimeSig")
            if time_sig_el is not None:
                sn = time_sig_el.find("sigN")
                sd = time_sig_el.find("sigD")
                if sn is not None and sn.text and sd is not None and sd.text:
                    try:
                        time_sig_n = int(sn.text.strip())
                        time_sig_d = int(sd.text.strip())
                    except ValueError:
                        pass
            duration_type = f"{time_sig_n}/{time_sig_d}"
            voices = measure.findall("voice")
            for voice in voices:
                has_chord_or_rest = any(
                    el.tag in ("Chord", "Rest") for el in voice
                )
                if has_chord_or_rest:
                    continue
                rest = etree.Element("Rest")
                dt = etree.SubElement(rest, "durationType")
                dt.text = duration_type
                voice.append(rest)
            if not voices:
                voice = etree.Element("voice")
                rest = etree.Element("Rest")
                dt = etree.SubElement(rest, "durationType")
                dt.text = duration_type
                voice.append(rest)
                measure.append(voice)


# --------------------------------------------------------------------------- #
# The interface: placement, the editor's projection, and the file adapters
# --------------------------------------------------------------------------- #

def place_lyrics(
    score_root: etree._Element,
    source: Any,
    fmt: Optional[str] = None,
    replace: bool = False,
    split: Optional[List[int]] = None,
    part_to_staff: Optional[Dict[str, int]] = None,
) -> LyricImport:
    """Put lyrics into the score, in place, and report what did not fit.

    `source` is TXT, JSON text, or the already-parsed JSON blocks the manual editor
    builds; `fmt` ("txt"/"json") overrides the format sniff. `replace` clears every
    verse-1 lyric first (a full replace); without it, measures and staves the source
    does not name keep what they have. `split` duplicates a part onto two staves
    (JSON only). Returns the mismatches — nothing is written to stdout or stderr.

    Only the JSON path measures syllables against chords, so a TXT import always
    reports an empty result (TXT is written per measure, so it cannot drift).
    """
    if fmt is None:
        fmt = "txt" if isinstance(source, str) and not source.lstrip().startswith("[") else "json"
    if fmt == "json":
        return _import_json_txt_into_mscx(
            score_root, source, part_to_staff=part_to_staff,
            split=split, clear_existing=replace,
        )
    return _import_txt_into_mscx(score_root, txt=source, clear_existing=replace)


# Staves that carry no lyrics: the click/spacer track added for recording.
_NON_LYRIC_PART_WORDS = ("drum", "click", "rest")


def slot_counts(score_root: etree._Element) -> Dict[int, Dict[int, int]]:
    """`[staff_id][measure] = how many notes there take a syllable`.

    The arithmetic that checks a reading of the printed page. A line whose
    syllables do not match these counts is either mis-assigned or the score is
    missing a slur, and which one it is follows from the direction: too few
    syllables means notes without words, too many means the reading is wrong.
    Same eligibility as the TXT export -- voice 0, verse 1, no rests, and no
    slur or tie continuations.
    """
    return _get_chord_counts_per_measure(
        score_root.find(".//Score") if score_root.tag != "Score" else score_root
    )


def editor_grid(
    score_root: etree._Element,
    systems: Optional[List[Tuple[int, int]]] = None,
) -> EditorGrid:
    """The manual editor's projection: one text cell per (printed system, output part).

    Parts come from the score's own track names (click/spacer staves excluded), systems
    from the printed line breaks, and each cell is prefilled with the lyrics already in
    the score for that part over that system's measures — the same syllable rules the
    TXT export uses, so what the editor shows round-trips back through `place_lyrics`.

    `systems` overrides the line breaks with explicit (start, end) measure ranges. It is
    needed because normal-mode cleaning strips layout breaks, so the cleaned score has
    no systems left to find and the whole piece collapses into one cell per part. The
    printed systems still exist -- on the page -- and are supplied from there.
    """
    score = score_root.find(".//Score") if score_root.tag != "Score" else score_root
    parts: List[EditorPart] = []
    for p in score.findall("Part"):
        st = p.find("Staff")
        sid = int(st.get("id")) if st is not None and st.get("id") else 0
        name = (p.findtext("trackName") or p.findtext("Instrument/trackName") or "").strip()
        if any(w in name.lower() for w in _NON_LYRIC_PART_WORDS):
            continue
        parts.append(EditorPart(id=sid, name=name or f"staff {sid}"))
    parts.sort(key=lambda p: p.id)

    if systems:
        ranges = [EditorSystem(index=i, start=a, end=b)
                  for i, (a, b) in enumerate(systems)]
    else:
        ranges = [EditorSystem(index=r.index, start=r.start, end=r.end)
                  for r in system_ranges(score_root)]
    systems = ranges
    by_measure = _lyrics_by_measure_staff(score_root)
    counts = slot_counts(score_root)
    cells: Dict[int, Dict[str, str]] = {}
    capacities: Dict[int, Dict[str, int]] = {}
    for system in systems:
        for part in parts:
            capacities.setdefault(system.index, {})[part.name] = sum(
                counts.get(part.id, {}).get(mi, 0)
                for mi in range(system.start, system.end + 1)
            )
            tokens: List[str] = []
            for mi in range(system.start - 1, system.end):
                tokens += [t for t in by_measure.get(mi, {}).get(part.id, []) if t != "_"]
            text = _merge_tokens(tokens).strip()
            if text:
                cells.setdefault(system.index, {})[part.name] = text
    return EditorGrid(parts=parts, systems=systems, cells=cells, capacities=capacities)


def blocks_from_cells(
    grid: EditorGrid, cells: Dict[Any, Dict[str, str]]
) -> List[Dict[str, Any]]:
    """Turn the editor's typed cells into lyric JSON blocks, one per printed system.

    `cells` is {system index: {part name: text}} as typed. Blank cells are left out —
    this editor expresses a lyric line starting in a system, not an instruction to
    clear one isolated cell — and each line addresses its part by NAME, which is
    immune to printed-staff order. The result is the same JSON `place_lyrics` takes
    from a paste, so both editors go through one path.
    """
    blocks: List[Dict[str, Any]] = []
    for system in grid.systems:
        typed = cells.get(system.index, cells.get(str(system.index), {})) or {}
        lyrics = [
            {"parts": [part.name], "text": typed[part.name].strip()}
            for part in grid.parts
            if isinstance(typed.get(part.name), str) and typed[part.name].strip()
        ]
        # Cells naming a part the score doesn't have simply never match a grid part.
        if lyrics:
            blocks.append({"measure_start": system.start, "lyrics": lyrics})
    return blocks


def _load_mscx(path: str) -> etree._Element:
    """Load .mscx file and return root element."""
    with open(path, "r", encoding="utf-8") as f:
        return etree.fromstring(f.read().encode("utf-8"))


def _save_mscx(root: etree._Element, path: str) -> None:
    """Write score XML to file."""
    with open(path, "wb") as f:
        f.write(etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True))


def export_file(mscx_path: str, txt_path: str) -> None:
    """Export lyrics from an .mscx file to a .txt file."""
    root = _load_mscx(mscx_path)
    txt = export_lyrics(root)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)


def import_file(
    txt_path: str,
    mscx_path_in: str,
    mscx_path_out: str,
    split: Optional[List[int]] = None,
    replace: bool = False,
) -> LyricImport:
    """Import lyrics from .txt or .json into a copy of the .mscx file. split only applies to .json (e.g. [3, 4]).
    replace=True clears all existing verse 1 lyrics first (full replace).
    Returns the placement result, so a caller can report mismatches without reading logs."""
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()
    root = _load_mscx(mscx_path_in)
    result = place_lyrics(
        root, content,
        fmt="json" if txt_path.lower().endswith(".json") else "txt",
        replace=replace, split=split,
    )
    _save_mscx(root, mscx_path_out)
    return result
