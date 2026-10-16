"""Evidence shown at Review, before a person approves the score for publishing."""

from __future__ import annotations

from collections import Counter
import os
from typing import Dict, Iterable, List, Optional

from lxml import etree

from src.clean_score.utils.utils import part_staff_ids

from . import health as health_check
from . import state

def _result(status: str, detail: str, **extra) -> Dict:
    return {"status": status, "detail": detail, **extra}


def singing_parts(mscx_path: str) -> List[str]:
    if not mscx_path or not os.path.exists(mscx_path):
        return []
    from src.score_export.musescore import part_names
    from src.score_export.score import silent_parts

    root = etree.parse(mscx_path).getroot()
    silent = set(silent_parts(root))
    return [name for name in part_names(root) if name not in silent]


def _note_events(path: str, only: Optional[Iterable[str]] = None) -> Counter:
    """(measure, pitch, duration) counts; `only` limits it to staves of those parts."""
    root = etree.parse(path).getroot()
    score = root.find(".//Score") if root.tag != "Score" else root
    names = {staff_id: (part.findtext("trackName") or "").strip()
             for part, owned in zip(score.findall("Part"), part_staff_ids(score))
             for staff_id in owned}
    wanted = set(only) if only is not None else None
    events = Counter()
    for staff in score.findall("Staff"):
        if wanted is not None and names.get(staff.get("id")) not in wanted:
            continue
        for measure_index, measure in enumerate(staff.findall("Measure"), 1):
            for chord in measure.findall(".//Chord"):
                duration = (
                    chord.findtext("durationType") or "",
                    len(chord.findall("dots")),
                    chord.findtext("duration") or "",
                )
                for note in chord.findall("Note"):
                    pitch = note.findtext("pitch")
                    if pitch is not None:
                        events[(measure_index, pitch, duration)] += 1
    return events


def compare_notes(source_mscx: str, cleaned_mscx: str) -> Dict:
    """Compare note pitch, measure and duration across the cleaning transform."""
    try:
        source = _note_events(source_mscx)
        cleaned = _note_events(cleaned_mscx)
    except (OSError, etree.XMLSyntaxError) as exc:
        return _result("not_checked", f"Could not compare notes: {exc}")
    source_count = sum(source.values())
    cleaned_count = sum(cleaned.values())
    if source == cleaned:
        return _result("passed", f"All {source_count} source note events are preserved by measure and duration.",
                       source_notes=source_count, cleaned_notes=cleaned_count)
    # A per-system `S1b` sings `S1`'s notes where it has none of its own, so those
    # bars hold a second copy of notes the source has once. That is the clean doing
    # what it was told; anything else missing or extra is still a warning.
    from src.clean_score.utils.per_system import _fallback_of

    fallbacks = _fallback_of(singing_parts(cleaned_mscx))
    # Each b-part copies its base on its own, so S1b and S1c borrowing one note of
    # S1's are two copies of it: count per pair, then add up.
    copies = Counter()
    for child, base in fallbacks.items():
        copies += (_note_events(cleaned_mscx, only=[child])
                   & _note_events(cleaned_mscx, only=[base]))
    if fallbacks and not (source - cleaned) and not ((cleaned - source) - copies):
        borrowed = cleaned_count - source_count
        return _result("passed", f"All {source_count} source note events are preserved by measure "
                       f"and duration; {borrowed} more are b-parts singing their base part's notes.",
                       source_notes=source_count, cleaned_notes=cleaned_count)
    return _result(
        "warning",
        f"Source has {source_count} note events and cleaned score has {cleaned_count}; "
        "pitch, measure, or duration differs.",
        source_notes=source_count,
        cleaned_notes=cleaned_count,
    )


def _musescore_result(stored: Optional[Dict], current: Optional[str]) -> Dict:
    """Whether MuseScore 3 opens the cleaned score without calling it corrupted."""
    if not current:
        return _result("not_checked", "No cleaned score exists.")
    if not stored:
        return _result("not_checked", "Not checked yet; Check again runs MuseScore 3's own check.")
    if stored.get("checked_against") != current:
        return _result("stale", "MuseScore 3's check applies to an older cleaned score.")
    status = stored.get("status")
    reset = len(stored.get("reset") or [])
    if status == "passed":
        return _result("passed", "MuseScore 3 opens it without calling it corrupted.")
    if status == "repaired":
        return _result("warning", f"MuseScore 3 called {reset} staff-bar(s) corrupted; they were "
                                  "reset to rests and are listed in the Fix panel.", reset=reset)
    if status == "rejected":
        left = len(stored.get("rejected") or [])
        still = "still calls" if reset else "calls"
        return _result("warning", f"MuseScore 3 {still} it corrupted ({left} problem(s)); "
                                  "see the Fix panel.", reset=reset, rejected=left)
    return _result("not_checked", "No MuseScore 3 was available to check it (MuseScore 4 does not run this check).")


def summary(song: state.Song, systems: int) -> Dict:
    cleaned = song.cleaned_path()
    current = state.file_fingerprint(cleaned) if cleaned else None
    health = song.data.get("health", {})
    if not current:
        health_result = _result("not_checked", "No cleaned score exists.")
    elif health.get("checked_against") != current:
        health_result = _result("stale", "Health was not checked against the current cleaned score.")
    else:
        # Count findings, not rows. A `meter-collapsed` row is one row standing for
        # many, and the whole reason it exists is that a score in that state is the
        # one most worth looking at -- so a summary that read it as 1 would put the
        # cliff straight back: B6's whole page would say "4 open issue(s)" against
        # its per-system parse's 28, which is the comparison that nearly cost a real
        # decision (#122). The rows stay collapsed in the Fix panel; the number does
        # not. Both are on the wire so the panel can say which is which.
        open_issues = [i for i in health.get("issues", []) if i.get("status") == "open"]
        open_count = health_check.finding_count(open_issues)
        folded = [i for i in open_issues if int(i.get("collapsed") or 1) > 1]
        collapsed_count = health_check.finding_count(folded)
        detail = f"Current score checked; {open_count} open issue(s)."
        if folded:
            detail += (f" {collapsed_count} of them are meter findings shown as "
                       f"{len(folded)} line(s) in the Fix panel.")
        health_result = _result("passed" if not open_count else "warning", detail,
                                open_count=open_count, row_count=len(open_issues),
                                collapsed_count=collapsed_count)

    stored = song.data.get("verification", {}).get("notes")
    if not stored:
        notes = _result("not_checked", "Source-to-cleaned notes were not compared.")
    elif stored.get("checked_against") != current:
        notes = _result("stale", "Note comparison applies to an older cleaned score.")
    else:
        notes = {k: v for k, v in stored.items() if k != "checked_against"}

    opens = _musescore_result(song.data.get("verification", {}).get("musescore"), current)

    lyrics = song.data.get("lyrics")
    if not lyrics:
        lyric_result = _result("not_checked", "Lyrics have not been imported.")
    elif lyrics.get("imported_against") != current:
        lyric_result = _result("stale", "Lyric results apply to an older cleaned score.")
    else:
        warnings = lyrics.get("warnings", [])
        lyric_result = _result("passed" if not warnings else "warning",
                               f"Current score; {len(warnings)} lyric warning(s).")

    parts = singing_parts(cleaned) if current else []

    return {
        "cleaned_fingerprint": current,
        "health": health_result,
        "notes": notes,
        "musescore": opens,
        "lyrics": lyric_result,
        "expected_parts": parts,
        "systems": systems,
    }
