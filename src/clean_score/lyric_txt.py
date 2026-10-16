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
The TXT format is verse 1 of the first voice. Rests get no token.

JSON format (line-by-line): array of objects with "measure_start" (int) and part keys (e.g. S1, S2, A1, A2) whose values are lyric lines, or a "lyrics" array whose lines may name a "verse" and a "voice" (#397). Tokens are distributed across measures using the score. Use a .json path with import_file to import this format.

An import changes only what it names: the parts, voices, verses and printed systems
its lines cover. Every other word in the score stays (#392, #397).

This module owns lyric placement end to end — format normalization, target routing,
chord eligibility, syllable distribution, XML placement and the diagnostics that come
out of it — for all three callers: the CLI file adapters, the AI-JSON paste, and the
song app's manual editor. Its interface:

    export_lyrics(root) -> str                     the TXT projection of the score
    place_lyrics(root, source, replace=, split=)   put lyrics in; returns LyricImport
    editor_grid(root) -> EditorGrid                what the manual editor renders
    slot_counts(root) -> {staff: {measure: n}}     notes that take a syllable
    blocks_from_boxes(grid, verse, boxes)          the by-system editor's changed boxes
    printed_staves(root, start, end)               a system's printed staves
    blocks_from_cells(grid, cells) -> [block]      the older editor's cells as lyric JSON
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
from .utils.utils import part_staff_ids

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
    verse: int = 1
    voice: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "measure_start": self.measure_start,
            "measure_end": self.measure_end,
            "staff_ids": list(self.staff_ids),
            "syllables": self.syllables,
            "slots": self.slots,
            "verse": self.verse,
            "voice": self.voice,
        }


@dataclass(frozen=True)
class Written:
    """Bars an import rewrote the words of: one part, voice and verse (#397).

    A caller keeping earlier warnings drops the ones these bars answer, since an
    import now changes only what it names and the rest of the score keeps its words.
    """

    staff: int
    voice: int
    verse: int
    start: int          # 1-based inclusive
    end: int

    def to_dict(self) -> Dict[str, Any]:
        return {"staff": self.staff, "voice": self.voice, "verse": self.verse,
                "start": self.start, "end": self.end}


@dataclass(frozen=True)
class LyricImport:
    """The outcome of placing lyrics: what was placed, and what did not fit."""

    mismatches: List[Mismatch] = field(default_factory=list)
    filled_measure_starts: List[int] = field(default_factory=list)
    written: List[Written] = field(default_factory=list)

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
class EditorBox:
    """One box of the editor: a part's voice in one printed system (#397).

    `key` is the part's staff id for voice 1 and `id@2` for its second voice: a
    name would not do, since two parts can share one. `text`
    holds every verse's words, so changing the verse tab needs no new request.
    `printed` is the printed staff the part sits on in this system (from the
    clean's part-to-staff record), or None when the score does not say.
    """

    key: str
    part: str
    id: int
    voice: int
    slots: int
    printed: Optional[int]
    text: Dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "part": self.part, "id": self.id, "voice": self.voice,
                "slots": self.slots, "printed": self.printed,
                "text": {str(v): t for v, t in self.text.items()}}


@dataclass(frozen=True)
class EditorGrid:
    """The manual editor's projection: a text cell per (printed system, part).

    `cells` and `capacities` are verse 1 of each part's first voice, as before;
    `boxes` is the whole of it, every voice that sings in a system with every
    verse's words, and `verses` how many verses the score has.
    """

    parts: List[EditorPart] = field(default_factory=list)
    systems: List[EditorSystem] = field(default_factory=list)
    cells: Dict[int, Dict[str, str]] = field(default_factory=dict)
    capacities: Dict[int, Dict[str, int]] = field(default_factory=dict)
    verses: List[int] = field(default_factory=lambda: [1])
    boxes: Dict[int, List[EditorBox]] = field(default_factory=dict)

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
            "verses": list(self.verses),
            "boxes": {str(si): [b.to_dict() for b in boxes]
                      for si, boxes in self.boxes.items()},
        }

# Default mapping from JSON part keys (e.g. S1, A2) to staff id. Overridable.
_DEFAULT_PART_TO_STAFF: Dict[str, int] = {"S1": 1, "S2": 2, "A1": 3, "A2": 4}


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


def _after_continuation(chord: etree._Element, slur_active: bool, tie_active: bool):
    """(slur_active, tie_active) after a continuation chord, which takes no syllable.

    It closes the slur or tie it ends, and opens any it starts: a melisma can begin on
    the note a tie carries over the barline, or on the note another slur ends on.
    Checking only the close lost the new slur, and the notes under it took syllables.
    """
    if _is_slur_continuation(chord):
        slur_active = False
    if _is_tie_continuation(chord):
        tie_active = False
    if _has_slur_start(chord):
        slur_active = True
    if _has_tie_start(chord):
        tie_active = True
    return slur_active, tie_active


def _is_verse1(no_el: Optional[etree._Element]) -> bool:
    """Verse 1 = omit <no> (no element or empty). <no>1</no> = verse 2."""
    if no_el is None:
        return True
    return ((no_el.text or "").strip() == "") if no_el.text is not None else True


def _get_verse1_lyric(
    chord: etree._Element, verse2_fallback: bool = False
) -> Optional[Tuple[str, str]]:
    """Returns (syllabic, text) for verse 1 (omit no). None if the chord has none.

    With verse2_fallback, a chord with no verse 1 reads its verse 2 (no=1) instead: some
    scores arrive with all their words stored as verse 2. The caller allows that only
    when the score has no verse 1 anywhere. An import keeps verses 2+ (#392), so reading
    verse 2 under a blank verse 1 in an imported score would put its words in the
    editor, and the next import would copy them into verse 1.
    """
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
    if verse2_fallback and verse2 is not None:
        return verse2
    return verse1


def _has_verse1(score: etree._Element) -> bool:
    """True if any Lyrics in the score is verse 1."""
    return any(_is_verse1(ly.find("no")) for ly in score.iter("Lyrics"))


def _verse_of(lyrics: etree._Element) -> int:
    """A Lyrics element's verse, 1-based. MuseScore stores `<no>` from 0 and omits it for verse 1."""
    no = (lyrics.findtext("no") or "").strip()
    return int(no) + 1 if no.isdigit() else 1


def _get_lyric(
    chord: etree._Element, verse: int, verse2_fallback: bool = False
) -> Optional[Tuple[str, str]]:
    """(syllabic, text) of one verse on a chord, or None. See `_get_verse1_lyric`."""
    if verse == 1:
        return _get_verse1_lyric(chord, verse2_fallback)
    for lyrics in chord.findall("Lyrics"):
        if _verse_of(lyrics) == verse:
            syllabic = (lyrics.findtext("syllabic") or "").strip() or "single"
            return syllabic, (lyrics.findtext("text") or "").strip()
    return None


def _clear_lyrics(chord: etree._Element, verse: int) -> None:
    """Remove one verse's Lyrics from a chord; the other verses stay."""
    for lyrics in list(chord.findall(".//Lyrics")):
        if _verse_of(lyrics) == verse:
            chord.remove(lyrics)


def _verses_in(score: etree._Element) -> List[int]:
    """1 .. the highest verse any word in the score is on."""
    top = max((_verse_of(ly) for ly in score.iter("Lyrics")), default=1)
    return list(range(1, top + 1))


def _voice_track(measure: etree._Element, voice: int) -> Optional[etree._Element]:
    """A measure's `<voice>` for a 1-based voice number, or None."""
    voices = measure.findall("voice")
    return voices[voice - 1] if len(voices) >= voice else None


def _walk_voice(staff: etree._Element, voice: int = 1):
    """Yield (1-based measure, chord, takes a syllable) for every chord of one voice.

    Eligibility is *stateful along the staff* — a slur opened in one bar silences
    notes in the next — so it is decided here once, for every reader and writer.
    No chord is yielded for a Rest, and a slur or tie continuation, or a note in
    the middle of one, takes no syllable. A second voice follows the same rules on
    its own notes (#397). Reads only the spanners, so placing words cannot change it.
    """
    slur_active = False
    tie_active = False
    for mi, measure in enumerate(staff.findall("Measure"), start=1):
        track = _voice_track(measure, voice)
        if track is None:
            continue
        for el in track:
            if el.tag != "Chord":
                continue
            if _is_continuation_no_lyric(el):
                slur_active, tie_active = _after_continuation(el, slur_active, tie_active)
                yield mi, el, False
                continue
            if slur_active and not _has_slur_start(el) and not _is_slur_continuation(el):
                yield mi, el, False
                continue
            if tie_active and not _has_tie_start(el) and not _is_tie_continuation(el):
                yield mi, el, False
                continue
            yield mi, el, True
            if _has_slur_start(el):
                slur_active = True
            if _has_tie_start(el):
                tie_active = True


def _music_staves(score: etree._Element) -> List[etree._Element]:
    """The Staff elements that hold measures (not the Part-level stubs)."""
    return [s for s in score.findall(".//Staff") if s.find("Measure") is not None]


def _token_from_lyric(syllabic: str, text: str) -> str:
    """One token string; may end with '-' for begin/middle. Ineligibility (spanner, verse != 1) is handled by caller."""
    t = (text or "").strip()
    suffix = ""
    if syllabic in ("begin", "middle"):
        suffix = "-"
    return t + suffix


def _merge_tokens(tokens: List[str]) -> str:
    """Merge hyphenated syllables and join with space.

    An `_` is a note with no syllable, never part of a word: a trailing hyphen
    says the word carries on to the next *syllable*, and the empty slot is not
    one. Absorbing it produced `kär-_ si-net`, which reads back as a syllable
    literally spelled "_" and loses the slot it was standing for.
    """
    if not tokens:
        return ""
    result: List[str] = []
    cur = tokens[0]
    for i in range(1, len(tokens)):
        nxt = tokens[i]
        if cur == "_" or nxt == "_":
            result.append(cur)
            cur = nxt
        elif cur.endswith("-"):
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


def _eligibility_per_measure(
    score: etree._Element, voice: int = 1
) -> Dict[int, Dict[int, List[bool]]]:
    """
    Return by_staff[staff_id][measure_1based] = one flag per chord of `voice`, in
    order: does that chord take a syllable? Read off `_walk_voice`, so the per-measure
    counts and the per-note flags cannot disagree. A measure with the voice present
    but no chords in it reads as an empty list.
    """
    out: Dict[int, Dict[int, List[bool]]] = {}
    for staff in _music_staves(score):
        staff_id = int(staff.get("id", "0"))
        flags = out.setdefault(staff_id, {})
        for mi, measure in enumerate(staff.findall("Measure"), start=1):
            if _voice_track(measure, voice) is not None:
                flags[mi] = []
        for mi, _chord, eligible in _walk_voice(staff, voice):
            flags.setdefault(mi, []).append(eligible)
    return out


def _get_chord_counts_per_measure(
    score: etree._Element, voice: int = 1
) -> Dict[int, Dict[int, int]]:
    """
    Return by_staff[staff_id][measure_1based] = number of lyric-eligible chords of `voice`.
    """
    return {staff_id: {measure: sum(flags) for measure, flags in by_measure.items()}
            for staff_id, by_measure in _eligibility_per_measure(score, voice).items()}


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
                    follow = {
                        int(k): [int(x) for x in v]
                        for k, v in (entry.get("follow") or {}).items()
                    }
                    result.append(
                        {"start": int(entry["start"]), "end": int(entry["end"]),
                         "map": pmap, "follow": follow}
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


def _follow_for_measure(
    system_map: Optional[List[Dict[str, Any]]], measure_start: int
) -> Dict[int, List[int]]:
    """{base id: [ids singing its notes here]} for the system covering measure_start.

    A per-system `S1b` left out of a system sings `S1`'s notes there, so it takes
    `S1`'s words too — unless the block gives it words of its own.
    """
    for entry in system_map or []:
        if entry["start"] <= measure_start <= entry["end"]:
            return entry.get("follow") or {}
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
    for part, owned in zip(score.findall("Part"), part_staff_ids(score)):
        name = (part.findtext("trackName") or "").strip()
        if name and owned:
            result[name.upper()] = int(owned[0])
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


@dataclass(frozen=True)
class _Line:
    """One lyric line: one part's words on one voice and verse, from a bar on.

    `end` is the last bar of the system the line was written for, when the source
    says (the editor always does). A line with no syllables in it clears that
    system: emptying a box is how a person takes words out (#393).
    """

    start: int
    end: Optional[int]
    staff: int
    voice: int
    verse: int
    text: str

    @property
    def key(self) -> Tuple[int, int, int]:
        return (self.staff, self.voice, self.verse)


def _int_or(value: Any, default: Any) -> Any:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _block_lines(
    block: Dict[str, Any],
    measure_start: int,
    measure_end: Optional[int],
    staff_map: Dict[int, List[int]],
    system_map: Optional[List[Dict[str, Any]]],
    name_map: Dict[str, int],
) -> List[_Line]:
    """The lines one `{measure_start, lyrics: [...]}` block names.

    Target output staves are resolved in priority order:
      1. explicit "part"/"parts" — a list (or scalar) of output staff ids and/or part
         NAMES ("T1", "B"); names map via `name_map` (the score's trackNames). Robust to
         printed-staff order, so this is preferred for per-system scores.
      2. otherwise (staff_number, position) via the staff map. With `system_map`
         (clean_score --per-system) the printed numbering shifts per system, so each
         block uses the map for the system covering its measure_start; else the single
         `staff_map` ('lyricsStaffMap') is used for all blocks.
    Each lyric may say its `verse` and `voice` (both default 1, #397). Lyrics of one
    block that land on the same part, voice and verse are space-joined.
    """
    block_map = (
        _map_for_measure(system_map, measure_start)
        if system_map is not None
        else staff_map
    )
    block_lyrics = [ly for ly in (block.get("lyrics") or []) if isinstance(ly, dict)]
    # Positions used per printed staff and verse in THIS block (decides divisi vs
    # unison locally; a second verse's line is not the other singer's).
    positions: Dict[Tuple[int, int], set] = {}
    for lyric in block_lyrics:
        sn = lyric.get("staff_number")
        if isinstance(sn, int):
            positions.setdefault((sn, _int_or(lyric.get("verse"), 1)), set()).add(
                lyric.get("position"))
    texts: Dict[Tuple[int, int, int], List[str]] = {}
    for lyric in block_lyrics:
        verse = _int_or(lyric.get("verse"), 1)
        voice = _int_or(lyric.get("voice"), 1)
        if verse < 1 or voice < 1:
            continue
        text = lyric.get("text")
        text = text.strip() if isinstance(text, str) else ""
        parts = lyric.get("parts")
        if parts is None and lyric.get("part") is not None:
            parts = lyric.get("part")
        if not isinstance(parts, list):
            parts = [parts] if parts is not None else []
        if parts:
            targets = _resolve_targets(parts, name_map)
        else:
            sn = lyric.get("staff_number")
            targets = _derive_target_staves(
                sn, lyric.get("position"), block_map,
                positions.get((sn, verse), set()) if isinstance(sn, int) else set(),
            )
        for staff in targets:
            texts.setdefault((staff, voice, verse), []).append(text)
    for base, followers in _follow_for_measure(system_map, measure_start).items():
        for (staff, voice, verse), words in list(texts.items()):
            if staff != base:
                continue
            for child in followers:
                texts.setdefault((child, voice, verse), list(words))
    return [_Line(measure_start, measure_end, staff, voice, verse,
                  " ".join(t for t in words if t))
            for (staff, voice, verse), words in sorted(texts.items())]


def _lines_from_blocks(
    data: List[Any],
    staff_map: Optional[Dict[int, List[int]]] = None,
    system_map: Optional[List[Dict[str, Any]]] = None,
    name_map: Optional[Dict[str, int]] = None,
    part_to_staff: Optional[Dict[str, int]] = None,
) -> List[_Line]:
    """Every line a lyric JSON names, in either of its two shapes.

    The PDF-derived shape is `{measure_start, lyrics: [{text, parts | staff_number +
    position, verse, voice}]}` (see `_block_lines`). The older one keys a block's
    lines by part — `{"measure_start": 1, "S1": "...", "3": "..."}` — numeric keys
    being staff ids and names going through `part_to_staff` (default S1->1, S2->2,
    A1->3, A2->4); it is verse 1 of the first voice. A part a block does not name
    has no line there at all, which is not the same as an empty one.
    """
    staff_map = staff_map or {}
    name_map = name_map or {}
    part_to_staff = part_to_staff or _DEFAULT_PART_TO_STAFF
    lines: List[_Line] = []
    for block in data or []:
        if not isinstance(block, dict) or block.get("measure_start") is None:
            continue
        start = _int_or(block.get("measure_start"), None)
        if start is None:
            continue
        end = _int_or(block.get("measure_end"), None)
        if isinstance(block.get("lyrics"), list):
            lines += _block_lines(block, start, end, staff_map, system_map, name_map)
            continue
        for key, text in block.items():
            if key in ("measure_start", "measure_end"):
                continue
            staff = int(key) if isinstance(key, str) and key.isdigit() else part_to_staff.get(key)
            if staff is None:
                continue
            lines.append(_Line(start, end, staff, 1, 1, text if isinstance(text, str) else ""))
    return lines


def _measure_count(score: etree._Element) -> int:
    return max((len(s.findall("Measure")) for s in _music_staves(score)), default=0)


def _bars_with_words(score: etree._Element, key: Tuple[int, int, int]) -> set:
    """The bars where one part's voice already carries words of one verse."""
    staff_id, voice, verse = key
    fallback = verse == 1 and not _has_verse1(score)
    for staff in _music_staves(score):
        if int(staff.get("id", "0")) != staff_id:
            continue
        return {m for m, chord, eligible in _walk_voice(staff, voice)
                if eligible and (_get_lyric(chord, verse, fallback) or ("", ""))[1]}
    return set()


def _lines_to_by_measure(
    lines: List[_Line],
    score: etree._Element,
    systems: Optional[List[Tuple[int, int]]] = None,
) -> Tuple[Dict[int, Dict[Tuple[int, int, int], List[str]]], List[Mismatch], List[Written]]:
    """Cut each line into per-bar tokens, and say what did not fit.

    Returns by_measure[measure][(staff, voice, verse)] = tokens — an empty list is a
    bar whose words go — plus one `Mismatch` per range whose syllables did not match
    the notes, and the bars each line rewrote.

    A line fills its own system, and runs on into the next one only while that
    system's box stays empty: it is not named by this import and has no words in the
    score, or this import empties it (#393). So an import changes only what it names,
    and a part's words in a later system stop a line that runs long. A line's system
    is `systems` (the printed ones) when the caller knows them, else the block starts.
    The syllables are counted against the systems the line reached.
    """
    last = _measure_count(score)
    if systems:
        cuts = sorted({a for a, _ in systems})
    else:
        cuts = sorted({ln.start for ln in lines} | {ln.end + 1 for ln in lines if ln.end})

    def system_end(start: int) -> int:
        later = [c for c in cuts if c > start]
        return min(later[0] - 1, last) if later else last

    counts_by_voice: Dict[int, Dict[int, Dict[int, int]]] = {}
    by_measure: Dict[int, Dict[Tuple[int, int, int], List[str]]] = {}
    found: List[Tuple[int, int, Tuple[int, int, int], str, int, int]] = []
    written: List[Written] = []

    by_key: Dict[Tuple[int, int, int], List[_Line]] = {}
    for ln in lines:
        by_key.setdefault(ln.key, []).append(ln)
    for key, key_lines in sorted(by_key.items()):
        staff, voice, verse = key
        if voice not in counts_by_voice:
            counts_by_voice[voice] = _get_chord_counts_per_measure(score, voice)
        counts = counts_by_voice[voice].get(staff, {})
        key_lines.sort(key=lambda ln: ln.start)
        tokens_of = {id(ln): _tokenize_line(ln.text) for ln in key_lines}
        has_words = lambda ln: any(t != "_" for t in tokens_of[id(ln)])
        sent = {ln.start: ln for ln in key_lines}
        words_now: Optional[set] = None

        def own_end(ln: _Line) -> int:
            return min(ln.end, last) if ln.end else system_end(ln.start)

        def end_known(ln: _Line) -> bool:
            # A line with no system known to it (a TXT-like JSON naming one bar to
            # start from) rewrites only the bars its words reach.
            return bool(ln.end) or any(c > ln.start for c in cuts)

        # Emptied boxes first, so a line running long can still flow into one.
        for ln in key_lines:
            if has_words(ln):
                continue
            end = own_end(ln)
            for m in range(ln.start, end + 1):
                by_measure.setdefault(m, {})[key] = []
            written.append(Written(staff, voice, verse, ln.start, end))

        prev_trailing_hyphen = False
        for ln in key_lines:
            if not has_words(ln):
                continue
            tokens = tokens_of[id(ln)]
            syllables = _tokens_to_syllables(tokens, first_syllabic_continuation=prev_trailing_hyphen)
            prev_trailing_hyphen = _last_token_ends_with_hyphen(tokens)
            # The systems this line may fill: its own, then each next one whose box is empty.
            segments = [(ln.start, own_end(ln))]
            while segments[-1][1] < last:
                nxt = segments[-1][1] + 1
                ahead = sent.get(nxt)
                if ahead is not None and has_words(ahead):
                    break
                seg = (nxt, system_end(nxt))
                if ahead is None:
                    if words_now is None:
                        words_now = _bars_with_words(score, key)
                    if any(seg[0] <= m <= seg[1] for m in words_now):
                        break
                segments.append(seg)
            syl_offset = 0
            last_m = None
            for m in range(ln.start, segments[-1][1] + 1):
                if syl_offset >= len(syllables):
                    break
                n_slots = counts.get(m, 0)
                if n_slots <= 0:
                    continue
                chunk = syllables[syl_offset : syl_offset + n_slots]
                syl_offset += len(chunk)
                by_measure.setdefault(m, {})[key] = _syllables_to_tokens(chunk)
                last_m = m
            # Too many syllables: keep the excess instead of dropping it — append to the
            # last filled measure so import crams it onto that measure's last note (the
            # mismatch stays visible for the user to fix, rather than silently vanishing).
            if syl_offset < len(syllables) and last_m is not None:
                by_measure[last_m][key].extend(_syllables_to_tokens(syllables[syl_offset:]))
            reached = segments[0][1]
            for a, b in segments:
                if last_m is not None and last_m >= a:
                    reached = b
            cleared = reached if end_known(ln) else (last_m or ln.start)
            for m in range(ln.start, cleared + 1):
                by_measure.setdefault(m, {}).setdefault(key, [])
            written.append(Written(staff, voice, verse, ln.start, cleared))
            total_slots = sum(counts.get(m, 0) for m in range(ln.start, reached + 1))
            n_syllables = len(syllables)
            if n_syllables > total_slots:
                found.append((ln.start, reached, key, TOO_MANY, n_syllables, total_slots))
            elif n_syllables < total_slots:
                found.append((ln.start, reached, key, TOO_FEW, n_syllables, total_slots))

    # One record per distinct (range, kind, voice, verse), with every staff it affects.
    key_to_staffs: Dict[Tuple[int, int, str, int, int, int, int], List[int]] = {}
    for m_start, m_end, (staff, voice, verse), kind, n_syl, slots in found:
        key_to_staffs.setdefault((m_start, m_end, kind, n_syl, slots, verse, voice), []).append(staff)
    mismatches: List[Mismatch] = []
    for (m_start, m_end, kind, n_syl, slots, verse, voice), staff_ids in sorted(key_to_staffs.items()):
        ids = tuple(sorted(set(staff_ids)))
        where = ", ".join(str(s) for s in ids)
        where += f", verse {verse}" if verse > 1 else ""
        where += f", voice {voice}" if voice > 1 else ""
        if kind == TOO_MANY:
            message = (
                f"Measures {m_start}-{m_end} (staffs {where}): too many tokens "
                f"({n_syl} syllables, {slots} slots); the extra words are kept on the last note and extra _ are dropped — fix the count."
            )
        else:
            message = (
                f"Measures {m_start}-{m_end} (staffs {where}): too few tokens "
                f"({n_syl} syllables, {slots} slots); some slots will be empty."
            )
        mismatches.append(Mismatch(
            kind=kind, message=message,
            measure_start=m_start, measure_end=m_end,
            staff_ids=ids, syllables=n_syl, slots=slots, verse=verse, voice=voice,
        ))
    return by_measure, mismatches, written


def _lyrics_by_measure_staff(
    score_root: etree._Element, voice: int = 1, verse: int = 1
) -> Dict[int, Dict[int, List[str]]]:
    """Build by_measure_staff[measure_index][staff_id] = [tokens] from the score.

    Tokens are the TXT-format syllable tokens (begin/middle end with '-', '_' for an
    eligible chord with no lyric). One voice and verse (voice 1, verse 1 by default);
    slur/tie-continuation notes are skipped. Shared by the TXT export and the
    manual-editor prefill.
    """
    _add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return {}
    verse2_fallback = verse == 1 and not _has_verse1(score)
    by_measure_staff: Dict[int, Dict[int, List[str]]] = {}
    for staff in _music_staves(score):
        staff_id = int(staff.get("id", "0"))
        for mi, measure in enumerate(staff.findall("Measure")):
            if _voice_track(measure, voice) is not None:
                by_measure_staff.setdefault(mi, {})[staff_id] = []
        for m, chord, eligible in _walk_voice(staff, voice):
            if not eligible:
                continue
            lyric = _get_lyric(chord, verse, verse2_fallback)
            token = _token_from_lyric(*lyric) if lyric is not None and lyric[1] else "_"
            by_measure_staff.setdefault(m - 1, {}).setdefault(staff_id, []).append(token)
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
    _clear_lyrics(chord, 1)


def _set_lyric(chord: etree._Element, syllabic: str, text: str, verse: int = 1) -> None:
    """Set one verse's lyric on a chord, replacing what that verse had there.

    Verse 1 omits `<no>`; verse n writes `<no>n-1</no>`. Where the old word was
    printed (`<placement>`) is kept.
    """
    placement = None
    for lyrics in list(chord.findall(".//Lyrics")):
        if _verse_of(lyrics) == verse:
            placement = placement or lyrics.findtext("placement")
            chord.remove(lyrics)
    lyric_el = etree.Element("Lyrics")
    if verse > 1:
        etree.SubElement(lyric_el, "no").text = str(verse - 1)
    etree.SubElement(lyric_el, "syllabic").text = syllabic
    if placement:
        etree.SubElement(lyric_el, "placement").text = placement
    etree.SubElement(lyric_el, "text").text = text
    # After the chord's other lyrics, before its notes: where MuseScore writes them.
    anchor = chord.find("Note")
    if anchor is not None:
        anchor.addprevious(lyric_el)
    else:
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

    A `_` (a note with no syllable) leaves that state alone: after `lei-` it is a note
    the word is held over, so `e-lei- _ _ son` keeps `son` as the end of the word
    (#393). It used to end the word, and Kyrie's 48 `son`s came back as words of their own.
    """
    out: List[Tuple[str, str]] = []
    continues_from_previous = first_syllabic_continuation
    for tok in tokens:
        if tok == "_":
            out.append(("_", ""))
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
    """True if the last syllable ends with a hyphen (begin/middle): the word carries on.

    Trailing `_` are notes the word is held over, so they are looked past.
    """
    for tok in reversed(tokens):
        if tok.strip() != "_":
            return tok.strip().endswith("-")
    return False


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


def _clear_all_verse1_lyrics(score_root: etree._Element) -> None:
    """Remove every verse 1 Lyrics element from the whole score (full-replace import)."""
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    for chord in score.findall(".//Chord"):
        _clear_verse1_lyrics(chord)


def _place_on_voice(
    staff: etree._Element, voice: int, verse: int, measures: Dict[int, List[str]]
) -> None:
    """Write one part's voice and verse: `measures` is {bar: tokens} for the bars to rewrite.

    Every other bar keeps its words. A word a bar starts in the middle of is joined to
    the last syllable before it — placed just now, or already in the score — so a
    system imported on its own continues the word the system before it left open.
    """
    by_bar: Dict[int, List[Tuple[etree._Element, bool]]] = {}
    for m, chord, eligible in _walk_voice(staff, voice):
        by_bar.setdefault(m, []).append((chord, eligible))
    open_word = False

    def note_open(chords: List[etree._Element]) -> bool:
        state = open_word
        for chord in chords:
            lyric = _get_lyric(chord, verse)
            if lyric is not None and lyric[1]:
                state = lyric[0] in ("begin", "middle")
        return state

    for m in sorted(by_bar):
        chords = by_bar[m]
        elig = [c for c, e in chords if e]
        if m not in measures:
            open_word = note_open(elig)
            continue
        syllables = _tokens_to_syllables(measures[m], first_syllabic_continuation=open_word)
        for chord, eligible in chords:
            if not eligible:
                _clear_lyrics(chord, verse)
        for i, chord in enumerate(elig):
            if i >= len(syllables):
                _clear_lyrics(chord, verse)
                continue
            if i == len(elig) - 1 and len(syllables) - i > 1:
                # More syllables than notes: every earlier note took its own, and the
                # overflow is kept on this last note so it stays visible (e.g. öt-tä.
                # forced into one slot). Cramming as soon as the count went over put a
                # whole bar on its first note (#307). A `_` says "no word here", so
                # extra ones are dropped rather than printed.
                chunk = syllables[i:]
                kept = [chunk[0]] + [s for s in chunk[1:] if s[0] != "_"]
                if kept[0][0] == "_" and len(kept) > 1:
                    kept = kept[1:]
                if len(kept) == 1:
                    syllabic, text = kept[0]
                    if syllabic == "_":
                        _clear_lyrics(chord, verse)
                    else:
                        _set_lyric(chord, syllabic, text, verse)
                else:
                    merged_text = " ".join(_syllables_to_tokens(kept)).strip()
                    if merged_text:
                        _set_lyric(chord, "single", merged_text, verse)
                    else:
                        _clear_lyrics(chord, verse)
                break
            syllabic, text = syllables[i]
            if syllabic == "_":
                _clear_lyrics(chord, verse)
            else:
                _set_lyric(chord, syllabic, text, verse)
        open_word = note_open(elig)


def _place_two_voice_words(
    score: etree._Element,
    written: List[Written],
    systems: Optional[List[Tuple[int, int]]] = None,
) -> None:
    """Where the words print on a staff singing words on two voices (#393).

    MuseScore puts both voices' words on one line under the staff, so where both sing
    at once they print on top of each other (Kevät's Soprano 2, bar 27). In a system
    where a staff's second voice has words, its first voice's words go above the
    staff; in the others they stay below. Only the systems an import wrote on change,
    so a placement set by hand elsewhere is left alone.
    """
    staves = {int(s.get("id", "0")): s for s in _music_staves(score)}
    ranges = sorted(set(systems or []))
    for staff_id in sorted({w.staff for w in written}):
        staff = staves.get(staff_id)
        if staff is None:
            continue
        bars = staff.findall("Measure")
        spans = set()
        for w in written:
            if w.staff != staff_id:
                continue
            hit = [(a, b) for a, b in ranges if a <= w.end and b >= w.start]
            spans.update(hit or [(m, m) for m in range(w.start, w.end + 1)])
        for a, b in sorted(spans):
            tracks = [(_voice_track(bar, 1), _voice_track(bar, 2)) for bar in bars[a - 1:b]]
            two = any(v2 is not None and v2.find("Chord/Lyrics") is not None for _, v2 in tracks)
            for v1, v2 in tracks:
                for track, above in ((v1, two), (v2, False)):
                    if track is None:
                        continue
                    for lyrics in track.findall("Chord/Lyrics"):
                        here = lyrics.find("placement")
                        if above and here is None:
                            here = etree.Element("placement")
                            text = lyrics.find("text")
                            if text is not None:
                                text.addprevious(here)
                            else:
                                lyrics.append(here)
                            here.text = "above"
                        elif above and here is not None:
                            here.text = "above"
                        elif not above and here is not None and (here.text or "").strip() == "above":
                            lyrics.remove(here)


def _import_txt_into_mscx(
    score_root: etree._Element,
    txt: Optional[str] = None,
    by_measure: Optional[Dict[int, Dict[Any, List[str]]]] = None,
    clear_existing: bool = False,
) -> LyricImport:
    """
    Import lyrics into the score (in-place). Slur-continuation chords are skipped (no lyric).
    Hyphenated tokens (e.g. il-man) are expanded to begin/end syllables on consecutive chords.
    If the syllable before a bar was begin/middle (trailing hyphen), the bar's first
    syllable continues that word.

    Provide either txt (plain # Measure N / staff: tokens format) or by_measure (precomputed
    {measure_1based: {target: [tokens]}}); a target is a staff id (verse 1 of its first
    voice) or a (staff, voice, verse) triple. If both are None, nothing is applied.

    Only the bars named are rewritten, on the voice and verse named: every other verse,
    voice and bar keeps its words (#392, #397). clear_existing: remove all verse 1
    lyrics from the whole score first (full replace), for a score whose words (e.g. OCR
    from MusicXML) the import should fully supersede.
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

    plan: Dict[Tuple[int, int, int], Dict[int, List[str]]] = {}
    for m, row in by_measure.items():
        for target, tokens in row.items():
            key = tuple(target) if isinstance(target, (tuple, list)) else (int(target), 1, 1)
            plan.setdefault(key, {})[int(m)] = tokens
    staves = {int(s.get("id", "0")): s for s in _music_staves(score)}
    for (staff_id, voice, verse), measures in sorted(plan.items()):
        staff = staves.get(staff_id)
        if staff is not None:
            _place_on_voice(staff, voice, verse, measures)
    if clear_existing:
        # A full replace leaves no verse 1 word on a note that cannot take one.
        for chord in score.findall(".//Chord"):
            if _is_continuation_no_lyric(chord):
                _clear_verse1_lyrics(chord)
    return LyricImport()


def _apply_split_to_by_measure(
    by_measure: Dict[int, Dict[Any, List[str]]],
    split: List[int],
) -> Dict[int, Dict[Any, List[str]]]:
    """
    Expand by_measure so that each split part is duplicated to two consecutive staff ids.
    E.g. split [3, 4]: input part 3 -> output staffs 3 and 4 (same content), input part 4 -> staffs 5 and 6.
    """
    if not split:
        return by_measure
    new_by_measure: Dict[int, Dict[Any, List[str]]] = {}
    for measure, staff_lines in by_measure.items():
        new_by_measure[measure] = {}
        for target, tokens in staff_lines.items():
            staff_id, rest = (target[0], tuple(target[1:])) if isinstance(target, tuple) else (target, ())
            if staff_id in split:
                i = split.index(staff_id)
                output_ids = [staff_id + i, staff_id + i + 1]
            else:
                output_ids = [staff_id]
            for out_id in output_ids:
                new_by_measure[measure][(out_id,) + rest if rest else out_id] = list(tokens)
    return new_by_measure


def _fill_missing_measure_starts(
    json_str: str, score_root: etree._Element, starts: Optional[List[int]] = None
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

    # The printed systems when the caller knows them: normal-mode cleaning strips the
    # line breaks this would otherwise read them off.
    starts = starts or [r.start for r in system_ranges(score_root)]
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
    systems: Optional[List[Tuple[int, int]]] = None,
) -> LyricImport:
    """
    Import line-by-line JSON lyrics into the score. JSON format: array of objects with
    'measure_start' (measure number where the line starts) and either part keys whose values are
    lyric lines, or a 'lyrics' array of {text, staff_number, position, parts, verse, voice}.
    staff_number/position are mapped to output staves via the score's 'lyricsStaffMap'
    (written by clean_score); an explicit 'parts' list overrides that mapping.
    Part keys can be numeric ("1", "2", ...) for staff id directly, or names (e.g. S1, A1) mapped
    via part_to_staff (default S1->1, S2->2, A1->3, A2->4).
    If split is given (e.g. [3, 4]), those input parts are each duplicated to two staves:
    part 3 -> staffs 3 and 4, part 4 -> staffs 5 and 6.
    clear_existing: remove all existing verse 1 lyrics first (full replace).
    `systems` are the printed systems as (start, end) bars, when the caller knows them:
    they decide where a line may run on to, and fill a null measure_start.

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
    json_str, notes, filled = _fill_missing_measure_starts(
        json_str, score_root, starts=[a for a, _ in systems] if systems else None)
    data = json.loads(json_str)
    if (not isinstance(data, list) or not data or not isinstance(data[0], dict)
            or "measure_start" not in data[0]):
        return LyricImport(mismatches=notes, filled_measure_starts=filled)
    lines = _lines_from_blocks(data, staff_map, system_map, name_map, part_to_staff)
    if not lines:
        return LyricImport(mismatches=notes, filled_measure_starts=filled)
    if clear_existing:
        _clear_all_verse1_lyrics(score_root)
    by_measure, mismatches, written = _lines_to_by_measure(lines, score, systems)
    if split:
        by_measure = _apply_split_to_by_measure(by_measure, split)
    _import_txt_into_mscx(score_root, by_measure=by_measure, clear_existing=clear_existing)
    _place_two_voice_words(score, written, systems)
    return LyricImport(mismatches=notes + mismatches, filled_measure_starts=filled,
                       written=written)


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
    systems: Optional[List[Tuple[int, int]]] = None,
) -> LyricImport:
    """Put lyrics into the score, in place, and report what did not fit.

    `source` is TXT, JSON text, or the already-parsed JSON blocks the manual editor
    builds; `fmt` ("txt"/"json") overrides the format sniff. `replace` clears every
    verse-1 lyric first (a full replace); without it, only the parts, voices, verses
    and systems the source names change, and everything else keeps its words.
    `split` duplicates a part onto two staves (JSON only). `systems` are the printed
    systems as (start, end) bars, when known (JSON only). Returns the mismatches and
    the bars rewritten — nothing is written to stdout or stderr.

    Only the JSON path measures syllables against chords, so a TXT import always
    reports an empty result (TXT is written per measure, so it cannot drift).
    """
    if fmt is None:
        fmt = "txt" if isinstance(source, str) and not source.lstrip().startswith("[") else "json"
    if fmt == "json":
        return _import_json_txt_into_mscx(
            score_root, source, part_to_staff=part_to_staff,
            split=split, clear_existing=replace, systems=systems,
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


def syllable_slots(score_root: etree._Element, staff_id: int, measure_no: int) -> List[bool]:
    """`slot_counts` spread out: one flag per voice-0 chord of that bar, in order.

    The same arithmetic, told per note rather than per bar, for a caller that has to
    point at *which* note — recording a missing slur means naming two of them, and a
    reader picking them wants to see which ones the lyrics currently land on. Asking
    here rather than reading the Spanner elements directly keeps one owner: a note in
    the middle of a slur carries no marker of its own, so eligibility cannot be
    decided by looking at a chord on its own.

    Empty when the staff or the measure is not there, or the measure has no voice.
    """
    score = score_root.find(".//Score") if score_root.tag != "Score" else score_root
    if score is None:
        return []
    return _eligibility_per_measure(score).get(int(staff_id), {}).get(int(measure_no), [])


def lyric_parts(score_root: etree._Element) -> List[EditorPart]:
    """The parts that carry words: name and output staff id, in staff order.

    The score's own track names, minus the click/spacer staff a recording adds — it
    has nothing to sing, so it is neither a lyric cell nor somewhere a slur belongs.
    Public because more than the lyric editor needs to offer a person a list of parts
    to point at, and the exclusion rule should not be written down twice.
    """
    score = score_root.find(".//Score") if score_root.tag != "Score" else score_root
    if score is None:
        return []
    parts: List[EditorPart] = []
    for p, owned in zip(score.findall("Part"), part_staff_ids(score)):
        sid = int(owned[0]) if owned else 0
        name = (p.findtext("trackName") or p.findtext("Instrument/trackName") or "").strip()
        if any(w in name.lower() for w in _NON_LYRIC_PART_WORDS):
            continue
        parts.append(EditorPart(id=sid, name=name or f"staff {sid}"))
    parts.sort(key=lambda p: p.id)
    return parts


def _printed_of(score_root: etree._Element, start: int) -> Dict[int, int]:
    """{output staff id: printed staff number} for the system starting at `start`.

    Read off the clean's part-to-staff record: the per-system map when there is one,
    else the one map for the whole score. Empty when the score has neither, which is
    a song cleaned before the record existed.
    """
    system_map = _read_lyrics_system_map(score_root)
    pmap = (_map_for_measure(system_map, start) if system_map is not None
            else _read_lyrics_staff_map(score_root))
    return {sid: printed for printed, ids in pmap.items() for sid in ids}


def printed_staves(score_root: etree._Element, start: int, end: int) -> Tuple[List[int], List[int]]:
    """The printed staves of one system, top first: all of them, and those that sing.

    "All" is every printed staff the clean's part-to-staff record names for the
    system; "sing" keeps those whose parts have notes in bars `start`-`end`. A page
    leaves a staff out where its part rests, and a crop shows one of the two counts,
    which is how the lyric editor knows which picture staff a part's boxes go under.
    """
    score = score_root.find(".//Score") if score_root.tag != "Score" else score_root
    if score is None:
        return [], []
    printed = _printed_of(score_root, start)
    singing = set()
    for staff in _music_staves(score):
        sid = int(staff.get("id", "0"))
        if sid not in printed:
            continue
        for bar in staff.findall("Measure")[start - 1:end]:
            if bar.find("voice/Chord") is not None:
                singing.add(printed[sid])
                break
    return sorted(set(printed.values())), sorted(singing)


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

    `boxes` (#397) is the same per voice and verse: a part's box in every system where
    it has a note to sing, and a box for its second voice only where that voice does,
    each holding every verse's words.
    """
    parts = lyric_parts(score_root)
    score = score_root.find(".//Score") if score_root.tag != "Score" else score_root

    if systems:
        ranges = [EditorSystem(index=i, start=a, end=b)
                  for i, (a, b) in enumerate(systems)]
    else:
        ranges = [EditorSystem(index=r.index, start=r.start, end=r.end)
                  for r in system_ranges(score_root)]
    systems = ranges
    verses = _verses_in(score) if score is not None else [1]
    words = {(voice, verse): _lyrics_by_measure_staff(score_root, voice, verse)
             for voice in (1, 2) for verse in verses}
    counts = {voice: _get_chord_counts_per_measure(score, voice) if score is not None else {}
              for voice in (1, 2)}

    def text_of(voice: int, verse: int, part: EditorPart, system: EditorSystem) -> str:
        tokens: List[str] = []
        for mi in range(system.start - 1, system.end):
            tokens += words[(voice, verse)].get(mi, {}).get(part.id, [])
        # Trailing slots are kept as well as inner ones: a note at the end of
        # the line with no word under it is worth seeing, and dropping it left
        # the cell short of its own capacity, so re-saving an untouched system
        # reported `too_few` out of nowhere. But a cell of *nothing but* empty
        # slots is a part that does not sing in this system, and that is an
        # empty cell, not a row of underscores.
        if not any(t != "_" for t in tokens):
            return ""
        return _merge_tokens(tokens).strip()

    cells: Dict[int, Dict[str, str]] = {}
    capacities: Dict[int, Dict[str, int]] = {}
    boxes: Dict[int, List[EditorBox]] = {}
    for system in systems:
        printed = _printed_of(score_root, system.start)
        for part in parts:
            for voice in (1, 2):
                slots = sum(counts[voice].get(part.id, {}).get(mi, 0)
                            for mi in range(system.start, system.end + 1))
                if voice == 1:
                    capacities.setdefault(system.index, {})[part.name] = slots
                    text = text_of(1, 1, part, system)
                    if text:
                        cells.setdefault(system.index, {})[part.name] = text
                if not slots:
                    continue
                boxes.setdefault(system.index, []).append(EditorBox(
                    # By staff id: two parts can share a name (Kaipaava's two "T2").
                    key=str(part.id) if voice == 1 else f"{part.id}@{voice}",
                    part=part.name, id=part.id, voice=voice, slots=slots,
                    printed=printed.get(part.id),
                    text={v: text_of(voice, v, part, system) for v in verses},
                ))
    return EditorGrid(parts=parts, systems=systems, cells=cells, capacities=capacities,
                      verses=verses, boxes=boxes)


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


def blocks_from_boxes(
    grid: EditorGrid, verse: int, boxes: Dict[Any, Dict[str, str]]
) -> List[Dict[str, Any]]:
    """The by-system editor's changed boxes as lyric JSON, for one verse (#397).

    `boxes` is {system index: {box key: text}} — only the boxes a person changed,
    keyed as `EditorBox.key` (staff id `3`, `3@2` for its second voice). An empty box is
    sent as an empty line, which clears its words; every box not sent keeps its own.
    Each block carries its system's last bar, so a line knows where its system ends.
    A key the system has no box for is ignored. Lines name their part by staff id,
    which no track name can be mistaken for.
    """
    blocks: List[Dict[str, Any]] = []
    for system in grid.systems:
        typed = boxes.get(system.index, boxes.get(str(system.index))) or {}
        known = {b.key: b for b in grid.boxes.get(system.index, [])}
        lyrics = []
        for key, text in typed.items():
            box = known.get(key)
            if box is None or not isinstance(text, str):
                continue
            # By staff id, not name: a part may be named "1", or two parts the same.
            lyrics.append({"parts": [box.id], "voice": box.voice, "verse": int(verse),
                           "text": text.strip()})
        if lyrics:
            blocks.append({"measure_start": system.start, "measure_end": system.end,
                           "lyrics": lyrics})
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
    systems: Optional[List[Tuple[int, int]]] = None,
) -> LyricImport:
    """Import lyrics from .txt or .json into a copy of the .mscx file. split only applies to .json (e.g. [3, 4]).
    replace=True clears all existing verse 1 lyrics first (full replace). `systems` are
    the printed systems as (start, end) bars, when known (.json only).
    Returns the placement result, so a caller can report mismatches without reading logs."""
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()
    root = _load_mscx(mscx_path_in)
    result = place_lyrics(
        root, content,
        fmt="json" if txt_path.lower().endswith(".json") else "txt",
        replace=replace, split=split, systems=systems,
    )
    _save_mscx(root, mscx_path_out)
    return result
