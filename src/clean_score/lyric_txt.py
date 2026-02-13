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
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

# Default mapping from JSON part keys (e.g. S1, A2) to staff id. Overridable.
DEFAULT_PART_TO_STAFF: Dict[str, int] = {"S1": 1, "S2": 2, "A1": 3, "A2": 4}


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
    spanner = chord.find(".//Spanner[@type='Slur']")
    if spanner is None:
        return False
    return spanner.find(".//prev") is not None


def _is_tie_continuation(chord: etree._Element) -> bool:
    """True if this chord is the continuation of a tie (Tie spanner with prev; often on Note)."""
    spanner = chord.find(".//Spanner[@type='Tie']")
    if spanner is None:
        return False
    return spanner.find(".//prev") is not None


def _is_continuation_no_lyric(chord: etree._Element) -> bool:
    """True if this chord should get no lyric token (slur or tie continuation)."""
    return _is_slur_continuation(chord) or _is_tie_continuation(chord)


def _has_slur_start(chord: etree._Element) -> bool:
    """True if this chord starts a slur (has Slur spanner with next)."""
    spanner = chord.find(".//Spanner[@type='Slur']")
    return spanner is not None and spanner.find(".//next") is not None


def _has_tie_start(chord: etree._Element) -> bool:
    """True if this chord starts a tie (has Tie spanner with next; often on Note)."""
    spanner = chord.find(".//Spanner[@type='Tie']")
    return spanner is not None and spanner.find(".//next") is not None


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
    """Split a line into tokens (space-separated, hyphen-merged). Same logic as parse_txt."""
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
    """
    out: Dict[int, Dict[int, int]] = {}
    staffs = score.findall(".//Staff")
    for staff in staffs:
        staff_id = int(staff.get("id", "0"))
        measure_index = -1
        for measure in staff.findall(".//Measure"):
            measure_index += 1
            voices = measure.findall("voice")
            if not voices:
                continue
            voice = voices[0]
            count = 0
            slur_active = False
            tie_active = False
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


def parse_json_txt(json_str: str) -> List[Dict[str, Any]]:
    """
    Parse JSON format: array of objects with 'measure_start' (int) and part keys (e.g. S1, S2, A1, A2).
    Returns list of {"measure_start": N, "S1": "text", ...}.
    """
    data = json.loads(json_str)
    if not isinstance(data, list) or len(data) == 0:
        return []
    first = data[0]
    if not isinstance(first, dict) or "measure_start" not in first:
        return []
    return data


def json_lines_to_by_measure(
    json_data: List[Dict[str, Any]],
    chord_counts: Dict[int, Dict[int, int]],
    part_to_staff: Optional[Dict[str, int]] = None,
) -> Dict[int, Dict[int, List[str]]]:
    """
    Convert line-by-line JSON (measure_start + part text per line) into by_measure[measure][staff_id] = tokens.
    Part keys can be "1", "2" (staff id) or names mapped via part_to_staff. Expands each line to syllables,
    distributes by chord count, then converts each measure's chunk back to tokens.
    """
    if part_to_staff is None:
        part_to_staff = DEFAULT_PART_TO_STAFF
    by_measure: Dict[int, Dict[int, List[str]]] = {}
    part_keys = [k for k in json_data[0].keys() if k != "measure_start"]
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
            tokens = _tokenize_line(text.strip())
            lines.append((int(m_start), tokens))
        lines.sort(key=lambda x: x[0])
        prev_trailing_hyphen = False
        for line_idx, (m_start, tokens) in enumerate(lines):
            next_start = lines[line_idx + 1][0] if line_idx + 1 < len(lines) else None
            syllables = _tokens_to_syllables(tokens, first_syllabic_continuation=prev_trailing_hyphen)
            prev_trailing_hyphen = _last_token_ends_with_hyphen(tokens)
            m_end = next_start if next_start is not None else (max(counts.keys()) + 1 if counts else m_start + 1)
            syl_offset = 0
            for m in range(m_start, m_end):
                if syl_offset >= len(syllables):
                    break
                n_slots = counts.get(m, 0)
                if n_slots <= 0:
                    continue
                chunk = syllables[syl_offset : syl_offset + n_slots]
                syl_offset += len(chunk)
                if chunk:
                    measure_tokens = _syllables_to_tokens(chunk)
                    by_measure.setdefault(m, {})[staff_id] = measure_tokens
    return by_measure


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


def export_mscx_to_txt(score_root: etree._Element) -> str:
    """
    Export lyrics from a MuseScore score element (root of parsed .mscx) to TXT format.
    Only voice 0, verse 1. Slur-continuation notes get no token.
    """
    add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return ""
    division = _get_division(score_root)
    staffs = score.findall(".//Staff")
    if not staffs:
        staffs = score_root.findall(".//Staff")
    by_measure_staff: Dict[int, Dict[int, List[str]]] = {}
    lines: List[str] = []
    for staff in staffs:
        staff_id = int(staff.get("id", "0"))
        measure_index = -1
        for measure in staff.findall(".//Measure"):
            measure_index += 1
            voices = measure.findall("voice")
            if not voices:
                continue
            voice = voices[0]
            measure_tokens: List[str] = []
            slur_active = False
            tie_active = False
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
                elif el.tag == "Rest":
                    continue
                elif el.tag == "location":
                    continue
            by_measure_staff.setdefault(measure_index, {})[staff_id] = measure_tokens

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


def parse_txt(txt: str) -> List[Dict[str, Any]]:
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
    Hyphen in the middle of a token splits into begin/end syllables.
    If first_syllabic_continuation is True (previous measure ended with begin/middle), the first
    syllable is forced to "end" so it continues across the bar.
    """
    out: List[Tuple[str, str]] = []
    for idx, tok in enumerate(tokens):
        if tok == "_":
            out.append(("_", ""))
            continue
        # Split on hyphen that joins syllables (not leading/trailing)
        raw_trailing_hyphen = tok.strip().endswith("-")
        parts = tok.strip().rstrip("-").split("-")
        if len(parts) == 1:
            text = parts[0].strip()
            if first_syllabic_continuation and idx == 0:
                out.append(("end", text))
            elif raw_trailing_hyphen:
                out.append(("begin", text))  # continues to next measure
            else:
                out.append(("single", text))
        else:
            for i, p in enumerate(parts):
                p = p.strip()
                if not p:
                    continue
                if first_syllabic_continuation and idx == 0 and i == 0:
                    out.append(("end", p))
                elif i == 0:
                    out.append(("begin", p))
                elif i == len(parts) - 1:
                    # Last part: "end" unless token had trailing hyphen (word continues to next measure)
                    if raw_trailing_hyphen:
                        out.append(("begin", p))
                    else:
                        out.append(("end", p))
                else:
                    out.append(("middle", p))
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
    """
    tokens: List[str] = []
    i = 0
    while i < len(syllables):
        syllabic, text = syllables[i]
        if syllabic == "_":
            tokens.append("_")
            i += 1
        elif syllabic == "single":
            tokens.append(text)
            i += 1
        elif syllabic == "begin":
            parts = [text]
            i += 1
            while i < len(syllables):
                s2, t2 = syllables[i]
                if s2 == "middle":
                    parts.append(t2)
                    i += 1
                elif s2 == "end":
                    parts.append(t2)
                    i += 1
                    tokens.append("-".join(parts))
                    break
                else:
                    break
            else:
                tokens.append("-".join(parts) + "-")
        elif syllabic == "end":
            tokens.append(text)
            i += 1
        elif syllabic == "middle":
            tokens.append(text)
            i += 1
        else:
            i += 1
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


def import_txt_into_mscx(
    score_root: etree._Element,
    txt: Optional[str] = None,
    by_measure: Optional[Dict[int, Dict[int, List[str]]]] = None,
) -> None:
    """
    Import TXT lyrics into the score (in-place). Verse 1, voice 0. Slur-continuation chords are skipped (no lyric).
    Hyphenated tokens (e.g. il-man) are expanded to begin/end syllables on consecutive chords.
    If the previous measure's last syllable was begin/middle (trailing hyphen), the next measure's
    first syllable is set as "end". Verse 2 and higher are removed.

    Provide either txt (plain # Measure N / staff: tokens format) or by_measure (precomputed
    {measure_1based: {staff_id: [tokens]}}). If both are None, nothing is applied.
    """
    add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    if by_measure is None:
        if txt is None:
            return
        blocks = parse_txt(txt)
        by_measure = {b["measure"]: b["staff_lines"] for b in blocks}

    staffs = score.findall(".//Staff")
    if not staffs:
        staffs = score_root.findall(".//Staff")
    # Only process Staff elements that contain measures (skip Part/Staff layout stubs)
    staffs = [s for s in staffs if s.find(".//Measure") is not None]

    for staff in staffs:
        staff_id = int(staff.get("id", "0"))
        measure_index = -1
        for measure in staff.findall(".//Measure"):
            measure_index += 1
            one_based = measure_index + 1
            # Only edit measures that are present in by_measure (partial JSON may omit earlier measures)
            if one_based not in by_measure or staff_id not in by_measure[one_based]:
                continue
            staff_tokens = by_measure[one_based][staff_id]
            # Derive from TXT: did the previous measure's last token end with hyphen?
            prev_measure_tokens = (by_measure.get(one_based - 1) or {}).get(staff_id) or []
            first_syllabic_continuation = _last_token_ends_with_hyphen(prev_measure_tokens)
            syllables = _tokens_to_syllables(staff_tokens, first_syllabic_continuation=first_syllabic_continuation)
            syl_index = [0]
            slur_active = False
            tie_active = False

            voices = measure.findall("voice")
            if not voices:
                continue
            voice = voices[0]
            for el in voice:
                if el.tag != "Chord":
                    continue
                if _is_continuation_no_lyric(el):
                    _clear_verse1_lyrics(el)
                    if _is_slur_continuation(el):
                        slur_active = False
                    if _is_tie_continuation(el):
                        tie_active = False
                    continue
                if slur_active and not _has_slur_start(el) and not _is_slur_continuation(el):
                    _clear_verse1_lyrics(el)
                    continue  # middle of slur
                if tie_active and not _has_tie_start(el) and not _is_tie_continuation(el):
                    _clear_verse1_lyrics(el)
                    continue  # middle of tie
                if syl_index[0] >= len(syllables):
                    for lyrics in list(el.findall(".//Lyrics")):
                        no_el = lyrics.find("no")
                        if _is_verse1(no_el):
                            el.remove(lyrics)
                    continue
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


def import_json_txt_into_mscx(
    score_root: etree._Element,
    json_str: str,
    part_to_staff: Optional[Dict[str, int]] = None,
    split: Optional[List[int]] = None,
) -> None:
    """
    Import line-by-line JSON lyrics into the score. JSON format: array of objects with
    'measure_start' (measure number where the line starts) and part keys whose values are lyric lines.
    Part keys can be numeric ("1", "2", ...) for staff id directly, or names (e.g. S1, A1) mapped
    via part_to_staff (default S1->1, S2->2, A1->3, A2->4).
    If split is given (e.g. [3, 4]), those input parts are each duplicated to two staves:
    part 3 -> staffs 3 and 4, part 4 -> staffs 5 and 6.
    """
    add_rests_to_empty_measures(score_root)
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    json_data = parse_json_txt(json_str)
    if not json_data:
        return
    chord_counts = _get_chord_counts_per_measure(score)
    by_measure = json_lines_to_by_measure(json_data, chord_counts, part_to_staff)
    if split:
        by_measure = _apply_split_to_by_measure(by_measure, split)
    import_txt_into_mscx(score_root, by_measure=by_measure)


def add_rests_to_empty_measures(score_root: etree._Element) -> None:
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


def load_mscx(path: str) -> etree._Element:
    """Load .mscx file and return root element."""
    with open(path, "r", encoding="utf-8") as f:
        return etree.fromstring(f.read().encode("utf-8"))


def save_mscx(root: etree._Element, path: str) -> None:
    """Write score XML to file."""
    with open(path, "wb") as f:
        f.write(etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True))


def export_file(mscx_path: str, txt_path: str) -> None:
    """Export lyrics from an .mscx file to a .txt file."""
    root = load_mscx(mscx_path)
    txt = export_mscx_to_txt(root)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)


def import_file(
    txt_path: str,
    mscx_path_in: str,
    mscx_path_out: str,
    split: Optional[List[int]] = None,
) -> None:
    """Import lyrics from .txt or .json into a copy of the .mscx file. split only applies to .json (e.g. [3, 4])."""
    with open(txt_path, "r", encoding="utf-8") as f:
        content = f.read()
    root = load_mscx(mscx_path_in)
    if txt_path.lower().endswith(".json"):
        import_json_txt_into_mscx(root, content, split=split)
    else:
        import_txt_into_mscx(root, txt=content)
    save_mscx(root, mscx_path_out)
