"""Evidence shown before recording and before human-controlled upload."""

from __future__ import annotations

from collections import Counter
import json
import math
import os
import subprocess
from typing import Dict, Iterable, List

from lxml import etree

from . import state

COMBINED_PART = "ALL"


def _result(status: str, detail: str, **extra) -> Dict:
    return {"status": status, "detail": detail, **extra}


def singing_parts(mscx_path: str) -> List[str]:
    if not mscx_path or not os.path.exists(mscx_path):
        return []
    from src.scrollvideo.audio import part_names
    from src.scrollvideo.score import silent_parts

    root = etree.parse(mscx_path).getroot()
    silent = set(silent_parts(root))
    return [name for name in part_names(root) if name not in silent]


def _note_events(path: str) -> Counter:
    root = etree.parse(path).getroot()
    score = root.find(".//Score") if root.tag != "Score" else root
    events = Counter()
    for staff in score.findall("Staff"):
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
    return _result(
        "warning",
        f"Source has {source_count} note events and cleaned score has {cleaned_count}; "
        "pitch, measure, or duration differs.",
        source_notes=source_count,
        cleaned_notes=cleaned_count,
    )


def _fraction(value: str) -> float:
    try:
        a, b = value.split("/", 1)
        return float(a) / float(b) if float(b) else 0.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


def probe_file(path: str) -> Dict:
    signature = {
        "size": os.path.getsize(path),
        "mtime_ns": os.stat(path).st_mtime_ns,
    }
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _result("warning", f"Could not inspect file: {exc}", **signature)
    if result.returncode:
        reason = (result.stderr or "ffprobe failed").strip().splitlines()[-1]
        return _result("warning", reason, **signature)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return _result("warning", "ffprobe returned unreadable data.", **signature)
    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    raw_duration = payload.get("format", {}).get("duration")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        duration = 0.0
    fps = _fraction((video or {}).get("avg_frame_rate"))
    valid = bool(video and audio and math.isfinite(duration) and duration > 0)
    detail = (f"{video.get('width')}×{video.get('height')}, {fps:.2f} fps, "
              f"{duration:.2f}s, picture and sound present") if valid else \
             "File must contain picture, sound, and a positive duration."
    return _result(
        "passed" if valid else "warning", detail,
        width=(video or {}).get("width"), height=(video or {}).get("height"),
        fps=fps, duration=duration, video=bool(video), audio=bool(audio), **signature,
    )


def verify_media(song: state.Song, outputs: Iterable[str], parts: Iterable[str]) -> Dict:
    expected = list(parts)
    if len(expected) > 1:
        expected.append(COMBINED_PART)
    by_part: Dict[str, Dict] = {}
    prefix = song.slug + " "
    for name in outputs:
        path = song.path("media", "video", os.path.basename(name))
        label = os.path.splitext(os.path.basename(name))[0]
        if label.startswith(prefix):
            label = label[len(prefix):]
        if not os.path.exists(path):
            by_part[label] = _result("warning", "File is missing.")
        else:
            by_part[label] = probe_file(path)
        by_part[label]["name"] = os.path.basename(name)
    missing = [part for part in expected if part not in by_part]
    unexpected = [part for part in by_part if part not in expected]
    invalid = [part for part in expected
               if part in by_part and by_part[part].get("status") != "passed"]
    ok = not missing and not invalid and not unexpected
    detail = (f"{len(by_part)} output(s); all {len(expected)} expected videos passed file checks."
              if ok else f"Missing: {', '.join(missing) or 'none'}; failed checks: "
              f"{', '.join(invalid) or 'none'}; unexpected: {', '.join(unexpected) or 'none'}. "
              f"Found {len(by_part)} output(s), expected {len(expected)}.")
    return _result("passed" if ok else "warning", detail,
                   expected=expected, expected_count=len(expected), output_count=len(by_part),
                   missing=missing, unexpected=unexpected, files=by_part)


def summary(song: state.Song, systems: int) -> Dict:
    cleaned = song.cleaned_path()
    current = state.file_fingerprint(cleaned) if cleaned else None
    health = song.data.get("health", {})
    if not current:
        health_result = _result("not_checked", "No cleaned score exists.")
    elif health.get("checked_against") != current:
        health_result = _result("stale", "Health was not checked against the current cleaned score.")
    else:
        open_count = sum(1 for issue in health.get("issues", [])
                         if issue.get("status") == "open")
        health_result = _result("passed" if not open_count else "warning",
                                f"Current score checked; {open_count} open issue(s).")

    stored = song.data.get("verification", {}).get("notes")
    if not stored:
        notes = _result("not_checked", "Source-to-cleaned notes were not compared.")
    elif stored.get("checked_against") != current:
        notes = _result("stale", "Note comparison applies to an older cleaned score.")
    else:
        notes = {k: v for k, v in stored.items() if k != "checked_against"}

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
    record = song.data.get("record", {})
    media = record.get("verification")
    if not record.get("outputs"):
        expected = parts + ([COMBINED_PART] if len(parts) > 1 else [])
        media_result = _result("not_checked", "Videos have not been rendered.",
                               expected=expected, files={})
    elif record.get("rendered_against") != current:
        expected = parts + ([COMBINED_PART] if len(parts) > 1 else [])
        media_result = _result("stale", "Videos were rendered from an older cleaned score.",
                               expected=expected, files=(media or {}).get("files", {}))
    elif not media:
        expected = parts + ([COMBINED_PART] if len(parts) > 1 else [])
        media_result = _result("not_checked", "Rendered files have not been inspected.",
                               expected=expected, files={})
    else:
        media_result = dict(media)
        for result in media_result.get("files", {}).values():
            name = result.get("name")
            path = song.path("media", "video", name) if name else ""
            if not path or not os.path.exists(path):
                result.update(status="warning", detail="File is missing.")
                media_result["status"] = "warning"
            elif (result.get("size"), result.get("mtime_ns")) != \
                    (os.path.getsize(path), os.stat(path).st_mtime_ns):
                result.update(status="stale", detail="File changed after it was inspected.")
                media_result["status"] = "stale"
        if media_result.get("status") != "passed":
            media_result["detail"] = "One or more rendered files are missing, changed, or failed checks."

    return {
        "cleaned_fingerprint": current,
        "health": health_result,
        "notes": notes,
        "lyrics": lyric_result,
        "expected_parts": parts,
        "systems": systems,
        "media": media_result,
        "render_error": record.get("error"),
    }
