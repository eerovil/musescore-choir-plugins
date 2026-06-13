#!/usr/bin/env python3
"""
Interactive resolution of voice-count anomalies in OCR'd scores.

A measure with more than two voices is beyond what the splitter handles (it splits a
staff into an upper/lower pair). It can be an OCR glitch (a chord exploded into several
voices) OR a real split (e.g. tenor and bass each dividing). The tool cannot tell which,
so when run interactively it stops at each such measure, prints what it found, and lets
the user say what is happening and which voices to keep.
"""

import sys
from collections import Counter
from typing import Dict, List, Optional

from lxml import etree

import logging

logger = logging.getLogger(__name__)

# A measure with more voices than this is treated as an anomaly to resolve.
MAX_NORMAL_VOICES = 2


def _staff_label(root: etree._Element, staff_id: int) -> str:
    """Human-readable label for a staff from its Part trackName, e.g. 'Track 1'."""
    for part in root.findall(".//Part"):
        staff = part.find(".//Staff")
        if staff is not None and staff.get("id") == str(staff_id):
            track = part.find("trackName")
            if track is not None and track.text:
                return track.text.strip()
            break
    return f"staff {staff_id}"


def _find_anomalies(staff: etree._Element) -> List[int]:
    """Return 0-based measure indices whose voice count exceeds MAX_NORMAL_VOICES."""
    return [
        i
        for i, measure in enumerate(staff.findall("Measure"))
        if len(measure.findall("voice")) > MAX_NORMAL_VOICES
    ]


def _modal_voice_count(staff: etree._Element) -> int:
    counts = [len(m.findall("voice")) for m in staff.findall("Measure")]
    counts = [c for c in counts if c > 0]
    return Counter(counts).most_common(1)[0][0] if counts else 1


def _keep_voices(measure: etree._Element, keep: List[int]) -> None:
    """Keep only the 1-based voice positions in `keep`; remove the rest from the measure."""
    voices = measure.findall("voice")
    for idx, voice in enumerate(voices, start=1):
        if idx not in keep:
            measure.remove(voice)


def _prompt_for_measure(
    label: str, measure_number: int, n_voices: int, modal: int
) -> List[int]:
    """
    Show the measure and ask the user what is happening. Returns 1-based voice
    positions to keep (the splitter handles two voices per staff: upper/lower).
    """
    print(
        f"\n⚠  {label}, measure {measure_number}: {n_voices} voices "
        f"(most measures here have {modal}).",
        file=sys.stderr,
    )
    print("   What is happening here?", file=sys.stderr)
    print(f"     [1] OCR added extra voices — keep the first {modal}", file=sys.stderr)
    print(f"     [2] Real voices — keep all {n_voices} (note: only the top 2 are split per staff)", file=sys.stderr)
    print("     [3] Let me pick which voices to keep", file=sys.stderr)
    while True:
        choice = input("   > ").strip() or "1"
        if choice == "1":
            return list(range(1, modal + 1))
        if choice == "2":
            return list(range(1, n_voices + 1))
        if choice == "3":
            raw = input(f"   Enter voice numbers to keep (1-{n_voices}, e.g. 1 2): ")
            picked = []
            for tok in raw.replace(",", " ").split():
                if tok.isdigit() and 1 <= int(tok) <= n_voices:
                    picked.append(int(tok))
            if picked:
                return sorted(set(picked))
            print("   No valid voice numbers; try again.", file=sys.stderr)
            continue
        print("   Please enter 1, 2 or 3.", file=sys.stderr)


def resolve_voice_anomalies(root: etree._Element, interactive: bool) -> None:
    """
    Find measures with more than two voices and reduce them.

    interactive=True: prompt the user per anomalous measure (only when stdin is a TTY).
    interactive=False (or no TTY): log a warning and keep the first `modal` voices so the
    splitter has clean input; nothing is asked.
    Modifies the tree in place.
    """
    use_prompt = interactive and sys.stdin.isatty()
    for staff in root.findall(".//Score/Staff"):
        anomalies = _find_anomalies(staff)
        if not anomalies:
            continue
        staff_id = int(staff.get("id", "0"))
        label = _staff_label(root, staff_id)
        modal = max(1, min(MAX_NORMAL_VOICES, _modal_voice_count(staff)))
        measures = staff.findall("Measure")
        for mi in anomalies:
            measure = measures[mi]
            n_voices = len(measure.findall("voice"))
            if use_prompt:
                keep = _prompt_for_measure(label, mi + 1, n_voices, modal)
            else:
                keep = list(range(1, modal + 1))
                logger.warning(
                    "%s, measure %d: %d voices (expected %d); keeping first %d. "
                    "Run clean_score with --interactive to choose.",
                    label, mi + 1, n_voices, modal, modal,
                )
            _keep_voices(measure, keep)
