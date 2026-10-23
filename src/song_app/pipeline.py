"""Glue between the web app and the existing scripts.

Conversion, cleaning (clean_score), and lyric import (lyric_txt) — driven
non-interactively. No musical logic lives here; this only orchestrates.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from . import pdf_systems, system_finder
from src import musescore_cli
from src.clean_score.main import main as clean_main
from src.clean_score import lyric_txt
from src.clean_score.lyric_txt import LyricImport, import_file
from src.clean_score.utils import per_system
from src.clean_score.utils.per_system import dropped_voices_for_file
from src.clean_score.utils.problem_marks import mark_bar, marks
from src.clean_score.utils.rejected_bars import clear_bar, staff_names
from src.clean_score.utils.score_fixes import (FixError, after_moves, apply_fixes, bar_items, voice2_tokens,
                                                bar_moves, bar_tokens, free_text, read_bar)
from src.clean_score.utils.staff_display import fix_staff_display
from src.clean_score.utils.utils import starts_new_system

MUSESCORE_EXTS = (".mscz", ".mscx", ".musicxml", ".xml")
Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


def convert_to_mscx(input_path: str, out_dir: str, log: Logger = _noop) -> str:
    """Return a .mscx path for input_path inside out_dir, converting if needed.

    .mscx -> used as-is; .mscz -> unzipped; .musicxml/.xml -> MuseScore CLI.
    """
    lower = input_path.lower()
    if lower.endswith(".mscx"):
        return input_path

    base = os.path.splitext(os.path.basename(input_path))[0]
    target = os.path.join(out_dir, base + ".mscx")

    # Reuse a previous conversion if it's still newer than the source.
    if os.path.exists(target) and os.path.getmtime(target) >= os.path.getmtime(input_path):
        return target

    if lower.endswith(".mscz"):
        log(f"Unzipping {os.path.basename(input_path)}")
        tmp = os.path.join(out_dir, "_temp_extracted")
        os.makedirs(tmp, exist_ok=True)
        try:
            with zipfile.ZipFile(input_path, "r") as zf:
                zf.extractall(tmp)
            inner = next((os.path.join(tmp, e) for e in os.listdir(tmp)
                          if e.lower().endswith(".mscx")), None)
            if not inner:
                raise RuntimeError("No .mscx found inside the .mscz archive.")
            shutil.copy2(inner, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return target

    # MusicXML -> MuseScore CLI
    log(f"Converting {os.path.basename(input_path)} with MuseScore CLI")
    cli = (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")
    result = musescore_cli.run([cli, input_path, "-o", target], target)
    if not musescore_cli.ok(result, log):
        raise RuntimeError(
            "MuseScore CLI conversion failed. Check MUSESCORE_CLI_PATH.\n"
            + result.said
        )
    return target


# --------------------------------------------------------------------------
# Taking the score away to MuseScore and bringing it back
# --------------------------------------------------------------------------
# The Fix stage's instruction is "fix it in MuseScore, save, and it re-checks
# automatically", and the button under it opens the file with `open -a` — both of
# which assume the person sitting at MuseScore is sitting at the host the app runs
# on. From a phone, or from any other machine, neither is true, so the score could
# be looked at and never edited. Downloading it and sending the fixed one back is
# the same loop over the network.
UPLOAD_EXTS = (".mscx", ".mscz")


class ScoreUploadError(ValueError):
    """An uploaded file that must not be put in place of the cleaned score."""


def _uploaded_score_summary(mscx_path: str) -> Dict:
    """What an uploaded file has to be to count as a score, and what it holds."""
    try:
        root = etree.parse(mscx_path).getroot()
    except etree.XMLSyntaxError as exc:
        raise ScoreUploadError(f"That file is not readable as MuseScore XML: {exc}")
    score = root.find("Score") if root.tag == "museScore" else None
    if score is None:
        raise ScoreUploadError("That XML is not a MuseScore score.")
    staves = [s for s in score.findall("Staff") if s.find("Measure") is not None]
    if not staves:
        raise ScoreUploadError("That score has no staves with any music in them.")
    return {"staves": len(staves),
            "measures": max(len(s.findall("Measure")) for s in staves)}


def accept_uploaded_score(cleaned_path: str, filename: str, data: bytes) -> Dict:
    """Put an uploaded score in place of the cleaned one, having checked it first.

    This is the only route that overwrites the file every later stage is derived
    from, so nothing is replaced until the upload has been parsed and found to be a
    score with music in it: a refused upload leaves the score that is there alone.
    A `.mscz` is accepted as well, because that is what MuseScore's Save As offers
    by default and noticing is not a reasonable thing to ask of somebody fixing a
    bar on their laptop.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in UPLOAD_EXTS:
        raise ScoreUploadError(
            f"Upload the score as .mscx or .mscz — '{os.path.basename(filename or 'that file')}' is neither.")
    tmp = tempfile.mkdtemp(prefix=".upload-", dir=os.path.dirname(cleaned_path))
    try:
        landed = os.path.join(tmp, "uploaded" + ext)
        with open(landed, "wb") as f:
            f.write(data)
        if ext == ".mscz":
            try:
                landed = convert_to_mscx(landed, tmp)
            except (RuntimeError, zipfile.BadZipFile) as exc:
                raise ScoreUploadError(f"Could not read that .mscz: {exc}")
        summary = _uploaded_score_summary(landed)
        # A move rather than a copy, and from a directory beside the target, so the
        # score is never half-written on disk.
        shutil.move(landed, cleaned_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return summary


def system_grid(mscx_path: str) -> List[Dict]:
    """Per-system staff layout for the clean-stage grid form.

    Returns one entry per printed system: measure range + each note-bearing staff's
    id, voice count and a short content summary, prefilled with any saved answer.
    """
    layouts = per_system.layout_for_file(mscx_path)
    out = []
    previous = None
    for layout in layouts:
        item = layout.to_dict()
        shape = [(staff.staff_id, staff.voices) for staff in layout.staves]
        item["can_reuse_previous"] = previous == shape if previous is not None else False
        out.append(item)
        previous = shape
    return out


def save_system_answers(mscx_path: str, answers: Dict[int, Dict[int, str]]) -> None:
    """Record the grid answers for this score so cleaning can run headless."""
    per_system.save_answers(mscx_path, answers)


def has_system_answers(input_path: str) -> bool:
    """True if this score has a recorded per-system answer set (so it is per-system)."""
    return per_system.has_answers(input_path)


def system_ranges(root: etree._Element) -> List[per_system.SystemRange]:
    """The score's printed systems as 1-based measure spans (for the lyric grid)."""
    return per_system.system_ranges(root)


#: Held across `clean_main`, so two cleans take turns. `clean_score` keeps its staff
#: mapping and reversed-voice table in one process-wide `GLOBALS`, and `main()` empties
#: both as it starts: a second song's clean starting 0.1s into the first one's emptied
#: the table under it, and the first died with the bare `KeyError: 3` (#357). A clean
#: is seconds; the fixes, the MuseScore check and the renders stay outside the lock.
_CLEAN_LOCK = threading.Lock()


def run_clean(
    input_path: str,
    out_dir: str,
    per_system: bool,
    add_staffs: Optional[str] = None,
    log: Logger = _noop,
    voicing: Optional[str] = None,
    check: Optional[Dict] = None,
) -> Tuple[str, str]:
    """Convert + clean. Returns (cleaned_path, mscx_intermediate_path).

    Runs non-interactively: per-system reads .persystem_cache.json; normal mode
    reduces >2-voice measures automatically (the health check flags them).
    A caller that passes `check` gets what MuseScore 3's own check said about the
    result put in it (see `check_opens_in_musescore`).
    """
    mscx_path = convert_to_mscx(input_path, out_dir, log)
    base = os.path.splitext(os.path.basename(mscx_path))[0]
    cleaned = os.path.join(out_dir, base + "_cleaned.mscx")
    log("Cleaning score" + (" (per-system)" if per_system else ""))
    if not _CLEAN_LOCK.acquire(blocking=False):
        log("Waiting for another clean to finish")
        _CLEAN_LOCK.acquire()
    # Build beside the real file and move it in only once the recorded fixes have
    # gone back on. A fix that no longer matches then leaves the previous cleaned
    # score exactly where it was, instead of a freshly rebuilt one with the
    # page-verified edits missing and nothing on disk saying so.
    building = cleaned + ".building"
    try:
        clean_main(
            mscx_path, building,
            add_staffs=add_staffs or "",
            interactive=False,
            per_system=per_system,
            voicing=voicing,
        )
    finally:
        _CLEAN_LOCK.release()
    if not os.path.exists(building):
        raise RuntimeError("Cleaning produced no output (no parts declared?).")
    try:
        apply_recorded_fixes(building, out_dir, log)
        record_clean_marks(building, out_dir, log)
        record_dropped_voices(
            out_dir, dropped_voices_for_file(mscx_path) if per_system else [],
            log)
        outcome = check_opens_in_musescore(building, out_dir, log)
    except Exception:
        os.remove(building)
        raise
    if check is not None:
        check.update(outcome)
    os.replace(building, cleaned)
    log("Cleaned score written.")
    return cleaned, mscx_path


def apply_recorded_fixes(cleaned_path: str, song_dir: str, log: Logger = _noop) -> int:
    """Re-apply the song's authorised score edits (`fixes.json`). Returns how many.

    Cleaning rebuilds the score from the source, so a hand edit made after the last
    clean is gone the moment anyone cleans again — which is how three page-verified
    rests had to be typed in twice on Kaksi laulua krapulasta. A recorded fix is a
    judgement someone already made about the printed page, so replaying it is not
    the pipeline guessing; it is the pipeline not forgetting.

    Strict on purpose: if a fix no longer matches the bar it was recorded against,
    this raises rather than skipping it. A silently dropped fix leaves a score
    looking repaired when it is not, and the whole point of the file is that nothing
    downstream can tell the difference.

    Free-text fixes are the exception, and are counted separately: nothing here can
    carry out a sentence. They are logged as still outstanding, because the two
    alternatives are both worse — refusing to clean would make the file a hostage,
    and passing over them in silence is exactly the failure it exists to prevent.
    """
    entries = _recorded_fixes(song_dir)
    if not entries:
        return 0
    tree = etree.parse(cleaned_path)
    # A picked reading follows its notes to whichever staff the grid put them on
    # (#291); the move is written back so the Fix panel and the next clean agree.
    from .bar_readings import relocate_picks
    entries, moved = relocate_picks(tree.getroot(), entries, log)
    if moved:
        _replace_recorded(song_dir, lambda fix: True, entries)
    try:
        lines = apply_fixes(tree.getroot(), entries)
    except FixError as exc:
        raise RuntimeError(
            f"A recorded fix in fixes.json no longer matches the score: {exc}. "
            "Re-read the page and update (or remove) that entry before cleaning again."
        ) from exc
    if lines:
        tree.write(cleaned_path, encoding="UTF-8", xml_declaration=True)
        log(f"Re-applied {len(lines)} recorded fix(es) from fixes.json")
        for line in lines:
            log("  " + line[:120])
    outstanding = free_text(entries)
    if outstanding:
        log(f"{len(outstanding)} fix(es) in fixes.json are free text and were NOT applied:")
        for said in outstanding:
            log("  " + said[:200])
    return len(lines)


def _recorded_fixes(song_dir: str) -> List[Dict]:
    """The song's `fixes.json`, checked over. Empty list if there is no file."""
    path = os.path.join(song_dir, "fixes.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    except ValueError as exc:
        raise RuntimeError(f"fixes.json is not valid JSON: {exc}") from exc
    if not entries:
        return []
    # Every entry has to be applicable. Skipping the ones that are not would leave a
    # score looking repaired when it is not — which is the failure this file exists
    # to prevent — and an entry with a mistyped key would vanish in silence.
    unusable = [e for e in entries if not isinstance(e, dict) or not e.get("kind")]
    if unusable:
        raise RuntimeError(
            f"{len(unusable)} entry/entries in fixes.json have no 'kind' and cannot be "
            f"applied: {unusable[:1]}. Give each one a kind, or take it out of the file.")
    return entries


# --------------------------------------------------------------------------
# Would MuseScore 3 open it?
# --------------------------------------------------------------------------
# MuseScore checks every score it opens and calls one that fails "corrupted" -- on
# Sangerhilsen the app crashed on it instead. Only the app runs that check; an export
# from the command line does not, which is why every render went through and the
# first anyone heard of it was a person opening the file. Except for one export: to
# `.mlog`, which runs the same `Score::sanityCheck` and writes what it found as JSON
# (`mscore/file.cpp`, 3.6.2). So this asks MuseScore itself rather than keeping a
# copy of its rule here that could drift from it.

#: What marks a free-text entry as written by this check rather than by a person.
MUSESCORE_CHECK_SOURCE = "musescore-check"

_REJECTION = re.compile(
    r"Measure (?P<measure>\d+), staff (?P<staff>\d+)(?:, voice (?P<voice>\d+))? "
    r"(?P<what>incomplete|too long)\. Expected: (?P<expected>\S+); Found: (?P<found>\S+)")


def musescore_check(mscx_path: str) -> Optional[List[Dict]]:
    """What MuseScore 3 objects to when it opens this score. Empty list: nothing.

    Each objection is `{measure, staff, voice, message}`, 1-based the way MuseScore
    counts (staff = position in the score, measure = every bar from the top). None
    when no MuseScore answered -- which is "not checked", never "fine". MuseScore 4
    writes no `.mlog`, so with it this is always None.
    """
    cli = (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "check.mlog")
        # MuseScore reads the format off the extension, and a clean checks the file
        # while it is still `<name>.mscx.building`.
        score = os.path.join(tmp, "check.mscx")
        shutil.copyfile(mscx_path, score)
        try:
            # Exits 1 when the score fails the check, so the exit code is not the test.
            subprocess.run([cli, score, "-o", out], capture_output=True, text=True,
                           timeout=MUSESCORE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return None
        try:
            with open(out, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
    if not data.get("result"):
        return []
    # MuseScore writes its line breaks into the JSON as a literal backslash-n.
    lines = [line.strip() for line in (data.get("error") or "").replace("\\n", "\n").split("\n")]
    found = []
    for line in filter(None, lines):
        match = _REJECTION.search(line)
        found.append({
            "measure": int(match["measure"]) if match else 0,
            "staff": int(match["staff"]) if match else 0,
            "voice": int(match["voice"] or 1) if match else 0,
            "message": line,
        })
    return found or [{"measure": 0, "staff": 0, "voice": 0,
                      "message": "MuseScore called the score corrupted without saying where."}]


def reset_rejected_bars(mscx_path: str, rejected: List[Dict]) -> List[Dict]:
    """Reset each bar MuseScore rejected to a whole-bar rest. Returns what was done.

    One entry per staff-bar: `{measure, staff, part, message, removed}`. A rejection
    that names no bar MuseScore can be pointed at is skipped and stays rejected.
    """
    tree = etree.parse(mscx_path)
    root = tree.getroot()
    names = staff_names(root)
    done: Dict[Tuple[int, int], Dict] = {}
    for one in rejected:
        key = (one["staff"], one["measure"])
        if key in done:
            done[key]["message"] += "; " + one["message"]
            continue
        staves = root.findall(".//Score/Staff")
        staff_id = staves[key[0] - 1].get("id") if 0 < key[0] <= len(staves) else None
        # The bar as the recorded fixes left it, which is what the next one meets.
        try:
            scanned_from = bar_tokens(root, int(staff_id), key[1])
            scanned_notes = read_bar(root, int(staff_id), key[1])
        except (FixError, TypeError, ValueError):
            scanned_from, scanned_notes = None, None
        removed = clear_bar(root, *key)
        if removed is None:
            continue
        mark_bar(root.findall(".//Score/Staff")[key[0] - 1].findall("Measure")[key[1] - 1],
                 f"MuseScore 3 rejected this bar; reset to a rest. Taken out: "
                 f"{' '.join(removed) or 'nothing'}")
        done[key] = {"measure": one["measure"], "staff": one["staff"],
                     "part": names.get(one["staff"], f"staff {one['staff']}"),
                     "message": one["message"], "removed": removed,
                     "staff_id": staff_id, "scanned_from": scanned_from,
                     "scanned_notes": scanned_notes}
    if done:
        tree.write(mscx_path, encoding="UTF-8", xml_declaration=True)
    return sorted(done.values(), key=lambda d: (d["measure"], d["staff"]))


def record_musescore_resets(song_dir: str, resets: List[Dict]) -> int:
    """Write each reset bar into `fixes.json` as an outstanding free-text fix.

    The notes were taken out to make the file open; a person has to put the right
    ones back from the page, and the Fix panel is where they will be told. Every
    clean re-checks the whole score, so this **replaces** all of the previous ones
    rather than adding to them -- a bar a better reading has since fixed stops being
    listed. A sentence somebody typed is never touched.
    """
    written = []
    for one in resets:
        entry = {
            "kind": "text",
            "source": MUSESCORE_CHECK_SOURCE,
            "measure": one["measure"],
            "staff": one["staff"],
            "what": (
                f"Bar {one['measure']}, {one['part']}: MuseScore 3 called this bar "
                f"corrupted ({one['message']}), so cleaning reset it to a whole-bar "
                f"rest. Taken out: {' '.join(one['removed']) or 'nothing'}. "
                "Put the notes back from the page."
            ),
        }
        # What a fix for this bar has to carry as its `from` (#357): fixes replay
        # before the check, so they meet the bar as it read before the reset.
        if one.get("scanned_from") is not None:
            entry.update(staff_id=one["staff_id"], scanned_from=one["scanned_from"],
                         scanned_notes=one["scanned_notes"])
        written.append(entry)
    _replace_recorded(song_dir, lambda fix: fix.get("source") == MUSESCORE_CHECK_SOURCE, written)
    return len(written)


#: What marks a free-text entry as one of the red marks cleaning left in the score.
CLEAN_MARK_SOURCE = "clean-marker"


def record_clean_marks(mscx_path: str, song_dir: str, log: Logger = _noop) -> int:
    """Say each red mark left in the score in the clean's log. Returns how many.

    The marks used to be copied into `fixes.json` as one sentence each (#238), so the
    Fix panel could list them. It reads them live off the score now (#290), and the
    copies only buried the real fixes -- 45 of them on one song -- and went on
    listing marks a fix had already answered (#347). So this writes nothing, and
    takes away the copies an older clean left behind.
    """
    root = etree.parse(mscx_path).getroot()
    names, found = staff_names(root), marks(root)
    old_copy = lambda fix: fix.get("source") == CLEAN_MARK_SOURCE  # noqa: E731
    if any(old_copy(fix) for fix in _recorded_fixes(song_dir)):
        _replace_recorded(song_dir, old_copy, [])
    for one in found:
        log(f"  Bar {one['measure']}, {names.get(one['staff'], one['staff'])} "
            f"(red mark in the score): {one['text']}")
    return len(found)


DROPPED_VOICE_SOURCE = "per-system-dropped"


def record_dropped_voices(song_dir: str, dropped: List, log: Logger = _noop) -> int:
    """List each voice the per-system answers left unnamed as an outstanding fix (#330).

    A staff answered with one name keeps its upper voice and loses the rest, and the
    usual way in is an answer typed once in system 1 and carried into a later system
    where the page prints two lines on that staff. Nothing about the result looks
    wrong — each part is well-formed — so a singer's line simply goes missing. This
    puts it in the Fix panel, where it was noticed, and in the clean's log. Every
    clean replaces the previous ones, so naming the voice and cleaning again takes
    the sentence away; a typed sentence is never touched.
    """
    # Chord notes kept in the lowest named part's chord are not lost: one voice may
    # sing a chord, so they are said in the log and not listed as a problem.
    for one in dropped:
        if one.kind == "kept":
            log("  " + one.message())
    written = [{
        "kind": "text",
        "source": DROPPED_VOICE_SOURCE,
        "measure": one.start,
        "what": one.message(),
    } for one in dropped if one.kind != "kept"]
    _replace_recorded(song_dir, lambda fix: fix.get("source") == DROPPED_VOICE_SOURCE, written)
    if written:
        log(f"{len(written)} line(s) the per-system answers leave without a part of "
            "their own (listed in the Fix panel):")
    for one in written:
        log("  " + one["what"])
    return len(written)


def check_opens_in_musescore(mscx_path: str, song_dir: str, log: Logger = _noop) -> Dict:
    """Run MuseScore's own check, reset what it rejects, and check again.

    Returns what a song records: `status` (`passed` / `repaired` / `rejected` /
    `not_checked`), `reset` (the bars reset) and `rejected` (what MuseScore still
    objects to after that, which should be nothing).
    """
    found = musescore_check(mscx_path)
    if found is None:
        log("Not checked against MuseScore 3's own check: no MuseScore 3 answered "
            "(MUSESCORE_CLI_PATH). MuseScore 4 does not run this check.")
        return {"status": "not_checked", "reset": [], "rejected": []}
    if not found:
        record_musescore_resets(song_dir, [])
        log("MuseScore 3 opens the cleaned score without calling it corrupted.")
        return {"status": "passed", "reset": [], "rejected": []}
    log(f"MuseScore 3 would call this score corrupted ({len(found)} problem(s)):")
    for one in found:
        log("  " + one["message"])
    resets = reset_rejected_bars(mscx_path, found)
    record_musescore_resets(song_dir, resets)
    for one in resets:
        log(f"  Reset bar {one['measure']} of {one['part']} to a rest; "
            f"listed in the Fix panel. Taken out: {' '.join(one['removed']) or 'nothing'}")
    again = musescore_check(mscx_path)
    if again is None:
        log("Could not check again after resetting those bars.")
        return {"status": "not_checked", "reset": resets, "rejected": []}
    if again:
        log(f"MuseScore 3 still calls the score corrupted ({len(again)} problem(s)).")
        return {"status": "rejected", "reset": resets, "rejected": again}
    log("MuseScore 3 opens it now.")
    return {"status": "repaired", "reset": resets, "rejected": []}


def musescore_findings(mscx_path: str, rejected: List[Dict]) -> List[Dict]:
    """Health rows for what MuseScore still rejects, so the Fix panel lists them."""
    try:
        names = staff_names(etree.parse(mscx_path).getroot())
    except (OSError, etree.XMLSyntaxError):
        names = {}
    return [{
        "id": f"musescore-corrupt-m{one['measure']}-s{one['staff']}-v{one['voice']}",
        "kind": "musescore-corrupt",
        "measure": one["measure"] or None,
        "staff": names.get(one["staff"], f"staff {one['staff']}") if one["staff"] else "whole score",
        "detail": f"MuseScore 3 calls this corrupted: {one['message']}",
    } for one in rejected]


def _replace_recorded(song_dir: str, owned: Callable[[Dict], bool], written: List[Dict]) -> None:
    """Swap the `fixes.json` entries `owned` picks out for `written`, keeping the rest."""
    entries = [fix for fix in _recorded_fixes(song_dir) if not owned(fix)] + written
    path = os.path.join(song_dir, "fixes.json")
    if not entries and not os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


#: What marks a free-text entry as written by the scan rather than by a person.
SCAN_SOURCE = "scan"


def record_scan_repairs(song_dir: str, system: int, moved: List[Dict]) -> int:
    """Write down the whole-measure rests the scan moved in one printed system.

    One ``text`` entry per rest, so the Fix panel lists each as outstanding and a
    person is told a bar was touched (`free_text_fixes`). This is the half of
    :func:`omr.split_measure_rests` that must not be skipped: cleaning pads the
    hole the repair leaves, health then goes quiet, and a loudly wrong bar becomes
    a quietly wrong one -- this project's named failure mode, occurring inside a
    repair. A log line is not a record; it scrolls past and the next page load has
    never heard of it.

    ``text`` and not one of the applicable kinds, because there is nothing to
    replay: the repair happens at the boundary on every parse, so a re-clean gets
    it again for free. What is left over is the judgement, and that is a person's.

    Re-reading a system **replaces** its entries rather than adding to them, so a
    band read five times is not five copies of the same sentence, and a re-read
    that came back clean takes the old sentence away. Only this system's are
    touched; a sentence somebody typed is never one of them.

    Returns how many entries this system now has.
    """
    written = [
        {
            "kind": "text",
            "source": SCAN_SOURCE,
            "system": int(system),
            "what": (
                f"Scan, printed system {system}, bar {one['measure']}, staff "
                f"{one['staff']}: homr wrote a whole-measure rest into the same voice "
                f"as sung notes, which made the bar a whole note too long. The rest was "
                f"moved to a voice of its own (voice {one['was']} -> {one['now']}). "
                "Nothing was added or taken away, so the voice it was sharing may still "
                "be short of a note that cleaning has since padded with a rest. Read "
                "this bar against the page."
            ),
        }
        for one in moved
    ]
    _replace_recorded(
        song_dir,
        lambda fix: fix.get("source") == SCAN_SOURCE and fix.get("system") == system,
        written)
    return len(written)


def free_text_fixes(song_dir: str) -> List[str]:
    """The song's outstanding free-text fixes, for showing on the Fix stage.

    Read live from the file rather than remembered from the last clean, so writing a
    sentence down shows up at once and applying it stops showing up as soon as the
    entry goes. Unreadable is reported as nothing to do: this runs on every state
    request, and cleaning is where a broken file gets said out loud.
    """
    try:
        return free_text(_recorded_fixes(song_dir))
    except (RuntimeError, FixError, OSError):
        return []


def recorded_slurs(song_dir: str) -> List[Dict]:
    """The slur fixes already recorded, for showing on the Fix stage.

    Read live off the file for the same reason the sentences are: the record is only
    worth keeping if someone can see it without opening the folder.
    """
    try:
        return [dict(fix) for fix in _recorded_fixes(song_dir) if fix.get("kind") == "slur"]
    except (RuntimeError, FixError, OSError):
        return []


def bar_for_fix(cleaned_path: str, staff: int, measure: int) -> Dict:
    """One bar of the cleaned score, as something a person can point at.

    The chords in the numbering a recorded fix uses, each with the token that fix
    would carry, the note as it is spelt on the page, and whether the lyrics land on
    it today (`carries_syllable`). That last flag is why this exists rather than the
    browser reading the XML: a note in the middle of a slur carries no marker of its
    own, so whether it takes a syllable cannot be decided by looking at it alone.
    """
    root = etree.parse(cleaned_path).getroot()
    slots = lyric_txt.syllable_slots(root, staff, measure)
    notes = read_bar(root, staff, measure)
    for note in notes:
        # A bar whose voice element is missing gives no slots at all; say nothing
        # rather than claim every note is sung.
        note["carries_syllable"] = slots[note["index"]] if note["index"] < len(slots) else None
    # `notes` are the chords only, in the numbering `index` uses; `from` is what a
    # fix recorded against this bar has to carry, rests and brackets included (#340).
    out = {"staff": staff, "measure": measure, "notes": notes,
           "from": bar_tokens(root, staff, measure),
           "items": bar_items(root, staff, measure),
           "voice2": voice2_tokens(root, staff, measure),
           "syllables": sum(1 for f in slots if f)}
    reset = _reset_as_scanned(root, os.path.dirname(cleaned_path), staff, measure)
    if reset:
        out["reset"] = reset
    return out


def _reset_as_scanned(root: etree._Element, song_dir: str, staff: int,
                      measure: int) -> Optional[Dict]:
    """The bar as a fix meets it, when MuseScore's check reset it after the fixes ran.

    Fixes replay *before* that check, so a new entry for a reset bar has to carry the
    bar as it read then, not the whole-bar rest on the score now — which before #357
    only showed up in the error of a clean that failed. Cleaning keeps it in the bar's
    `musescore-check` entry. The top-level `from` stays what the score reads now,
    because the Fix panel's slur recorder writes to the score as it is.
    """
    ids = [st.get("id") for st in root.findall(".//Score/Staff")]
    for fix in _recorded_fixes(song_dir):
        if fix.get("source") != MUSESCORE_CHECK_SOURCE or "scanned_from" not in fix:
            continue
        position = int(fix.get("staff", 0))
        staff_id = fix.get("staff_id") or (ids[position - 1] if 0 < position <= len(ids) else None)
        if str(staff_id) == str(staff) and int(fix.get("measure", 0)) == measure:
            return {"from": fix["scanned_from"], "notes": fix.get("scanned_notes", []),
                    "why": ("MuseScore 3 refused this bar and cleaning reset it to a rest "
                            "after the recorded fixes ran. A fix for it replays before "
                            "that, so its `from` must be this one, not the bar above.")}
    return None


def score_parts_and_measures(cleaned_path: str) -> Tuple[List[Dict], int]:
    """The singing parts of the cleaned score and how many bars it has.

    What the Fix stage needs to offer a choice: which part, and which bar.
    """
    root = etree.parse(cleaned_path).getroot()
    parts = [{"staff": p.id, "name": p.name} for p in lyric_txt.lyric_parts(root)]
    measures = 0
    for staff in root.findall(".//Score/Staff"):
        measures = max(measures, len(staff.findall("Measure")))
    return parts, measures


def record_slur_fix(song_dir: str, cleaned_path: str, staff: int, measure: int,
                    index: int, span: int, why: str) -> Dict:
    """Write one `slur` entry into the song's fixes.json and apply it to the score.

    Two things happen because a recorded fix is worth nothing if only one of them
    does. It goes on the cleaned score now, so the person who read the page sees the
    result they asked for; and it goes into fixes.json, so `run_clean` puts it back
    the next time the score is rebuilt from the scan. Recording it and not applying
    it would leave the score looking unrepaired until the next clean; applying it and
    not recording it is the hand edit this file exists to replace.

    Only the new entry is applied, never the whole file: the ones already in it are
    on the score already, and `slur` is not idempotent — applying it twice writes a
    second Spanner over the same notes.

    Refuses rather than half-writes. The entry is applied to a parsed copy first, so
    a span that runs past the end of the bar, a chord that is already slurred, or a
    reason left blank all leave the score and the file exactly as they were. The file
    is written before the score deliberately: if the second write is the one that
    fails, the judgement is still recorded and the next clean carries it out, which is
    the recoverable half of the pair.
    """
    why = (why or "").strip()
    if not why:
        raise FixError(
            "say why. A recorded fix is a judgement about the printed page, and in six "
            "months the entry is all that says what was read there.")
    if span < 1:
        raise FixError("a slur has to reach at least the next note")
    root = etree.parse(cleaned_path).getroot()
    bar = read_bar(root, staff, measure)
    if index < len(bar) and bar[index]["starts_slur"]:
        raise FixError(
            f"staff {staff} m{measure} note {index + 1} already starts a slur — "
            "the score has this one; nothing to record.")
    entry = {"kind": "slur", "staff": int(staff), "measure": int(measure),
             "index": int(index), "span": int(span), "why": why}
    applied = apply_fixes(root, [entry])
    entries = _recorded_fixes(song_dir) + [entry]
    with open(os.path.join(song_dir, "fixes.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    etree.ElementTree(root).write(cleaned_path, encoding="UTF-8", xml_declaration=True)
    return {"entry": entry, "applied": applied[0] if applied else ""}


def strip_lyrics_copy(mscx_path: str) -> str:
    """Write a copy of the score with all lyrics removed (cached by mtime).

    Lets us show the cleaned structure without lyrics regardless of what's been
    imported, so it never goes stale relative to the live cleaned file.
    """
    out = os.path.splitext(mscx_path)[0] + ".nolyrics.mscx"
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(mscx_path):
        return out
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    for lyr in root.findall(".//Lyrics"):
        parent = lyr.getparent()
        if parent is not None:
            parent.remove(lyr)
    with open(out, "wb") as f:
        f.write(etree.tostring(root, pretty_print=True, encoding="UTF-8"))
    return out


# Shrink the staff for the rendered previews so the score's own system breaks fit on
# the page (otherwise MuseScore adds extra breaks). Tunable via .env.
SPATIUM_SCALE = float(os.getenv("RENDER_SPATIUM_SCALE", "0.65"))
# Staff sizes to try when the printed line breaks are being kept, largest first.
# Larger is more legible; too large and a wide system gets split anyway.
BREAK_SCALES = (0.85, 0.75, 0.65)
# A wedged MuseScore process would otherwise hold a request open forever; one
# system is seconds, so this is generous rather than tight.
MUSESCORE_TIMEOUT = float(os.getenv("MUSESCORE_CLI_TIMEOUT", "120"))


def line_break_measures(mscx_path: str) -> List[int]:
    """0-based measure indices that end a printed system, from the first staff
    that has any. A page break ends one as well as a line break does. Empty when
    the score has none -- not every source does."""
    if not mscx_path or not os.path.exists(mscx_path):
        return []
    try:
        root = etree.parse(mscx_path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return []
    for staff in root.findall(".//Score/Staff"):
        measures = staff.findall("Measure")
        found = [i for i, m in enumerate(measures) if starts_new_system(m)]
        if found:
            return found
    return []


def cleaned_line_breaks(song_dir: str, input_mscx: str) -> List[int]:
    """The printed line breaks, in the cleaned score's bar numbering (#354).

    The breaks are read off the converted input, which still counts a bar a
    ``delbar`` took out and lacks one an ``insbar`` put in; applied as they stand,
    every system after the move starts a bar off. A break on a bar that was taken
    out moves to the bar before it.
    """
    breaks = line_break_measures(input_mscx)
    if not breaks:
        return []
    try:
        moves = bar_moves(_recorded_fixes(song_dir))
    except RuntimeError:
        moves = []
    return moved_breaks(breaks, moves)


def moved_breaks(breaks: List[int], moves) -> List[int]:
    """0-based break indices of the score before ``moves``, as numbered after them."""
    if not moves:
        return list(breaks)
    out: List[int] = []
    for index in breaks:
        bar = index + 1
        moved = after_moves(bar, moves)
        while moved is None and bar > 1:
            bar -= 1
            moved = after_moves(bar, moves)
        if moved is not None and moved - 1 not in out:
            out.append(moved - 1)
    return sorted(out)


def _apply_line_breaks(root: etree._Element, indices: List[int]) -> int:
    """Put line breaks on the top staff at `indices`. Returns how many were added.

    Nothing is added unless the score has the same number of measures as the one
    the indices came from: applied to a score of a different length they would put
    the systems in the wrong places, which is worse than not applying them.
    """
    staves = [s for s in root.findall(".//Score/Staff") if s.find("Measure") is not None]
    if not staves or not indices:
        return 0
    top = staves[0].findall("Measure")
    if max(indices) >= len(top):
        return 0
    added = 0
    for i in indices:
        if starts_new_system(top[i]):     # a page break there already ends the system
            continue
        lb = etree.SubElement(top[i], "LayoutBreak")
        etree.SubElement(lb, "subtype").text = "line"
        added += 1
    return added


def score_staff_count(mscx_path: str) -> int:
    """How many note-bearing staves the score has (a system's height, in staves)."""
    try:
        root = etree.parse(mscx_path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return 0
    return len([st for st in root.findall(".//Score/Staff") if st.find("Measure") is not None])


def _scaled_staff_mscx(mscx_path: str, breaks: Optional[List[int]] = None,
                       scale: Optional[float] = None, tidy: bool = False) -> Optional[str]:
    """Write a temp copy of the score for rendering: the printed line breaks put
    back if `breaks` is given, and otherwise the staff size reduced by
    SPATIUM_SCALE.

Breaks alone are not enough to keep the printed layout: at full size a system
    that does not fit the page width gets split anyway, and MuseScore quietly adds
    its own break. The caller therefore renders at a scale and checks the result;
    `scale` is which one to use, defaulting to SPATIUM_SCALE.

    `tidy` is for a cleaned score: its rests and barlines are drawn the way a staff
    of its own needs (`fix_staff_display`, #354), so a score cleaned before that
    pass existed is drawn right without being cleaned again.

    Returns the temp path, or None if there is nothing to change (caller then
    renders the original). Caller must delete the temp file.
    """
    if scale is None:
        scale = SPATIUM_SCALE
    if scale >= 1.0 and not breaks and not tidy:
        return None
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    score = root if root.tag == "Score" else root.find(".//Score")
    added = _apply_line_breaks(root, breaks or [])
    if tidy:
        added += sum(fix_staff_display(root).values())
    style = score.find("Style") if score is not None else None
    if style is None or scale >= 1.0:
        if not added:
            return None
        fd, tmp = tempfile.mkstemp(suffix=".mscx")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(etree.tostring(root, encoding="UTF-8"))
        return tmp
    sp = style.find("Spatium")
    if sp is None:
        sp = etree.SubElement(style, "Spatium")
        base = 1.74978  # MuseScore 3 default
    else:
        try:
            base = float(sp.text)
        except (TypeError, ValueError):
            base = 1.74978
    sp.text = f"{base * scale:.5f}"
    fd, tmp = tempfile.mkstemp(suffix=".mscx")
    os.close(fd)
    with open(tmp, "wb") as f:
        f.write(etree.tostring(root, encoding="UTF-8"))
    return tmp


def _page_cache(song_dir: str) -> str:
    return os.path.join(song_dir, ".pages")


def quick_system_bands(song_dir: str, pdf_path: str, log=lambda _m: None) -> List[Dict]:
    """A quick proposal for the printed systems, off the pages the editor shows."""
    return [b.to_dict() for b in
            system_finder.quick_bands(pdf_path, _page_cache(song_dir), log=log)]


def system_bounds(song_dir: str) -> List[Dict]:
    """The stored printed-system boundaries, as plain dicts for the wire."""
    return [b.to_dict() for b in pdf_systems.load_bounds(song_dir)]


def save_system_bounds(song_dir: str, bands: List[Dict], mscx_path: str = "") -> List[Dict]:
    """Store boundaries, re-indexed in page order and labelled where possible.

    `bands` are {page, top, bottom} as fractions of page height -- whatever the
    editor currently shows. Indices and measure ranges are derived here rather
    than trusted from the browser, so a drag can never invent an alignment.
    """
    ordered = sorted(bands, key=lambda b: (int(b["page"]), float(b["top"])))
    bounds = [
        pdf_systems.SystemBounds(
            index=i, page=int(b["page"]),
            top=max(0.0, min(1.0, float(b["top"]))),
            bottom=max(0.0, min(1.0, float(b["bottom"]))),
        )
        for i, b in enumerate(ordered, 1)
    ]
    if mscx_path:
        bounds = pdf_systems.label(bounds, mscx_path)
    pdf_systems.save_bounds(song_dir, bounds)
    # Crop names are system-index + DPI, so edited geometry must clear the old
    # originals. Rendered-score crops live in .pages/cleaned and are unrelated.
    cache = _page_cache(song_dir)
    if os.path.isdir(cache):
        for name in os.listdir(cache):
            if name.startswith("system-") and name.endswith(".png"):
                os.remove(os.path.join(cache, name))
    return [b.to_dict() for b in bounds]


def label_system_bounds(song_dir: str, mscx_path: str) -> bool:
    """Label the stored bands with their bars, from a score that has line breaks.

    A song started from a PDF has its bands drawn before there is any score to
    label them against, so they are saved with no bars; nothing else labels them
    once the scan has assembled one, and the comparison and the by-system lyric
    editor skip a band with no bars (#243). Only the labels change: the band stamp
    is geometry, so no fragment, crop or answer is touched. Returns whether the
    file was written -- a count that disagrees leaves it as it was.
    """
    stored = pdf_systems.load_bounds(song_dir)
    if not stored or not mscx_path:
        return False
    labelled = pdf_systems.label(stored, mscx_path)
    if labelled == stored:
        return False
    pdf_systems.save_bounds(song_dir, labelled)
    return True


def declared_system_count(mscx_path: str) -> int:
    """How many printed systems the score itself declares (0 if unknown)."""
    if not mscx_path or not os.path.exists(mscx_path):
        return 0
    try:
        return len(per_system.system_ranges(etree.parse(mscx_path).getroot()))
    except Exception:
        return 0


def page_image(song_dir: str, pdf_path: str, page: int, dpi: int, grid: bool = False) -> str:
    """One rasterised page, cached under the song folder."""
    out = _page_cache(song_dir)
    raw = pdf_systems.render_page(pdf_path, page, dpi, out)
    return pdf_systems._with_grid(raw) if grid else raw


def page_count(pdf_path: str) -> int:
    return pdf_systems.page_count(pdf_path)


def compare_systems(song_dir: str, mscx_path: str, breaks: List[int]) -> List[Dict]:
    """The printed systems, paired with where they sit in the cleaned render.

    Empty when the two do not correspond — no bounds stored for the scan, or the
    render did not come out with the expected number of systems. Showing a
    mismatched pair side by side would be worse than showing nothing.
    """
    stored = [b for b in pdf_systems.load_bounds(song_dir) if b.measure_start]
    if not stored or not breaks:
        return []
    pdf = render_score_pdf(mscx_path, breaks, tidy=True)
    staves = score_staff_count(mscx_path)
    bands = pdf_systems.rendered_system_bands(pdf, staves, _page_cache(song_dir))
    if len(bands) != len(stored):
        return []
    # The stored labels count the bars of the scan; the render is cut by the breaks
    # moved into the cleaned numbering, so label it by those (#354).
    if len(breaks) + 1 == len(stored):
        last = _measure_count(mscx_path)
        starts = [1] + [i + 2 for i in breaks]
        ends = [i + 1 for i in breaks] + [last]
        return [{"index": b.index, "measure_start": start, "measure_end": end}
                for b, start, end in zip(stored, starts, ends)]
    return [{"index": b.index, "measure_start": b.measure_start,
             "measure_end": b.measure_end} for b in stored]


def _measure_count(mscx_path: str) -> int:
    try:
        root = etree.parse(mscx_path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return 0
    staff = root.find(".//Score/Staff")
    return len(staff.findall("Measure")) if staff is not None else 0


def cleaned_system_crop(song_dir: str, mscx_path: str, breaks: List[int],
                        index: int, dpi: int) -> str:
    """One system of the cleaned render, cropped."""
    pdf = render_score_pdf(mscx_path, breaks, tidy=True)
    staves = score_staff_count(mscx_path)
    cache = _page_cache(song_dir)
    bands = pdf_systems.rendered_system_bands(pdf, staves, cache)
    match = [b for b in bands if b.index == index]
    if not match:
        raise ValueError(f"the cleaned render has no system {index}")
    # Its own folder: crop names are by index, and the scan's crops live next door.
    out = os.path.join(cache, "cleaned")
    return pdf_systems.crop_systems(pdf, match, out, dpi=dpi)[0].path


def system_crop(song_dir: str, pdf_path: str, index: int, dpi: int) -> str:
    """One printed system, cropped from the stored bounds."""
    bounds = pdf_systems.load_bounds(song_dir)
    match = [b for b in bounds if b.index == index]
    if not match:
        raise ValueError(f"No stored bounds for system {index}")
    images = pdf_systems.crop_systems(pdf_path, match, _page_cache(song_dir), dpi=dpi)
    return images[0].path


def scan_system_render(song_dir: str, musicxml_path: str, dpi: int = 200) -> str:
    """Engrave one scanned system as a PNG, cached under the song folder.

    The parse as a picture, so it can be read against the band it was read from.
    `-T` trims the page down to the music: a fragment is one system on an
    otherwise blank A4, and a page shrunk to fit a phone shows neither.
    """
    cache = os.path.join(_page_cache(song_dir), "scan")
    os.makedirs(cache, exist_ok=True)
    stem = os.path.splitext(os.path.basename(musicxml_path))[0]
    out = os.path.join(cache, f"{stem}@{dpi}.png")
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(musicxml_path):
        return out
    cli = (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")
    result = musescore_cli.run(
        [cli, "-T", "10", "-r", str(dpi), musicxml_path, "-o", out], out,
        timeout=MUSESCORE_TIMEOUT)
    # MuseScore numbers the pages it writes, so a one-page export lands as
    # <name>-1.png rather than under the name it was asked for. It is moved into
    # place *whenever it exists*, stale target or not: guarding on the target
    # being absent meant a system read a second time re-engraved correctly and
    # then kept serving the old picture, since the old file was still sitting
    # there. Nothing about that is visible — the picture is plausible, it is
    # just the previous parse — so it read as the new engine having changed
    # nothing.
    numbered = f"{os.path.splitext(out)[0]}-1.png"
    if os.path.exists(numbered):
        os.replace(numbered, out)
    # The verdict comes after the move on purpose: `musescore_cli.ok` asks whether
    # the file at `out` is one this run wrote, and until the numbered page has been
    # moved into place the only thing there is the previous picture.
    if not musescore_cli.ok(result):
        raise RuntimeError(
            "MuseScore CLI could not engrave the scanned system. Check "
            "MUSESCORE_CLI_PATH.\n" + result.said)
    return out


#: Bump when what a render draws changes, so renders cached before it are redone.
RENDER_VERSION = "2"


def render_score_pdf(mscx_path: str, breaks: Optional[List[int]] = None,
                     tidy: bool = False) -> str:
    """Render a .mscx to a PDF via the MuseScore CLI (cached; re-renders if stale).

    Returns the rendered PDF path. The render lives next to the score as
    <base>.render.pdf and is regenerated whenever the source score is newer. The
    staff is shrunk (SPATIUM_SCALE) so the score's own system breaks fit the page.

    `breaks` puts the printed line breaks back for the render. Normal-mode cleaning
    strips them, so without this the preview reflows into MuseScore's own systems
    and cannot be read against the page it came from. Rendered to its own cache
    file, so the two versions do not overwrite each other.

    `tidy` draws a cleaned score's rests and barlines as its own staves need
    (`_scaled_staff_mscx`). A sidecar ``.key`` holds the breaks, `tidy` and
    `RENDER_VERSION` the render was made with: the score's mtime alone does not
    move when a recorded ``delbar`` moves the breaks (#354).
    """
    stem = os.path.splitext(mscx_path)[0]
    out = f"{stem}.breaks.render.pdf" if breaks else f"{stem}.render.pdf"
    key = json.dumps([RENDER_VERSION, list(breaks or []), bool(tidy)])
    key_path = out + ".key"
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(mscx_path):
        try:
            with open(key_path, encoding="utf-8") as f:
                if f.read() == key:
                    return out
        except OSError:
            pass
    cli = (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")

    # Without breaks there is one render at the configured scale. With them, the
    # scale has to be one the score actually fits at: at full size a wide system
    # is split anyway and MuseScore adds a break the page never had, so the
    # result is checked against the number of systems expected and the largest
    # staff that keeps them is used.
    want = (len(breaks) + 1) if breaks else 0
    staves = score_staff_count(mscx_path) if breaks else 0
    scales = BREAK_SCALES if breaks else (SPATIUM_SCALE,)
    cache = os.path.join(os.path.dirname(mscx_path) or ".", ".pages")

    for i, scale in enumerate(scales):
        src = _scaled_staff_mscx(mscx_path, breaks, scale, tidy) or mscx_path
        try:
            result = musescore_cli.run([cli, src, "-o", out], out)
        finally:
            if src != mscx_path and os.path.exists(src):
                os.remove(src)
        if not musescore_cli.ok(result):
            raise RuntimeError(
                "MuseScore CLI render failed. Check MUSESCORE_CLI_PATH.\n"
                + result.said
            )
        if not want or not staves or i == len(scales) - 1:
            break
        got = len(pdf_systems.rendered_system_bands(out, staves, cache))
        if got == want:
            break
    with open(key_path, "w", encoding="utf-8") as f:
        f.write(key)
    return out
    cli = (os.getenv("MUSESCORE_CLI_PATH") or "musescore3")
    src = _scaled_staff_mscx(mscx_path, breaks) or mscx_path
    try:
        result = subprocess.run([cli, src, "-o", out], capture_output=True, text=True)
    finally:
        if src != mscx_path and os.path.exists(src):
            os.remove(src)
    if result.returncode != 0 or not os.path.exists(out):
        raise RuntimeError(
            "MuseScore CLI render failed. Check MUSESCORE_CLI_PATH.\n"
            + (result.stderr or result.stdout or "")
        )
    return out


def _printed_systems(song_dir: str) -> Optional[List[Tuple[int, int]]]:
    """Measure ranges of the printed systems, from the bounds read off the scan.

    Normal-mode cleaning strips layout breaks, so the cleaned score has no systems
    left to find and the lyric editor would offer one cell per part for the whole
    piece. The printed systems still exist on the page; these are them, in the
    cleaned score's bar numbering (#446).
    """
    systems = [r for _, r in _printed_system_bounds(song_dir) if r]
    return systems or None


def _printed_system_bounds(song_dir: str) -> List[Tuple[object, Optional[Tuple[int, int]]]]:
    """Each labelled printed system's bounds and its bars in the cleaned numbering.

    The stored labels count the bars of the scan, so a bar a ``delbar`` took out or
    an ``insbar`` put in would cut every later system at the wrong bars (#446). The
    range is ``None`` for a system every bar of which was taken out.
    """
    if not song_dir:
        return []
    bounds = [b for b in pdf_systems.load_bounds(song_dir) if b.measure_start]
    if not bounds:
        return []
    try:
        moves = bar_moves(_recorded_fixes(song_dir))
    except RuntimeError:
        moves = []
    return [(b, moved_system(b.measure_start, b.measure_end or b.measure_start, moves))
            for b in bounds]


def moved_system(start: int, end: int, moves) -> Optional[Tuple[int, int]]:
    """Bars ``start``-``end`` of the score before ``moves``, as numbered after them.

    A deleted bar at either edge is skipped, and a bar inserted after the system's
    last bar is counted in it (the next system starts after it). ``None`` when every
    bar of the system was taken out.
    """
    if not moves:
        return start, end
    first = None
    for bar in range(start, end + 1):
        first = after_moves(bar, moves)
        if first is not None:
            break
    if first is None:
        return None
    # The bar after the system, wherever it lands, less one. Bars past the score are
    # never deleted, so the search ends within as many steps as there are moves.
    bar, after = end + 1, None
    for _ in range(len(moves) + 1):
        after = after_moves(bar, moves)
        if after is not None:
            break
        bar += 1
    return first, after - 1


def lyric_grid(mscx_path: str, song_dir: str = "") -> Dict:
    """The manual editor's projection of a score: parts x printed systems, prefilled.

    Each system also carries `crop`, the printed system's number in `.systems.json`
    (the picture `/system/{crop}` and `/cleaned-system/{crop}` serve), or None when
    the song has no marked systems and the systems are the score's own line breaks.
    """
    root = etree.parse(mscx_path).getroot()
    grid = lyric_txt.editor_grid(root, systems=_printed_systems(song_dir)).to_dict()
    crops = [b.index for b, r in _printed_system_bounds(song_dir) if r]
    for system in grid["systems"]:
        i = system["index"]
        system["crop"] = crops[i] if len(crops) == len(grid["systems"]) else None
    return grid


def lyric_blocks(mscx_path: str, cells: Dict, song_dir: str = "") -> List[Dict]:
    """The editor's typed cells as lyric JSON blocks, addressed by part name."""
    root = etree.parse(mscx_path).getroot()
    grid = lyric_txt.editor_grid(root, systems=_printed_systems(song_dir))
    return lyric_txt.blocks_from_cells(grid, cells)


def lyric_box_blocks(mscx_path: str, verse: int, boxes: Dict, song_dir: str = "") -> List[Dict]:
    """The by-system editor's changed boxes of one verse as lyric JSON blocks (#397)."""
    root = etree.parse(mscx_path).getroot()
    grid = lyric_txt.editor_grid(root, systems=_printed_systems(song_dir))
    return lyric_txt.blocks_from_boxes(grid, verse, boxes)


# The staves of a printed system are found at the Systems editor's dpi: the finder
# is tuned there (`system_finder.QUICK_DPI`), and the positions are fractions anyway.
LYRIC_STAFF_DPI = system_finder.QUICK_DPI
# Found again when this changes, as when the crop does.
LYRIC_STAFF_VERSION = 2


def _cut_above(ink, upper: tuple, lower: tuple) -> float:
    """The pixel row to cut a system's picture at, just above the lower of two staves.

    A staff's own ink reaches above it — high notes, ledger lines, stems — so the cut
    is the first blank row going up from the staff, a little way into the white. A
    row counts as blank by the columns that are not ink from one staff to the other:
    a barline drawn through the gap would otherwise leave no blank row at all. When
    nothing between them is blank, the cut is a fixed distance above the staff.
    """
    space = lower[2]
    fallback = max(upper[1], lower[0] - 1.5 * space)
    a, b = int(upper[1]) + 1, int(lower[0] - 0.4 * space)
    if b - a < 2:
        return fallback
    region = ink[a:b, int(ink.shape[1] * 0.08):]
    through = region.all(axis=0)
    counts = region[:, ~through].sum(axis=1)
    limit = max(1, int(0.003 * region.shape[1]))
    y = len(counts) - 1
    while y >= 0 and counts[y] > limit:
        y -= 1
    if y < 0:
        return fallback
    top = y
    while top > 0 and counts[top - 1] <= limit and y - top < space:
        top -= 1
    return a + (top + y) / 2


def _found_staves(song_dir: str, pdf_path: str, crop: int) -> Tuple[List[Dict], float]:
    """The staves in one printed system's crop, as fractions of its height, and the
    crop's width over its height.

    Kept per system in the page cache and found again only when the crop changes:
    the crop's file name carries its band and its page (`pdf_systems.crop_systems`).
    """
    import numpy as np
    from PIL import Image

    path = system_crop(song_dir, pdf_path, crop, LYRIC_STAFF_DPI)
    stamp = os.path.basename(path)
    store = os.path.join(_page_cache(song_dir), "lyric-staves.json")
    try:
        with open(store, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    hit = saved.get(str(crop))
    if (isinstance(hit, dict) and hit.get("crop") == stamp
            and hit.get("version") == LYRIC_STAFF_VERSION):
        return hit.get("staves") or [], hit["aspect"]
    with Image.open(path) as image:
        ink = np.asarray(image.convert("L")) < system_finder._INK
    height, width = ink.shape
    found = system_finder._staves(ink)
    staves = [{"top": t / height, "bottom": b / height, "space": s / height,
               "cut": 0.0 if i == 0 else _cut_above(ink, found[i - 1], (t, b, s)) / height}
              for i, (t, b, s) in enumerate(found)]
    aspect = width / height
    saved[str(crop)] = {"crop": stamp, "version": LYRIC_STAFF_VERSION,
                        "staves": staves, "aspect": aspect}
    os.makedirs(os.path.dirname(store), exist_ok=True)
    tmp = store + f".{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(saved, f)
    os.replace(tmp, store)
    return staves, aspect


def lyric_staves(song_dir: str, pdf_path: str, mscx_path: str, system_index: int) -> Dict:
    """Where each staff of one printed system is, and which printed staff it is (#397).

    `{"crop", "aspect", "rows": [{"top", "bottom"}], "printed": [n per row] | None}`,
    `aspect` being the crop's width over its height. A row runs
    from just above its staff's own ink to just above the next one's, so the editor can cut
    the picture there and put each staff's boxes under it. The staves found in the
    picture are paired with the printed staves the cleaned score records for the
    system — every one of them, or the ones that sing there, since a page leaves out
    a resting part's staff — and `printed` is None when neither count matches: then
    no box may be placed under a staff, because it could be the wrong one.
    """
    grid = lyric_grid(mscx_path, song_dir)
    systems = grid["systems"]
    if not 0 <= system_index < len(systems):
        raise ValueError(f"there is no system {system_index + 1}")
    system = systems[system_index]
    crop = system.get("crop")
    if crop is None or not pdf_path:
        return {"crop": crop, "rows": [], "printed": None}
    staves, aspect = _found_staves(song_dir, pdf_path, crop)
    rows = [{"top": st["cut"], "staff": st["top"]} for st in staves]
    for i, row in enumerate(rows):
        row["bottom"] = rows[i + 1]["top"] if i + 1 < len(rows) else 1.0
    root = etree.parse(mscx_path).getroot()
    every, singing = lyric_txt.printed_staves(root, system["start"], system["end"])
    printed = (every if every and len(every) == len(rows)
               else singing if singing and len(singing) == len(rows) else None)
    return {"crop": crop, "aspect": aspect, "rows": rows, "printed": printed}


_LANDED_LOCK = threading.Lock()


def landed_system_svg(song_dir: str, mscx_path: str, start: int, end: int) -> str:
    """Bars `start`-`end` of the cleaned score as they are now, words included (#397).

    What the lyric editor's "Show what landed" shows. Drawn from those bars alone —
    MuseScore exports the score, verovio engraves just that range — rather than cut
    out of a render of the whole score, because a render's systems need not be the
    page's: on a hand-rewritten score (Kevät) the render has 18 systems for the
    page's 13, and "the ninth" is another part of the song. Cached by the score's
    content; an older score's files go when a newer one is drawn.
    """
    import verovio

    from src.score_export import engraving, musescore

    with open(mscx_path, "rb") as f:
        stamp = hashlib.sha1(f.read()).hexdigest()[:12]
    cache = os.path.join(_page_cache(song_dir), "landed")
    out = os.path.join(cache, f"{stamp}-{start}-{end}.svg")
    if os.path.exists(out):
        return out
    with _LANDED_LOCK:
        os.makedirs(cache, exist_ok=True)
        xml = os.path.join(cache, f"{stamp}.musicxml")
        if not os.path.exists(xml):
            for old in os.listdir(cache):
                if not old.startswith(stamp):
                    os.remove(os.path.join(cache, old))
            musescore.run_musescore(mscx_path, xml)
        tk = verovio.toolkit(False)
        tk.setResourcePath(engraving.RESOURCE_PATH)
        tk.setOptions({"pageWidth": 2400, "pageHeight": 60000, "adjustPageHeight": True,
                       "header": "none", "footer": "none", "breaks": "auto", "scale": 45,
                       "xmlIdSeed": engraving.XML_ID_SEED})
        if not tk.loadFile(xml):
            raise RuntimeError("verovio could not read the score")
        tk.select({"measureRange": f"{start}-{end}"})
        tk.redoLayout()
        tmp = out + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(tk.renderToSVG(1))
        os.replace(tmp, out)
    return out


def bar_count(mscx_path: str) -> int:
    """How many bars the score has, counted on its first staff."""
    staff = etree.parse(mscx_path).getroot().find("Score/Staff")
    return len(staff.findall("Measure")) if staff is not None else 0


def has_opening_tempo(mscx_path: str) -> bool:
    """Whether the score supplies its own tempo at the opening."""
    from src.score_export.score import has_opening_tempo as score_has_opening_tempo

    return score_has_opening_tempo(etree.parse(mscx_path).getroot())


def run_lyric_import(
    json_path: str, cleaned_path: str, replace: bool = False, song_dir: str = ""
) -> LyricImport:
    """Import lyric JSON in place into the cleaned score; return the placement result.

    Not a full replace any more (#397): only the parts, voices, verses and systems the
    JSON names change. The printed systems decide how far a line may run on.
    """
    return import_file(json_path, cleaned_path, cleaned_path, replace=replace,
                       systems=_printed_systems(song_dir))
