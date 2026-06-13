#!/usr/bin/env python3
"""
Interactive re-voicing of OCR'd scores.

OCR'd MusicXML often has measures where a staff briefly carries more voices than
usual (a chord exploded into several voices, or a real extra part for a few bars).
The splitter only handles an upper/lower pair per staff, so anything beyond two
voices needs a decision the tool cannot make on its own.

Flow (only when interactive and stdin is a TTY):
  1. Establish a named baseline: the user names the normal voices top-to-bottom
     (e.g. "T1, T2, B"). This maps each name to a source staff.
  2. For each measure with more than two voices, show the current voicing and the
     voices (with their notes), and ask the user for the new voicing — one name per
     voice, in voice order. A blank name drops that voice.
  3. Voices labelled with a name from this staff's baseline stay in the staff (so the
     normal split still works). The others are captured and, after the split, either
     moved into the named existing part's staff or placed on a brand-new staff.

Non-interactive callers should use `reduce_voice_anomalies` instead.
"""

import sys
from collections import Counter
from copy import deepcopy
from typing import Dict, List, Optional

from lxml import etree

import logging

logger = logging.getLogger(__name__)

MAX_NORMAL_VOICES = 2
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_name(pitch_text: Optional[str]) -> str:
    try:
        p = int(pitch_text)
    except (TypeError, ValueError):
        return str(pitch_text)
    return f"{_NOTE_NAMES[p % 12]}{p // 12 - 1}"


def _voice_summary(voice: etree._Element) -> str:
    """Short description of a voice's notes, e.g. 'E4,E4,D4,C#4' or '(rest)'."""
    pitches = [
        _midi_name(n.findtext("pitch"))
        for chord in voice.findall("Chord")
        for n in chord.findall("Note")
    ]
    return ",".join(pitches) if pitches else "(rest)"


def _staff_label(root: etree._Element, staff_id: int) -> str:
    for part in root.findall(".//Part"):
        staff = part.find(".//Staff")
        if staff is not None and staff.get("id") == str(staff_id):
            track = part.find("trackName")
            if track is not None and track.text:
                return track.text.strip()
            break
    return f"staff {staff_id}"


def _modal_voice_count(staff: etree._Element) -> int:
    counts = [len(m.findall("voice")) for m in staff.findall("Measure")]
    counts = [c for c in counts if c > 0]
    return Counter(counts).most_common(1)[0][0] if counts else 1


def _measure_has_notes(measure: etree._Element) -> bool:
    return measure.find(".//Chord") is not None


def establish_baseline(root: etree._Element) -> Optional[Dict]:
    """
    Ask the user to name the normal (baseline) voices top-to-bottom.

    Returns {'name_to_staff': {name: source_staff_id},
             'staff_to_names': {source_staff_id: [names per voice]}}
    or None if there is nothing to do.
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    if score is None:
        return None
    staves = score.findall("Staff")
    if not staves:
        return None
    # One slot per modal voice of each source staff, in document (top-to-bottom) order.
    slots = []  # list of (staff_id, label_for_display)
    for staff in staves:
        sid = int(staff.get("id", "0"))
        modal = max(1, min(MAX_NORMAL_VOICES, _modal_voice_count(staff)))
        for _ in range(modal):
            slots.append(sid)

    print("\nName the normal voices of this score, top to bottom.", file=sys.stderr)
    for staff in staves:
        sid = int(staff.get("id", "0"))
        modal = max(1, min(MAX_NORMAL_VOICES, _modal_voice_count(staff)))
        print(f"   {_staff_label(root, sid)}: {modal} voice(s)", file=sys.stderr)
    while True:
        raw = input(f"   Enter {len(slots)} names, comma-separated (top to bottom): ")
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if len(names) == len(slots):
            break
        print(
            f"   Got {len(names)} names, expected {len(slots)}. Try again.",
            file=sys.stderr,
        )
    name_to_staff: Dict[str, int] = {}
    staff_to_names: Dict[int, List[str]] = {}
    for name, sid in zip(names, slots):
        name_to_staff[name] = sid
        staff_to_names.setdefault(sid, []).append(name)
    return {"name_to_staff": name_to_staff, "staff_to_names": staff_to_names}


def _active_voicing(root: etree._Element, baseline: Dict, measure_index: int) -> List[str]:
    """Baseline names whose staff actually has notes at this measure (top to bottom)."""
    score = root if root.tag == "Score" else root.find(".//Score")
    active: List[str] = []
    for staff in score.findall("Staff"):
        sid = int(staff.get("id", "0"))
        names = baseline["staff_to_names"].get(sid)
        if not names:
            continue
        measures = staff.findall("Measure")
        if measure_index < len(measures) and _measure_has_notes(measures[measure_index]):
            active.extend(names)
    return active


def _prompt_measure(
    root: etree._Element,
    baseline: Dict,
    staff_label: str,
    measure_index: int,
    voices: List[etree._Element],
) -> List[str]:
    """Show the measure's voices and read one name per voice (blank = drop)."""
    active = _active_voicing(root, baseline, measure_index)
    print(
        f"\n⚠  Measure {measure_index + 1}, staff '{staff_label}': {len(voices)} voices.",
        file=sys.stderr,
    )
    print(f"   Current voicing here: {', '.join(active) if active else '(none)'}", file=sys.stderr)
    for i, voice in enumerate(voices, start=1):
        print(f"     voice {i}: {_voice_summary(voice)}", file=sys.stderr)
    while True:
        raw = input(
            f"   New voicing — {len(voices)} names, in voice order (blank = drop): "
        )
        labels = [n.strip() for n in raw.split(",")]
        # Pad/truncate to the number of voices; empty strings mean "drop this voice".
        labels = (labels + [""] * len(voices))[: len(voices)]
        return labels


def capture_revoice_plan(root: etree._Element, baseline: Dict) -> List[Dict]:
    """
    Walk every source staff; for each measure with >2 voices, prompt the user and:
      - keep voices labelled with one of this staff's baseline names (reordered to
        baseline order so the splitter sees a clean upper/lower pair),
      - capture the rest into the plan as 'new' (unknown name) or 'move' (a name that
        belongs to another staff), dropping blank-labelled voices.
    Returns a list of plan entries:
      {'kind': 'new'|'move', 'label': str, 'measure_index': int, 'voice': <deepcopy>}
    Modifies the source measures in place (reduces them to their kept voices).
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    plan: List[Dict] = []
    name_to_staff = baseline["name_to_staff"]
    for staff in score.findall("Staff"):
        sid = int(staff.get("id", "0"))
        this_names = baseline["staff_to_names"].get(sid, [])
        label = _staff_label(root, sid)
        for mi, measure in enumerate(staff.findall("Measure")):
            voices = measure.findall("voice")
            if len(voices) <= MAX_NORMAL_VOICES:
                continue
            labels = _prompt_measure(root, baseline, label, mi, voices)
            kept_by_name: Dict[str, etree._Element] = {}
            for voice, name in zip(voices, labels):
                if not name:
                    measure.remove(voice)  # drop
                elif name in this_names:
                    kept_by_name[name] = voice  # stays in this staff
                elif name in name_to_staff:
                    plan.append({"kind": "move", "label": name,
                                 "measure_index": mi, "voice": deepcopy(voice)})
                    measure.remove(voice)
                else:
                    plan.append({"kind": "new", "label": name,
                                 "measure_index": mi, "voice": deepcopy(voice)})
                    measure.remove(voice)
            # Reorder the kept voices into baseline order so the splitter's
            # voice0/voice1 line up with the named upper/lower parts.
            for voice in measure.findall("voice"):
                measure.remove(voice)
            for name in this_names:
                if name in kept_by_name:
                    measure.append(kept_by_name[name])
    return plan


def _new_output_staff(root: etree._Element, label: str) -> int:
    """
    Create a new output Part+Staff (rests in every measure), modelled on the first
    existing Part/Staff. Returns the new staff id.
    """
    score = root if root.tag == "Score" else root.find(".//Score")
    existing_ids = [int(s.get("id", "0")) for s in score.findall("Staff")]
    new_id = max(existing_ids, default=0) + 1
    template_part = score.find("Part")
    template_staff = score.find("Staff")
    new_part = deepcopy(template_part)
    ps = new_part.find(".//Staff")
    if ps is not None:
        ps.set("id", str(new_id))
    for tag in ("trackName",):
        el = new_part.find(tag)
        if el is not None:
            el.text = label
    for tag in ("longName", "shortName", "trackName"):
        el = new_part.find(f".//Instrument/{tag}")
        if el is not None:
            el.text = label
    parts = score.findall("Part")
    if parts:
        score.insert(score.index(parts[-1]) + 1, new_part)
    else:
        score.insert(0, new_part)

    new_staff = deepcopy(template_staff)
    new_staff.set("id", str(new_id))
    vbox = new_staff.find("VBox")
    if vbox is not None:
        new_staff.remove(vbox)
    # Turn every chord into a rest and strip lyrics so the staff is silent by default.
    for chord in new_staff.findall(".//Chord"):
        voice = chord.getparent()
        if voice is None:
            continue
        dur = chord.findtext("durationType") or "quarter"
        rest = etree.Element("Rest")
        etree.SubElement(rest, "durationType").text = dur
        voice.insert(voice.index(chord), rest)
        voice.remove(chord)
    for lyrics in new_staff.findall(".//Lyrics"):
        lyrics.getparent().remove(lyrics)
    score.append(new_staff)
    return new_id


def apply_revoice_plan(
    root: etree._Element, plan: List[Dict], baseline: Dict,
    printed_to_output: Dict[int, List[int]],
) -> None:
    """
    Apply captured voices to the final (split) score. 'move' adds the voice to the
    named part's output staff at that measure; 'new' places it on a fresh staff
    (created once per new label) that is otherwise rests.
    """
    if not plan:
        return
    score = root if root.tag == "Score" else root.find(".//Score")

    def staff_by_id(out_id: int) -> Optional[etree._Element]:
        for s in score.findall("Staff"):
            if s.get("id") == str(out_id):
                return s
        return None

    new_label_to_id: Dict[str, int] = {}
    for entry in plan:
        label = entry["label"]
        mi = entry["measure_index"]
        voice = entry["voice"]
        for lyrics in voice.findall(".//Lyrics"):
            lyrics.getparent().remove(lyrics)
        if entry["kind"] == "move":
            src_staff = baseline["name_to_staff"].get(label)
            outs = printed_to_output.get(src_staff) if src_staff else None
            target = staff_by_id(outs[0]) if outs else None
            if target is None:
                logger.warning("Could not resolve move target for '%s'; skipping.", label)
                continue
            measures = target.findall("Measure")
            if mi < len(measures):
                measures[mi].append(voice)  # add as an extra voice at this measure
        else:  # new
            if label not in new_label_to_id:
                new_label_to_id[label] = _new_output_staff(root, label)
            target = staff_by_id(new_label_to_id[label])
            if target is None:
                continue
            measures = target.findall("Measure")
            if mi < len(measures):
                for v in measures[mi].findall("voice"):
                    measures[mi].remove(v)
                measures[mi].append(voice)
