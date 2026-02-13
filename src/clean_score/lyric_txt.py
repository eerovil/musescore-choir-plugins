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
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree


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


def _get_verse1_lyric(chord: etree._Element) -> Optional[Tuple[str, str]]:
    """Returns (syllabic, text) for verse 1, or verse 2 if verse 1 is missing. None if no lyrics."""
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
        if (no == "1" or not no) and verse1 is None:
            verse1 = pair
        elif no == "2":
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
    """Remove all verse 1 Lyrics from chord (used for ineligible positions: inside spanner, etc.)."""
    for lyrics in list(chord.findall(".//Lyrics")):
        no_el = lyrics.find("no")
        n = (no_el.text or "").strip() if no_el is not None else ""
        if n in ("", "1"):
            chord.remove(lyrics)


def _set_lyric(chord: etree._Element, syllabic: str, text: str, no: str = "1") -> None:
    """Set or replace verse 1 lyric on chord. Removes all existing verse 1 lyrics first."""
    for lyrics in list(chord.findall(".//Lyrics")):
        no_el = lyrics.find("no")
        n = (no_el.text or "").strip() if no_el is not None else ""
        if n == no or (not n and no == "1"):
            chord.remove(lyrics)
    lyric_el = etree.Element("Lyrics")
    s_el = etree.SubElement(lyric_el, "syllabic")
    s_el.text = syllabic
    t_el = etree.SubElement(lyric_el, "text")
    t_el.text = text
    no_el = etree.SubElement(lyric_el, "no")
    no_el.text = no
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


def _remove_verse2_plus(score_root: etree._Element) -> None:
    """Remove all Lyrics with no=2, no=3, etc. so only verse 1 remains."""
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
        return
    for lyrics in score.findall(".//Lyrics"):
        no_el = lyrics.find("no")
        no = (no_el.text or "").strip() if no_el is not None else ""
        if no and no != "1":
            parent = lyrics.getparent()
            if parent is not None:
                parent.remove(lyrics)


def import_txt_into_mscx(score_root: etree._Element, txt: str) -> None:
    """
    Import TXT lyrics into the score (in-place). Verse 1, voice 0. Slur-continuation chords are skipped (no lyric).
    Hyphenated tokens (e.g. il-man) are expanded to begin/end syllables on consecutive chords.
    If the previous measure's last syllable was begin/middle (trailing hyphen), the next measure's
    first syllable is set as "end". Verse 2 and higher are removed.
    """
    score = score_root if score_root.tag == "Score" else score_root.find(".//Score")
    if score is None:
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
            staff_tokens = (by_measure.get(one_based) or {}).get(staff_id)
            if staff_tokens is None:
                staff_tokens = []
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
                        if (no_el is None or (no_el.text or "").strip() in ("", "1")):
                            el.remove(lyrics)
                    continue
                syllabic, text = syllables[syl_index[0]]
                syl_index[0] += 1
                if syllabic == "_":
                    for lyrics in list(el.findall(".//Lyrics")):
                        no_el = lyrics.find("no")
                        if (no_el is None or (no_el.text or "").strip() in ("", "1")):
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


def import_file(txt_path: str, mscx_path_in: str, mscx_path_out: str) -> None:
    """Import lyrics from .txt into a copy of the .mscx file."""
    with open(txt_path, "r", encoding="utf-8") as f:
        txt = f.read()
    root = load_mscx(mscx_path_in)
    import_txt_into_mscx(root, txt)
    save_mscx(root, mscx_path_out)
