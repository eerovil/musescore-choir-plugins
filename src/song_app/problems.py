"""Every problem the Fix stage knows of, one row per bar and part, with its choices (#290).

The Fix panel used to say the same thing up to three times — a sentence from
`fixes.json`, a red-mark health row, and the bar again under "Unsure bars" — and only
the last offered anything to tap. This puts them together: a row is a bar of one part,
it says everything wrong there, and where there is something to choose between it
offers a/b/c beside the page crop.

Where each part comes from, and why:

- **Red marks are read live off the cleaned score** (`problem_marks.marks`), not off
  the `clean-marker` sentences, which are only rewritten at the next clean: a pick
  that takes a mark off would otherwise leave its sentence showing. A mark whose
  health row was dismissed stays dismissed.
- **Other health findings** come from the health record, with their Dismiss.
- **Sentences** in `fixes.json` other than the red marks' own copies: a bar MuseScore
  refused, a rest the scan moved, a sentence somebody typed. A sentence with no bar
  is a row of its own.
- **Choices** are homr's readings of an unsure bar as whole bars — lengths and pitches
  together, and the second reading of the bar when homr has one (`bar_readings.offers`,
  #295) — and for a slur cleaning took out because it ran from
  one singer into another, the slur back in either singer, both, or none — worked out
  here from where cleaning recorded the two halves (`cross_voice_slurs.removed_slurs`).

Nothing here decides anything about the music: the rows are what is already known,
put side by side.
"""
import json
import os
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from lxml import etree

from src.clean_score.utils import score_fixes
from src.clean_score.utils.cross_voice_slurs import removed_slurs
from src.clean_score.utils.problem_marks import marks
from src.clean_score.utils.rejected_bars import staff_names
from src.clean_score.utils.score_fixes import FixError

from . import bar_readings, pdf_systems, pipeline, state

#: What marks a `fixes.json` entry as a slur choice made here.
SLUR_SOURCE = "slur-choice"


def _system_of(bounds: List[Tuple[int, int, int]], measure: Optional[int]) -> Optional[int]:
    if measure is None:
        return None
    for index, start, end in bounds:
        if start <= measure <= end:
            return index
    return None


def _slur_id(rec: Dict) -> str:
    return (f"slur-{rec['staff']}-{rec['measure']}-{rec['pos']}"
            f"-{rec['end_staff']}-{rec['end_measure']}-{rec['end_pos']}")


def _chord_at(root: etree._Element, sid: int, measure: int, pos: Fraction):
    """(index, name, slurred) of the chord starting at `pos` of a bar, or None."""
    try:
        body = score_fixes._measure(root, sid, measure)
        body = body.find("voice") if body.find("voice") is not None else body
        timeline = score_fixes._timeline(body)
    except FixError:
        return None
    index = 0
    for el, at, _ in timeline:
        if el.tag != "Chord":
            continue
        if at == pos:
            note = el.find("Note")
            name = "?"
            if note is not None and (note.findtext("pitch") or "").strip().isdigit():
                tpc = (note.findtext("tpc") or "").strip()
                name = score_fixes.note_name(
                    int(note.findtext("pitch")),
                    int(tpc) if tpc.lstrip("-").isdigit() else None)
            slurred = any(sp.find("next") is not None
                          for sp in el.findall("Spanner[@type='Slur']"))
            return index, name, slurred
        index += 1
    return None


def _slur_options(root: etree._Element, rec: Dict) -> List[Dict]:
    """Slur in the first singer, the second, both, or none — whichever can be drawn."""
    staves = root.findall(".//Score/Staff")
    names = staff_names(root)
    p1, p2 = Fraction(rec["pos"]), Fraction(rec["end_pos"])
    singers = []
    for position in (rec["staff"], rec["end_staff"]):
        if not 1 <= position <= len(staves):
            continue
        sid = int(staves[position - 1].get("id") or position)
        start = _chord_at(root, sid, rec["measure"], p1)
        end = _chord_at(root, sid, rec["end_measure"], p2)
        if start is None or end is None:
            continue
        if (rec["end_measure"], end[0]) <= (rec["measure"], start[0]):
            continue
        singers.append({"staff": sid, "part": names.get(position, f"staff {position}"),
                        "index": start[0], "end_index": end[0], "slurred": start[2],
                        "label": f"{start[1]} → {end[1]}"})
    options = [{"slurs": [s], "label": f"Slur in {s['part']} ({s['label']})"} for s in singers]
    if len(singers) == 2:
        options.append({"slurs": singers,
                        "label": f"Slur in both {singers[0]['part']} and {singers[1]['part']}"})
    options.append({"slurs": [], "label": "No slur here on the page"})
    for letter, option in zip(bar_readings.LETTERS, options):
        option["letter"] = letter
    return options


def _slur_marks(rec: Dict) -> List[Tuple[int, int, str]]:
    """The two red marks cleaning left for one removed slur: (staff, measure, text)."""
    start_at = f"{rec['part']} bar {rec['measure']}"
    end_at = f"{rec['end_part']} bar {rec['end_measure']}"
    return [(rec["staff"], rec["measure"], f"slur to {end_at} removed; check the page"),
            (rec["end_staff"], rec["end_measure"], f"slur from {start_at} removed; check the page")]


def _slur_decisions(song_dir: str) -> Dict[str, str]:
    return {fix["offer"]: fix.get("choice", "?") for fix in pipeline._recorded_fixes(song_dir)
            if fix.get("source") == SLUR_SOURCE and fix.get("offer")}


def problems(song: state.Song) -> List[Dict]:
    """Every problem of the cleaned score, one row per bar and part, ordered by bar.

    A row: `id`, `measure` (None for a sentence about no bar), `part`, `system` (the
    printed system to crop, or None), `notes` (each `{text, kind, dismiss}`: what is
    wrong, and the health row to dismiss when there is one) and `choices` (each `{id,
    kind, title, options, shown, decision}`, options `{letter, label, current, svg}`;
    `shown` is how many to show before "More", absent when all are shown), plus
    `bar_in_system` / `bars_in_system`: which bar of its printed system the row is
    about, and how many that system holds (#310) — the crop shows the whole line, so
    without them a person counts bars to find the one meant. Both are None when the
    bar or the system's range is not known.
    """
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return []
    root = etree.parse(cleaned).getroot()
    names = staff_names(root)
    order = {name: position for position, name in names.items()}
    bounds = [(b.index, b.measure_start, b.measure_end)
              for b in pdf_systems.load_bounds(song.dir) if b.measure_start]
    rows: Dict[Tuple, Dict] = {}

    def row(measure: Optional[int], part: str, system: Optional[int] = None) -> Dict:
        key = (measure, part)
        if key not in rows:
            rows[key] = {"id": f"m{measure}-{part}" if measure else f"note-{len(rows)}",
                         "measure": measure, "part": part,
                         "system": system if system is not None else _system_of(bounds, measure),
                         "notes": [], "choices": []}
        return rows[key]

    def note(target: Dict, text: str, kind: str, dismiss: Optional[str] = None) -> None:
        if not any(n["text"] == text for n in target["notes"]):
            target["notes"].append({"text": text, "kind": kind, "dismiss": dismiss})

    issues = song.data.get("health", {}).get("issues", [])
    marked = {(i.get("measure"), i.get("staff"), i.get("detail")): i for i in issues
              if i.get("kind") == "marked-problem"}

    # Each removed slur is one question about two bars, so it is asked once, on the
    # bar it started in, with the other bar's mark said there too.
    folded: Dict[Tuple[int, int, str], Tuple[int, str]] = {}
    slurs = [rec for rec in removed_slurs(root)
             if rec.get("measure") and rec.get("end_measure") and rec.get("pos") is not None
             and rec.get("end_pos") is not None]
    for rec in slurs:
        end_mark = _slur_marks(rec)[1]
        folded[end_mark] = (rec["measure"], names.get(rec["staff"], ""))

    for mark in marks(root):
        part = names.get(mark["staff"], f"staff {mark['staff']}")
        health_row = marked.get((mark["measure"], part, mark["text"]))
        if health_row is not None and health_row.get("status") == "dismissed":
            continue
        dismiss = health_row["id"] if health_row and health_row.get("status") == "open" else None
        home = folded.get((mark["staff"], mark["measure"], mark["text"]))
        if home is not None:
            note(row(*home), f"Bar {mark['measure']}, {part}: {mark['text']}", "mark", dismiss)
        else:
            note(row(mark["measure"], part), mark["text"], "mark", dismiss)

    for issue in issues:
        if issue.get("status") != "open" or issue.get("kind") == "marked-problem":
            continue
        note(row(issue.get("measure"), issue.get("staff") or ""),
             issue.get("detail") or issue.get("kind", ""), issue.get("kind", ""), issue["id"])

    try:
        recorded = pipeline._recorded_fixes(song.dir)
    except (RuntimeError, FixError, OSError):
        recorded = []
    for fix in recorded:
        if not isinstance(fix, dict) or fix.get("kind") != "text":
            continue
        if fix.get("source") == pipeline.CLEAN_MARK_SOURCE:
            continue  # the mark itself is read live above
        text = (fix.get("what") or fix.get("why") or "").strip()
        if not text:
            continue
        measure = fix.get("measure")
        part = names.get(fix.get("staff"), "") if fix.get("staff") else ""
        system = fix.get("system") if measure is None else None
        note(row(measure, part, system), text, fix.get("source") or "fixes.json")

    for offer in bar_readings.offers(song, cleaned):
        target = row(offer["measure"], offer["part"] or "")
        if target["system"] is None:
            target["system"] = offer["system"]
        title = "Which bar does the page print?"
        options = [{"letter": o["letter"], "current": o["current"],
                    "label": "second reading" if o["second"] else ""}
                   for o in offer["options"]]
        for option in options:
            option["svg"] = f"readings/{offer['id']}/{option['letter']}.svg"
        target["choices"].append({"id": offer["id"], "kind": offer["kind"],
                                  "title": title, "options": options,
                                  "shown": bar_readings.SHOWN,
                                  "decision": offer["decision"], "can_decline": True})

    decided = _slur_decisions(song.dir)
    for rec in slurs:
        cid = _slur_id(rec)
        options = _slur_options(root, rec)
        target = row(rec["measure"], names.get(rec["staff"], ""))
        decision = {"picked": decided[cid]} if cid in decided else None
        target["choices"].append({
            "id": cid, "kind": "slur",
            "title": (f"The scan ran a slur from {rec['part']} bar {rec['measure']} into "
                      f"{rec['end_part']} bar {rec['end_measure']}. What does the page print?"),
            "options": [{"letter": o["letter"], "label": o["label"], "current": False,
                         "svg": None} for o in options],
            "decision": decision, "can_decline": False})

    ranges = {index: (start, end) for index, start, end in bounds}
    for target in rows.values():
        start, end = ranges.get(target["system"], (0, 0))
        inside = bool(target["measure"]) and start <= target["measure"] <= end
        target["bar_in_system"] = target["measure"] - start + 1 if inside else None
        target["bars_in_system"] = end - start + 1 if inside else None

    return sorted(rows.values(), key=lambda r: (
        r["measure"] is None, r["measure"] or 0, order.get(r["part"], 99), r["part"]))


def record_slur_choice(song: state.Song, cid: str, letter: str) -> Dict:
    """Apply a person's answer about a removed slur, and record it in fixes.json.

    The slur (or slurs) go on the cleaned score and both red marks come off; each is a
    `fixes.json` entry, so a re-clean — which takes the slur out and marks the bars
    again — puts the answer back. "No slur" is the marks coming off alone. As
    `pipeline.record_slur_fix`: applied to a parsed copy first, so a refusal changes
    nothing, and the file is written before the score.
    """
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise FixError("clean the score first")
    root = etree.parse(cleaned).getroot()
    rec = next((r for r in removed_slurs(root)
                if r.get("pos") is not None and r.get("end_pos") is not None
                and _slur_id(r) == cid), None)
    if rec is None:
        raise FixError("that slur is not on offer any more — the score has been cleaned "
                       "again since the page was loaded")
    if cid in _slur_decisions(song.dir):
        raise FixError("that slur has already been decided")
    option = next((o for o in _slur_options(root, rec) if o["letter"] == letter), None)
    if option is None:
        raise FixError(f"there is no option {letter!r}")
    if any(slur["slurred"] for slur in option["slurs"]):
        raise FixError("that note already starts a slur — the score has one there now")
    why = f"picked {letter} ({option['label']}) against the page"
    entries: List[Dict] = []
    for slur in option["slurs"]:
        entries.append({"kind": "slur", "source": SLUR_SOURCE, "offer": cid, "choice": letter,
                        "staff": slur["staff"], "part": slur["part"],
                        "measure": rec["measure"], "index": slur["index"],
                        "end_measure": rec["end_measure"], "end_index": slur["end_index"],
                        "why": why})
    staves = root.findall(".//Score/Staff")
    for position, measure, text in _slur_marks(rec):
        sid = int(staves[position - 1].get("id") or position)
        entries.append({"kind": "unmark", "source": SLUR_SOURCE, "offer": cid, "choice": letter,
                        "staff": sid, "measure": measure, "text": text, "why": why})
    applied = score_fixes.apply_fixes(root, entries)
    recorded = pipeline._recorded_fixes(song.dir) + entries
    with open(os.path.join(song.dir, "fixes.json"), "w", encoding="utf-8") as fh:
        json.dump(recorded, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    etree.ElementTree(root).write(cleaned, encoding="UTF-8", xml_declaration=True)
    return {"choice": cid, "applied": "; ".join(applied)}
