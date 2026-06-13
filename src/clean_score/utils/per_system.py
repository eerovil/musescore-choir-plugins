#!/usr/bin/env python3
"""
Per-system re-voicing (opt-in) for badly-parsed scores.

Some OCR'd scores assign parts to physical staves inconsistently: the same staff
carries different parts in different systems (e.g. staff 1 is T1+T2 at the start,
T3 at measure 20, T1 at measure 26). The only reliable cut is the printed system,
i.e. each line break.

This mode walks the score system by system (between line breaks) and asks the user
to name each staff's voices for that system. It then rebuilds the score as one clean
staff per named part, pulling each part's notes from whichever (staff, voice) was
declared in each system and filling rests where the part is absent. Undeclared /
empty staves simply disappear (this is also how part deletion happens here).

Only used when explicitly enabled (clean_score --per-system) and stdin is a TTY.
"""

import json
import os
import sys
from copy import deepcopy
from typing import Dict, List, Optional, Tuple

from lxml import etree

import logging

from .revoice import _midi_name, _staff_label, _voice_summary

logger = logging.getLogger(__name__)

# Repo-root cache of per-system answers, keyed by input file name (no extension).
# Lets you re-run the same score without retyping (and run non-interactively in tests).
_CACHE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".persystem_cache.json")
)


def load_answer_cache(input_key: str) -> Optional[Dict[int, Dict[int, str]]]:
    """Return cached answers {system_index: {staff_id: answer}} for input_key, or None."""
    if not input_key or not os.path.exists(_CACHE_PATH):
        return None
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    entry = raw.get(input_key)
    if not entry:
        return None
    return {int(sidx): {int(sid): ans for sid, ans in staves.items()}
            for sidx, staves in entry.items()}


def save_answer_cache(input_key: str, answers: Dict[int, Dict[int, str]]) -> None:
    """Persist answers {system_index: {staff_id: answer}} for input_key."""
    if not input_key:
        return
    raw: Dict[str, Dict[str, Dict[str, str]]] = {}
    if os.path.exists(_CACHE_PATH):
        try:
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
    raw[input_key] = {str(sidx): {str(sid): ans for sid, ans in staves.items()}
                      for sidx, staves in answers.items()}
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Could not write per-system cache: %s", exc)

# Part letter -> (sort rank, full name, clef). Unknown letters sort last.
# Voice elements provided by the staff skeleton (not copied from the source voice).
# Everything else (Chord, Rest, location, Tuplet, endTuplet, Beam, Spanner, ...) is
# note content and IS copied, so tuplets/beams/ties survive the rebuild.
_SKELETON_KEEP = {"TimeSig", "KeySig", "Clef"}

_PART_ORDER = {"S": 0, "A": 1, "T": 2, "B": 3, "M": 4, "W": 5}
_PART_FULL = {"S": "Soprano", "A": "Alto", "T": "Tenor", "B": "Bass", "M": "Men", "W": "Women"}
_PART_CLEF = {"S": "G", "A": "G", "T": "G8vb", "B": "F", "M": "G8vb", "W": "G"}


def find_systems(root: etree._Element) -> List[Tuple[int, int]]:
    """Return (start, end) 0-based inclusive measure ranges, split at line breaks."""
    score = root if root.tag == "Score" else root.find(".//Score")
    staff = score.find("Staff")
    measures = staff.findall("Measure")
    breaks = set()
    for i, m in enumerate(measures):
        for lb in m.findall(".//LayoutBreak"):
            if (lb.findtext("subtype") or "").strip() == "line":
                breaks.add(i)
    systems: List[Tuple[int, int]] = []
    start = 0
    for i in range(len(measures)):
        if i in breaks:
            systems.append((start, i))
            start = i + 1
    if start < len(measures):
        systems.append((start, len(measures) - 1))
    return systems


def _part_sort_key(name: str) -> Tuple[int, int, str]:
    letter = name[0].upper() if name else "Z"
    rank = _PART_ORDER.get(letter, 99)
    digits = "".join(c for c in name if c.isdigit())
    return (rank, int(digits) if digits else 0, name)


def _max_voices_in_range(staff: etree._Element, a: int, b: int) -> int:
    """Max number of note-bearing voices (ignoring all-rest voices) across the range."""
    measures = staff.findall("Measure")
    best = 0
    for m in range(a, b + 1):
        n = sum(1 for v in measures[m].findall("voice") if v.find("Chord") is not None)
        best = max(best, n)
    return best


def _first_nonempty_summary(staff: etree._Element, a: int, b: int) -> str:
    measures = staff.findall("Measure")
    for m in range(a, b + 1):
        voices = measures[m].findall("voice")
        summaries = [_voice_summary(v) for v in voices]
        if any(s != "(rest)" for s in summaries):
            return " || ".join(summaries)
    return "(empty)"


def _apply_answer(
    decls: Dict[int, Dict[Tuple[int, int], str]],
    sidx: int,
    sid: int,
    nv: int,
    chosen: str,
) -> None:
    """Turn one staff's answer string ("T1,T2") into decl entries for that system."""
    labels = [n.strip() for n in chosen.split(",")] if chosen else []
    for vidx, name in enumerate(labels):
        if vidx < nv and name:
            decls.setdefault(sidx, {})[(sid, vidx)] = name


def decls_from_answers(
    root: etree._Element,
    systems: List[Tuple[int, int]],
    answers: Dict[int, Dict[int, str]],
) -> Dict[int, Dict[Tuple[int, int], str]]:
    """Build decls from cached/saved answers without prompting."""
    score = root if root.tag == "Score" else root.find(".//Score")
    staves = score.findall("Staff")
    decls: Dict[int, Dict[Tuple[int, int], str]] = {}
    for sidx, (a, b) in enumerate(systems):
        sys_ans = answers.get(sidx, {})
        for staff in staves:
            sid = int(staff.get("id", "0"))
            nv = _max_voices_in_range(staff, a, b)
            if nv == 0:
                continue
            _apply_answer(decls, sidx, sid, nv, sys_ans.get(sid, ""))
    return decls


def prompt_system_decls(
    root: etree._Element,
    systems: List[Tuple[int, int]],
    cache: Optional[Dict[int, Dict[int, str]]] = None,
) -> Tuple[Dict[int, Dict[Tuple[int, int], str]], Dict[int, Dict[int, str]]]:
    """
    For each system, ask the user to name each staff's voices.

    Returns (decls, answers) where decls[system_index][(staff_id, voice_index)] = part_name
    and answers[system_index][staff_id] = the chosen answer string (for caching).

    If `cache` is given, its per-staff answer is offered as the default for that system
    (Enter reuses it), so re-running a previously-answered score needs almost no typing.
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    staves = score.findall("Staff")
    decls: Dict[int, Dict[Tuple[int, int], str]] = {}
    answers: Dict[int, Dict[int, str]] = {}
    last_answer: Dict[int, str] = {}  # per staff id; Enter reuses it
    cache = cache or {}
    print(
        "\nPer-system re-voicing: for each system, name each staff's voices "
        "(comma per voice).\n"
        "   Enter reuses the previous answer (shown in [brackets]); "
        "'-' clears/skips a staff.",
        file=sys.stderr,
    )
    for sidx, (a, b) in enumerate(systems):
        print(f"\n— System {sidx + 1}: measures {a + 1}-{b + 1} —", file=sys.stderr)
        for staff in staves:
            sid = int(staff.get("id", "0"))
            nv = _max_voices_in_range(staff, a, b)
            summary = _first_nonempty_summary(staff, a, b)
            print(f"   staff {sid}: {nv} voice(s) — {summary}", file=sys.stderr)
        for staff in staves:
            sid = int(staff.get("id", "0"))
            nv = _max_voices_in_range(staff, a, b)
            if nv == 0:
                continue
            # Prefer this system's cached answer as the default; fall back to the
            # previous system's answer for the same staff.
            default = cache.get(sidx, {}).get(sid, last_answer.get(sid, ""))
            hint = f" [{default}]" if default else ""
            raw = input(f"   staff {sid} ({nv} voice(s)){hint} > ").strip()
            if raw == "":
                chosen = default          # reuse default for this staff
            elif raw == "-":
                chosen = ""               # explicit skip / clear
            else:
                chosen = raw
            last_answer[sid] = chosen
            answers.setdefault(sidx, {})[sid] = chosen
            _apply_answer(decls, sidx, sid, nv, chosen)
    return decls, answers


def _system_of(measure_index: int, systems: List[Tuple[int, int]]) -> int:
    for sidx, (a, b) in enumerate(systems):
        if a <= measure_index <= b:
            return sidx
    return len(systems) - 1


def _measure_rest(sig_n: int, sig_d: int) -> etree._Element:
    rest = etree.Element("Rest")
    etree.SubElement(rest, "durationType").text = "measure"
    etree.SubElement(rest, "duration").text = f"{sig_n}/{sig_d}"
    return rest


def _set_clef(staff: etree._Element, letter: str) -> None:
    clef_type = _PART_CLEF.get(letter.upper())
    if not clef_type:
        return
    for clef in staff.findall(".//Clef"):
        for child in clef:
            if child.tag in ("concertClefType", "transposingClefType"):
                child.text = clef_type


def build_parts(
    root: etree._Element,
    systems: List[Tuple[int, int]],
    decls: Dict[int, Dict[Tuple[int, int], str]],
) -> List[str]:
    """
    Rebuild the score as one staff per declared part. Returns the ordered part names.
    Old Parts/Staves are removed (so empty/undeclared staves are deleted).
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    source_staves = {int(s.get("id", "0")): s for s in score.findall("Staff")}
    template_part = score.find("Part")
    ref_staff = score.find("Staff")

    parts = sorted({name for d in decls.values() for name in d.values()}, key=_part_sort_key)
    if not parts:
        return []

    new_staves: List[etree._Element] = []
    new_parts: List[etree._Element] = []
    for out_idx, part in enumerate(parts, start=1):
        staff = deepcopy(ref_staff)
        staff.set("id", str(out_idx))
        if out_idx > 1:
            vbox = staff.find("VBox")
            if vbox is not None:
                staff.remove(vbox)
        sig_n, sig_d = 4, 4
        for mi, measure in enumerate(staff.findall("Measure")):
            # Each output staff is single-voice; drop any extra voices copied from the
            # reference staff (which may itself be a 2-voice staff).
            voices = measure.findall("voice")
            for extra in voices[1:]:
                measure.remove(extra)
            # Drop copied layout breaks; they are re-added on the top staff below.
            for lb in measure.findall("LayoutBreak"):
                measure.remove(lb)
            voice = voices[0] if voices else etree.SubElement(measure, "voice")
            ts = voice.find("TimeSig")
            if ts is not None:
                try:
                    sig_n = int(ts.findtext("sigN") or sig_n)
                    sig_d = int(ts.findtext("sigD") or sig_d)
                except ValueError:
                    pass
            # Strip existing note content; keep TimeSig/KeySig/Clef from the skeleton.
            for el in list(voice):
                if el.tag not in _SKELETON_KEEP:
                    voice.remove(el)
            # Find the source (staff, voice) declared as this part in this system.
            system = _system_of(mi, systems)
            src: Optional[Tuple[int, int]] = None
            for (sid, vidx), name in decls.get(system, {}).items():
                if name == part:
                    src = (sid, vidx)
                    break
            placed = False
            if src is not None:
                src_staff = source_staves.get(src[0])
                if src_staff is not None:
                    src_measure = src_staff.findall("Measure")[mi]
                    src_voices = src_measure.findall("voice")
                    if src[1] < len(src_voices):
                        for el in src_voices[src[1]]:
                            if el.tag not in _SKELETON_KEEP:
                                voice.append(deepcopy(el))
                        placed = True
            if not placed:
                voice.append(_measure_rest(sig_n, sig_d))
        _set_clef(staff, part[0] if part else "")
        new_staves.append(staff)

        new_part = deepcopy(template_part)
        ps = new_part.find(".//Staff")
        if ps is not None:
            ps.set("id", str(out_idx))
        tn = new_part.find("trackName")
        if tn is not None:
            tn.text = part
        full = _PART_FULL.get(part[0].upper(), part) if part else part
        for tag, val in (("longName", part), ("shortName", part), ("trackName", part)):
            el = new_part.find(f".//Instrument/{tag}")
            if el is not None:
                el.text = val
        new_parts.append(new_part)

    # Re-add a line break at the end of each system (except the last) on the top staff,
    # so the rebuilt score keeps the original system layout.
    if new_staves:
        top_measures = new_staves[0].findall("Measure")
        for (a, b) in systems[:-1]:
            if b < len(top_measures):
                lb = etree.SubElement(top_measures[b], "LayoutBreak")
                etree.SubElement(lb, "subtype").text = "line"

    for old in score.findall("Part"):
        score.remove(old)
    for old in score.findall("Staff"):
        score.remove(old)
    # Parts come before Staves in a MuseScore Score.
    for i, p in enumerate(new_parts):
        score.insert(i, p)
    for s in new_staves:
        score.append(s)
    return parts


def revoice_by_system(
    root: etree._Element,
    input_key: Optional[str] = None,
    can_prompt: bool = True,
) -> List[str]:
    """
    Run the full per-system flow (prompt + rebuild). Returns the ordered part names.

    If `input_key` is given, cached answers for that input are loaded as prompt
    defaults and the chosen answers are saved back. When `can_prompt` is False
    (non-TTY / non-interactive), a complete cache is required and used directly;
    without one, nothing is rebuilt.
    """
    systems = find_systems(root)
    if not systems:
        return []
    cache = load_answer_cache(input_key) if input_key else None
    if can_prompt:
        decls, answers = prompt_system_decls(root, systems, cache=cache)
        if input_key:
            save_answer_cache(input_key, answers)
    elif cache:
        logger.info("Per-system: using cached answers for %s", input_key)
        decls = decls_from_answers(root, systems, cache)
    else:
        logger.warning(
            "Per-system needs a terminal to prompt, or a cached answer set for '%s'.",
            input_key,
        )
        return []
    return build_parts(root, systems, decls)
