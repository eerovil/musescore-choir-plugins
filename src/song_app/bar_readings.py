"""Let a person pick between homr's readings of a bar it was unsure of (#269).

homr marks a bar it is probably wrong about (`--mark-doubt`), and for each voice of
that bar with more than one reading that fills it, writes its three likeliest
readings into the MusicXML it hands back (`identification/miscellaneous`, field
`homr-bar-readings`; homr's `homr/bar_readings.py`). Only note lengths differ
between them. On Legenda system 11 bar 25 the reading homr chose was the wrong one
of two that both add up, and no rule can tell which: a person with the page crop
beside the options can, in seconds. So the Fix panel offers them.

**Where an option lands is found by what the bar says, not by numbering.** homr's
part, staff and voice are the crop's, and cleaning splits voices into staves, drops
and renames parts, and in per-system mode re-routes every one of them. What
survives all of that is the bar number (the system's first bar plus the crop's own)
and the music: an option is offered on the cleaned staff whose bar holds the same
notes, at the same pitches, with the lengths homr wrote. A tenor marked an octave
down still matches, at the same shift for every note. A bar cleaning changed — a
rest shared out, a bar cut back to its signature — matches nothing and is not
offered, which is right: the options are about a bar that is no longer there.

**Since #295 the options are whole bars** (`whole_bars`): every reading of the lengths
paired with every pitch homr weighed for an unsure note, ranked together, with homr's
second reading of the bar — the crop read again at 80% — always **b**. Picking lengths
and then a pitch one after the other mixed the two up; a whole bar cannot.

**A pick is a recorded fix** (`score_fixes`, kind `bar`; `rhythm` before #295), applied to the cleaned
score where it stands, the way the slur recorder does it: a re-clean rebuilds the
score from the scan and would throw imported lyrics away, and changing lengths
leaves every syllable on its note. `run_clean` replays it like any other entry.
Each entry carries the content stamp of the fragment it was offered from, and **when
that system is read again and comes back different the pick is dropped** before the
next clean, so the bar asks again rather than writing an old answer over a new
reading. **A pick follows its notes, too** (#291): re-answering the per-system grid
moves a part to another staff, so on a rebuild a pick whose staff no longer holds
the bar it was made on goes to the staff that does, and one whose notes are nowhere
in the bar any more is dropped, with a line in the clean's log either way. "None of these" is kept in the song state against the same stamp: the
reading stays as homr wrote it, the red mark stays, and the bar is fixed in
MuseScore.
"""
import json
import math
import os
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils import score_fixes
from src.clean_score.utils.score_fixes import FixError

from . import omr_systems, pipeline, state

FIELD = "homr-bar-readings"
#: What marks a `fixes.json` entry as a pick made here.
SOURCE = "reading"
LETTERS = "abcdefghijklmnopqrstuvwxyz"
#: How many whole bars a card shows before "More", and at most (#295).
SHOWN = 6
MOST = 18

Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


# ---------------------------------------------------------------- reading the field


def _field(path: str, key: str) -> List[Dict]:
    try:
        root = etree.parse(path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return []
    for field in root.iterfind("identification/miscellaneous/miscellaneous-field"):
        if field.get("name") == FIELD and field.text:
            try:
                return list(json.loads(field.text).get(key, []))
            except (ValueError, AttributeError):
                return []
    return []


def fragment_readings(path: str) -> List[Dict]:
    """The readings a fragment carries, or none (an older homr, or nothing doubted)."""
    return _field(path, "bars")


def fragment_second_readings(path: str) -> List[Dict]:
    """The bars a fragment carries as homr's second reading read them (#295).

    homr's field version 3; a fragment read by an older homr has none.
    """
    return _field(path, "second")


def fragment_note_readings(path: str) -> List[Dict]:
    """The other pitches a fragment carries for its doubted notes (#290).

    homr's field version 2; a fragment read by an older homr has none.
    """
    return _field(path, "notes")


_STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _midi(pitch: Dict) -> int:
    return (_STEPS.get(pitch.get("step", "C"), 0) + int(pitch.get("alter", 0))
            + 12 * (int(pitch.get("octave", 4)) + 1))


def _systems(song: state.Song) -> List[Tuple[Dict, int]]:
    """Each read system with the assembled bar its first bar became, in order."""
    systems = song.data.get("scan", {}).get("systems", {})
    out = []
    start = 0
    for key in sorted(systems, key=int):
        entry = systems[key]
        if entry.get("error") or not entry.get("musicxml"):
            return []  # a hole: the bar numbering after it cannot be trusted
        out.append((entry, start))
        start += int(entry.get("bars") or 0)
    return out


def offer_id(system: Dict, entry: Dict) -> str:
    oid = (f"s{system['index']}-{system.get('content', '')}-p{entry['part']}"
           f"-st{entry['staff']}-b{entry['bar']}-v{entry['voice']}")
    if "moment" in entry:  # one note's pitch rather than the bar's lengths
        oid += f"-m{entry['moment']}-c{entry['chord']}"
    return oid


def pitch_name(pitch: Dict) -> str:
    """A homr pitch as a person reads it: "F#4", "Bb3"."""
    accidental = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}.get(int(pitch.get("alter", 0)), "")
    return f"{pitch.get('step', '?')}{accidental}{pitch.get('octave', '')}"


# ---------------------------------------------------------------- the cleaned bar


def _bar(staff: etree._Element, measure_no: int):
    measures = staff.findall("Measure")
    if measure_no < 1 or measure_no > len(measures):
        return None
    return measures[measure_no - 1]


def _bar_reading(measure: etree._Element) -> Optional[List[Tuple[str, Tuple[int, ...]]]]:
    """The bar's first voice as (homr length, pitches) per note or rest, or None."""
    body = measure.find("voice") if measure.find("voice") is not None else measure
    out = []
    in_tuplet = False
    for el in body:
        if el.tag == "Tuplet":
            if (el.findtext("normalNotes"), el.findtext("actualNotes")) != ("2", "3"):
                return None
            in_tuplet = True
        elif el.tag == "endTuplet":
            in_tuplet = False
        elif el.tag == "location":
            return None
        elif el.tag in ("Chord", "Rest"):
            value = score_fixes.element_value(el, in_tuplet)
            if value is None:
                return None
            pitches = tuple(sorted(int(n.findtext("pitch") or 0) for n in el.findall("Note")))
            out.append((value, pitches))
    return out


def _shift(found, entry: Dict) -> Optional[int]:
    """How far a cleaned bar sounds from the voice homr wrote, or None if it is not it.

    Whole octaves only: a tenor cleaned onto a G8vb staff sounds an octave below
    what homr read off the page, at the same shift for every note.
    """
    moments = entry.get("moments", [])
    if found is None or len(found) != len(moments):
        return None
    shift = None
    for (value, pitches), moment in zip(found, moments):
        if value != moment.get("value"):
            return None
        wanted = tuple(sorted(_midi(p) for p in moment.get("pitches", [])))
        if len(wanted) != len(pitches):
            return None
        if not wanted:
            continue
        here = pitches[0] - wanted[0]
        if here % 12 or any(a - b != here for a, b in zip(pitches, wanted)):
            return None
        if shift is None:
            shift = here
        elif here != shift:
            return None
    return shift or 0


_NOTE_SUFFIX = re.compile(r"-m(\d+)-c(\d+)$")


def printed_staves(root: etree._Element) -> List[Tuple[int, int, Dict[int, List[int]], int]]:
    """(first bar, last bar, {staff on the page: [output staves, upper voice first]}, staves).

    Where each part was printed, keyed by its staff's position on the page from the
    top — the numbering homr's fragment uses. A per-system clean records it as each
    lyric-map entry's "source" (#310); its "map" will not do, since that ranks the
    staves S<A<T<B for the lyric JSON, and a system printing T3 above B above T1/T2
    would be read as T3 on the third staff. A score cleaned before "source" existed
    says nothing, rather than a guess. An ordinary clean's lyricsStaffMap is keyed by
    the input staff, which is the page position already. Empty with no record.
    """
    score = root.find(".//Score") if root.tag != "Score" else root
    tags = {m.get("name"): m.text for m in (score.findall("metaTag") if score is not None else [])}
    if (tags.get("lyricsSystemMap") or "").strip():
        try:
            out = []
            for entry in json.loads(tags["lyricsSystemMap"]):
                source = {int(k): [int(x) for x in v] for k, v in entry["source"].items()}
                count = int(entry.get("staves") or max(source, default=0))
                out.append((int(entry["start"]), int(entry["end"]), source, count))
            return out
        except (KeyError, TypeError, ValueError, AttributeError):
            return []
    staves = lyric_txt._read_lyrics_staff_map(root)
    return [(1, 10 ** 9, staves, len(staves))] if staves else []


def printed_place(printed, measure: Optional[int], staff: Optional[int]) -> Tuple:
    """(staff, staves, voice, voices) a part was printed on in a bar, or four Nones."""
    if measure and staff:
        for start, end, staves, count in printed:
            if not start <= measure <= end:
                continue
            for number, outputs in staves.items():
                if staff in outputs:
                    return (number, count, outputs.index(staff) + 1, len(outputs))
            break
    return (None, None, None, None)


def _fragment_staves(path: str) -> List[int]:
    """How many staves each part of a fragment holds, in page order."""
    try:
        root = etree.parse(path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return []
    return [len(omr_systems._staff_numbers(part)) for part in root.findall("part")]


def _printed_on(printed, measure: int, part_staves: List[int], group: Dict) -> Optional[set]:
    """The cleaned staves printed on the staff homr read a voice off, or None.

    homr names the staff by its part and the staff inside it; counted down the
    fragment that is the staff's position on the page, the key `printed_staves`
    uses. A staff no part was assigned to is sung by nobody. None when the score
    keeps no record, or names a staff the fragment does not have, since then the
    two cannot be lined up and any part might be the one.
    """
    part, staff = int(group["part"]), int(group["staff"])
    if not printed or part >= len(part_staves):
        return None
    number = sum(part_staves[:part]) + staff
    for start, end, staves, _ in printed:
        if start <= measure <= end:
            if max(staves, default=0) > sum(part_staves):
                return None
            return set(staves.get(number, []))
    return None


def _staves(root: etree._Element) -> List[Tuple[int, str, etree._Element]]:
    names = {p.id: p.name for p in lyric_txt.lyric_parts(root)}
    out = []
    for staff in root.findall(".//Score/Staff"):
        try:
            sid = int(staff.get("id") or 0)
        except ValueError:
            continue
        if sid in names and staff.find("Measure") is not None:
            out.append((sid, names[sid], staff))
    return out


# ---------------------------------------------------------------- what is on offer


def _picks(song_dir: str) -> Dict[str, Dict]:
    return {fix["offer"]: fix for fix in pipeline._recorded_fixes(song_dir)
            if fix.get("source") == SOURCE and fix.get("offer")}


def _declined(song: state.Song) -> Dict[str, Dict]:
    return {d["offer"]: d for d in song.data.get("readings", {}).get("declined", [])
            if isinstance(d, dict) and d.get("offer")}


def _now(entry: Dict) -> List[Dict]:
    return [{"kind": m.get("kind", "note"), "value": m.get("value"),
             "pitches": list(m.get("pitches") or [])} for m in entry.get("moments", [])]


def _signature(moments: List[Dict]) -> Tuple:
    return tuple((m.get("value"), tuple(sorted(_midi(p) for p in m.get("pitches") or [])))
                 for m in moments)


def _written_probability(pitches: List[Dict]) -> float:
    """The probability of the pitch a note was written with.

    homr's field version 3 carries it; version 2 left it out, and then it is what the
    other pitches leave over — never below the likeliest of them, since homr wrote it.
    """
    given = pitches[0].get("probability")
    if given is not None:
        return float(given)
    others = [float(p.get("probability") or 0) for p in pitches[1:]]
    return max(1 - sum(others), max(others, default=0), 0.01)


def whole_bars(group: Dict) -> List[Dict]:
    """The whole bars to offer for one voice of an unsure bar, in the order shown.

    **a** is the bar as read now. The second reading comes next when homr has one,
    whatever it would rank, because it is what catches a mistake the decoder was sure
    of. Then every pairing of a reading of the lengths with a pitch for each unsure
    note, likeliest first: the lengths' score is homr's log-likelihood and each pitch
    adds its own log-probability. At most `MOST` in all, each one different.
    """
    now = _now(group)
    rhythms = [(list(r["values"]), float(r.get("score") or 0))
               for r in group.get("rhythms", []) if len(r.get("values") or []) == len(now)]
    if not rhythms:
        rhythms = [([m["value"] for m in now], 0.0)]
    beams: List[Tuple[float, List[str], Dict]] = [(score, values, {}) for values, score in rhythms]
    for note in group.get("notes", []):
        pitches = note.get("pitches") or []
        if len(pitches) < 2:
            continue
        where = (int(note["moment"]), int(note["chord"]))
        choices = [(math.log(_written_probability(pitches)), pitches[0])] + [
            (math.log(max(float(p.get("probability") or 0), 1e-6)), p) for p in pitches[1:]]
        beams = sorted(((score + add, values, {**chosen, where: pitch})
                        for score, values, chosen in beams for add, pitch in choices),
                       key=lambda beam: -beam[0])[:MOST * 2]
    ranked = []
    for _, values, chosen in beams:
        moments = json.loads(json.dumps(now))
        for moment, value in zip(moments, values):
            moment["value"] = value
        for (m, c), pitch in chosen.items():
            if m < len(moments) and c < len(moments[m]["pitches"]):
                moments[m]["pitches"][c] = {k: pitch[k] for k in ("step", "alter", "octave")
                                            if k in pitch}
        ranked.append({"moments": moments, "second": False})
    candidates = [{"moments": now, "second": False}]
    if group.get("second"):
        candidates.append({"moments": _now({"moments": group["second"]}), "second": True})
    out: List[Dict] = []
    seen = set()
    for bar in candidates + ranked:
        signature = _signature(bar["moments"])
        if signature in seen or len(out) >= MOST:
            continue
        seen.add(signature)
        out.append(bar)
    return out


def _groups(system: Dict, path: str) -> Dict[str, Dict]:
    """Everything a fragment offers about one voice of one bar, by the voice's id."""
    groups: Dict[str, Dict] = {}

    def group(entry: Dict) -> Dict:
        oid = offer_id(system, {k: entry[k] for k in ("part", "staff", "bar", "voice")})
        if oid not in groups:
            groups[oid] = {k: entry[k] for k in ("part", "staff", "bar", "voice")}
            groups[oid].update(moments=entry.get("moments") or [], rhythms=[], notes=[],
                               second=None)
        return groups[oid]

    for entry in fragment_readings(path):
        group(entry)["rhythms"] = entry.get("readings") or []
    for entry in fragment_note_readings(path):
        group(entry)["notes"].append(entry)
    for entry in fragment_second_readings(path):
        group(entry)["second"] = entry.get("second")
    return groups


def _differs(now: List[Dict], moments: List[Dict]) -> List[Tuple[int, int]]:
    """The notes of a bar that are not as read now, to draw them blue."""
    if len(now) != len(moments):
        return []
    out = []
    for m, (old, new) in enumerate(zip(now, moments)):
        for c, pitch in enumerate(new.get("pitches") or []):
            before = (old.get("pitches") or [])[c:c + 1]
            if old.get("value") != new.get("value") or not before or \
                    _midi(before[0]) != _midi(pitch):
                out.append((m, c))
    return out


def offers(song: state.Song, cleaned: Optional[str] = None) -> List[Dict]:
    """Every unsure bar of one voice homr offered other readings of, as whole bars.

    Each offer: `id`, `kind` ("bar"), `system`, `measure`, `staff` (the cleaned staff
    id), `part`, `from` (the bar as tokens, what a recorded pick is checked against),
    `options` (each `{letter, current, second, moments, to, differs}`; `to` is what a
    pick writes, `None` when the offer is no longer on the score) and `decision`
    (`None`, `{"picked": letter}` or `{"none": True}`).
    """
    cleaned = cleaned or song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return []
    systems = _systems(song)
    if not systems:
        return []
    root = etree.parse(cleaned).getroot()
    staves = _staves(root)
    printed = printed_staves(root)
    picks, declined = _picks(song.dir), _declined(song)
    out: List[Dict] = []
    for system, start in systems:
        path = os.path.join(song.dir, system["musicxml"])
        claimed: Dict[int, set] = {}
        part_staves = _fragment_staves(path)
        for oid, group in _groups(system, path).items():
            bars = whole_bars(group)
            if len(bars) < 2:
                continue
            measure = start + int(group["bar"])
            now = bars[0]["moments"]

            def options(shift: Optional[int]) -> List[Dict]:
                return [{"letter": LETTERS[i], "current": i == 0, "second": bar["second"],
                         "moments": bar["moments"], "differs": _differs(now, bar["moments"]),
                         "to": None if shift is None else _to(bar["moments"], shift)}
                        for i, bar in enumerate(bars)]

            base = {"id": oid, "kind": "bar", "system": int(system["index"]),
                    "measure": measure, "bar_in_system": int(group["bar"]),
                    "part_index": int(group["part"]), "staff_index": int(group["staff"])}
            earlier = [fix for pid, fix in picks.items()
                       if pid == oid or _NOTE_SUFFIX.sub("", pid) == oid]
            if earlier:
                fix = earlier[-1]
                letter = fix.get("letter") if fix.get("kind") == "bar" else None
                out.append({**base, "options": options(None), "staff": fix["staff"],
                            "part": fix.get("part", ""), "from": fix.get("from", []),
                            "decision": {"picked": letter or "earlier"}})
                continue
            if oid in declined:
                d = declined[oid]
                out.append({**base, "options": options(None), "staff": d.get("staff"),
                            "part": d.get("part", ""), "from": [],
                            "decision": {"none": True}})
                continue
            # Doubled voices (one notehead, two stems) read alike, so the first
            # staff that matches and has not been offered this bar yet takes it.
            # Without a record of where the parts were printed every staff is a
            # candidate, as before.
            taken = claimed.setdefault(measure, set())
            # Only the parts printed on the staff homr read: the same notes an
            # octave away on another staff are another singer (#310).
            allowed = _printed_on(printed, measure, part_staves, group)
            for sid, name, staff in staves:
                if sid in taken or (allowed is not None and sid not in allowed):
                    continue
                bar = _bar(staff, measure)
                shift = _shift(_bar_reading(bar), {"moments": now}) if bar is not None else None
                if shift is None:
                    continue
                taken.add(sid)
                out.append({**base, "options": options(shift), "staff": sid, "part": name,
                            "from": score_fixes._bar_tokens(bar), "decision": None})
                break
    return out


def _to(moments: List[Dict], shift: int) -> List[Dict]:
    """A whole bar as a `bar` fix writes it: lengths, MIDI pitches and their spelling."""
    return [{"value": m["value"],
             "pitches": [_midi(p) + shift for p in m.get("pitches") or []],
             "tpcs": [score_fixes.tpc_of(p["step"], int(p.get("alter", 0)))
                      for p in m.get("pitches") or []]}
            for m in moments]


def _find(song: state.Song, oid: str, cleaned: Optional[str] = None) -> Dict:
    for offer in offers(song, cleaned):
        if offer["id"] == oid:
            return offer
    raise FixError("that bar has no readings on offer any more — the score or the scan "
                   "has changed since the page was loaded")


# ---------------------------------------------------------------- picking


def record_pick(song: state.Song, oid: str, choice: str) -> Dict:
    """Apply a person's pick to the cleaned score and record it in fixes.json.

    `choice` is an option's letter, or "none". As `pipeline.record_slur_fix`: the
    entry is applied to a parsed copy first, so a refusal changes nothing, and the
    file is written before the score, so a failed second write still leaves the
    judgement recorded for the next clean.
    """
    cleaned = song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        raise FixError("clean the score first")
    offer = _find(song, oid, cleaned)
    if offer["decision"] is not None:
        raise FixError("that bar has already been decided")
    if choice == "none":
        with state.song_lock(song.slug):
            fresh = state.load(song.slug) or song
            readings = fresh.data.setdefault("readings", {})
            readings.setdefault("declined", []).append(
                {"offer": oid, "staff": offer["staff"], "part": offer["part"],
                 "measure": offer["measure"]})
            fresh.save()
        song.data = fresh.data
        return {"offer": oid, "applied": "kept homr's reading; fix the bar in MuseScore"}
    option = next((o for o in offer["options"] if o["letter"] == choice), None)
    if option is None or option["to"] is None:
        raise FixError(f"there is no option {choice!r}")
    system = song.data["scan"]["systems"][str(offer["system"])]
    said = "homr's second reading" if option["second"] else f"homr's reading {choice}"
    entry = {"kind": "bar", "source": SOURCE, "offer": oid, "letter": choice,
             "system": offer["system"], "content": system.get("content"),
             "staff": offer["staff"], "part": offer["part"], "measure": offer["measure"],
             "from": offer["from"], "to": option["to"],
             "why": f"picked {said} of bar {offer['measure']} against the page"}
    root = etree.parse(cleaned).getroot()
    applied = score_fixes.apply_fixes(root, [entry])
    entries = pipeline._recorded_fixes(song.dir) + [entry]
    with open(os.path.join(song.dir, "fixes.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    etree.ElementTree(root).write(cleaned, encoding="UTF-8", xml_declaration=True)
    return {"offer": oid, "applied": applied[0] if applied else ""}


def drop_stale_picks(song: state.Song, log: Logger = _noop) -> int:
    """Take out the picks made on a reading the scan has since replaced. Returns how many.

    A pick names the fragment content it was offered from. Once that system has been
    read again and come back different, the bar the pick was about is not the bar the
    score will be rebuilt from, so replaying it would fail the clean — or worse, fit.
    """
    systems = song.data.get("scan", {}).get("systems", {})

    def stale(fix: Dict) -> bool:
        if fix.get("source") != SOURCE:
            return False
        system = systems.get(str(fix.get("system")))
        return not system or system.get("content") != fix.get("content")

    gone = [fix for fix in pipeline._recorded_fixes(song.dir) if stale(fix)]
    if gone:
        pipeline._replace_recorded(song.dir, stale, [])
        for fix in gone:
            log(f"Dropped the reading picked for bar {fix.get('measure')}: system "
                f"{fix.get('system')} has been read again since.")
    return len(gone)


def relocate_picks(root: etree._Element, entries: List[Dict],
                   log: Logger = _noop) -> Tuple[List[Dict], bool]:
    """Point each pick at the staff of `root` that holds the bar it was made on.

    Returns the entries to replay and whether any pick moved or was dropped. A pick
    names a cleaned staff, and cleaning numbers staves by the parts the per-system
    grid names: answer the grid differently and the same notes land on another staff,
    so a pick replayed by number checks the wrong bar and fails the clean (#291).
    The notes are what the pick was checked against when it was made, so they are
    what finds it again. A staff a pick already stands on is kept first; among the
    rest the first match in staff order that no other pick in that bar has taken
    wins, the rule `offers` uses for voices that read alike. A pick whose notes are
    on no staff is dropped: its voice is gone from the score, and if the bar is
    still on offer somewhere the Fix panel asks again. Every other kind of entry is
    passed through untouched and stays strict.
    """
    staves = _staves(root)
    # Every kind a pick can be: `bar` since #295, `rhythm` and `pitch` before it.
    picks = [fix for fix in entries
             if fix.get("source") == SOURCE and fix.get("kind") in ("bar", "rhythm", "pitch")]
    pick_ids = {id(fix) for fix in picks}
    taken: Dict[int, set] = {}
    # Fixes apply in file order, so a pick after a `delbar` counts bars without the
    # deleted one (#346); `root` still has it, so look the pick up in that numbering.
    deleted: List[int] = []
    at_bar: Dict[int, int] = {}
    for fix in entries:
        if fix.get("kind") == "delbar":
            deleted.append(int(fix.get("measure", 0)))
        elif id(fix) in pick_ids:
            measure = int(fix.get("measure", 0))
            for gone in reversed(deleted):
                if measure >= gone:
                    measure += 1
            at_bar[id(fix)] = measure

    def reads(sid, measure, tokens) -> bool:
        staff = next((st for s, _, st in staves if s == sid), None)
        bar = _bar(staff, measure) if staff is not None else None
        return bar is not None and score_fixes._bar_tokens(bar) == list(tokens)

    # Picks still standing where they were claim their staves before any moves.
    staying = set()
    for fix in picks:
        measure, sid = at_bar[id(fix)], int(fix.get("staff", 0))
        if sid not in taken.setdefault(measure, set()) and reads(sid, measure, fix.get("from", [])):
            taken[measure].add(sid)
            staying.add(id(fix))
    out: List[Dict] = []
    changed = False
    for fix in entries:
        if id(fix) not in pick_ids or id(fix) in staying:
            out.append(fix)
            continue
        measure = at_bar[id(fix)]
        claimed = taken.setdefault(measure, set())
        match = next(((sid, name) for sid, name, _ in staves
                      if sid not in claimed and reads(sid, measure, fix.get("from", []))), None)
        changed = True
        was = f"{fix.get('part') or 'staff'} (staff {fix.get('staff')})"
        if match is None:
            log(f"Dropped the reading picked for bar {fix.get('measure')} of {was}: no part sings "
                "those notes in that bar any more.")
            continue
        sid, name = match
        claimed.add(sid)
        out.append({**fix, "staff": sid, "part": name})
        log(f"Moved the reading picked for bar {fix.get('measure')} from {was} to {name} "
            f"(staff {sid}): the parts were regrouped.")
    return out, changed


# ---------------------------------------------------------------- engraving an option


def _context(part: etree._Element, staff: int, bar: int):
    """The clef, key and time in force on one staff of the crop at a bar."""
    clef, fifths, time = ("G", 2, 0), 0, (4, 4)
    for measure in part.findall("measure")[:bar]:
        for attributes in measure.findall("attributes"):
            for c in attributes.findall("clef"):
                if int(c.get("number") or 1) == staff:
                    clef = (c.findtext("sign") or "G", int(c.findtext("line") or 2),
                            int(c.findtext("clef-octave-change") or 0))
            if attributes.findtext("key/fifths"):
                fifths = int(attributes.findtext("key/fifths"))
            if attributes.findtext("time/beats"):
                time = (int(attributes.findtext("time/beats")),
                        int(attributes.findtext("time/beat-type") or 4))
    return clef, fifths, time


_SHARPS = "FCGDAEB"
_ACCIDENTAL = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
_DIVISIONS = 48  # per quarter: triplets and 32nds both come out whole


def option_musicxml(fragment: str, entry: Dict, values: List[str],
                    moments: Optional[List[Dict]] = None,
                    highlight: Iterable[Tuple[int, int]] = ()) -> str:
    """One bar of one voice, with `values` as its lengths, as MusicXML.

    `moments` stands in for the voice homr wrote (a whole-bar option has its own
    pitches), and each (moment, chord) in `highlight` is drawn blue, so the notes
    that differ from the bar as read are the ones the eye lands on.
    """
    highlight = {tuple(h) for h in highlight}
    moments = moments if moments is not None else entry["moments"]
    root = etree.parse(fragment).getroot()
    parts = root.findall("part")
    part = parts[int(entry["part"])]
    clef, fifths, (beats, beat_type) = _context(part, int(entry["staff"]), int(entry["bar"]))
    key = {s: (1 if fifths > 0 else -1)
           for s in (_SHARPS[:fifths] if fifths > 0 else _SHARPS[::-1][:-fifths])}
    score = etree.Element("score-partwise", version="3.1")
    plist = etree.SubElement(score, "part-list")
    sp = etree.SubElement(plist, "score-part", id="P1")
    etree.SubElement(sp, "part-name").text = ""
    p = etree.SubElement(score, "part", id="P1")
    m = etree.SubElement(p, "measure", number="1")
    attributes = etree.SubElement(m, "attributes")
    etree.SubElement(attributes, "divisions").text = str(_DIVISIONS)
    etree.SubElement(etree.SubElement(attributes, "key"), "fifths").text = str(fifths)
    t = etree.SubElement(attributes, "time")
    etree.SubElement(t, "beats").text = str(beats)
    etree.SubElement(t, "beat-type").text = str(beat_type)
    c = etree.SubElement(attributes, "clef")
    etree.SubElement(c, "sign").text = clef[0]
    etree.SubElement(c, "line").text = str(clef[1])
    if clef[2]:
        etree.SubElement(c, "clef-octave-change").text = str(clef[2])
    seen: Dict[Tuple[str, int], int] = {}
    groups = score_fixes.triplet_groups(values)
    starts = {first for first, _ in groups}
    stops = {last for _, last in groups}
    for index, (moment, value) in enumerate(zip(moments, values)):
        _, number, dots = score_fixes._parse_value(value)
        length = score_fixes.value_length(value)
        triplet = number % 3 == 0
        written = number * 2 // 3 if triplet else number
        for n, pitch in enumerate(moment.get("pitches") or [None]):
            note = etree.SubElement(m, "note")
            if (index, n) in highlight:
                note.set("color", "#1f6fd1")
            if n:
                etree.SubElement(note, "chord")
            if pitch is None:
                etree.SubElement(note, "rest")
            else:
                pe = etree.SubElement(note, "pitch")
                etree.SubElement(pe, "step").text = pitch["step"]
                if pitch.get("alter"):
                    etree.SubElement(pe, "alter").text = str(pitch["alter"])
                etree.SubElement(pe, "octave").text = str(pitch["octave"])
            etree.SubElement(note, "duration").text = str(int(length * 4 * _DIVISIONS))
            etree.SubElement(note, "type").text = score_fixes._TYPES.get(written, "quarter")
            for _ in range(dots):
                etree.SubElement(note, "dot")
            if pitch is not None:
                # Written where the key and the bar so far do not already say it.
                spot = (pitch["step"], int(pitch["octave"]))
                alter = int(pitch.get("alter", 0))
                if alter != seen.get(spot, key.get(pitch["step"], 0)):
                    etree.SubElement(note, "accidental").text = _ACCIDENTAL.get(alter, "natural")
                seen[spot] = alter
            if triplet:
                tm = etree.SubElement(note, "time-modification")
                etree.SubElement(tm, "actual-notes").text = "3"
                etree.SubElement(tm, "normal-notes").text = "2"
                if not n and (index in starts or index in stops):
                    notations = etree.SubElement(note, "notations")
                    if index in starts:
                        etree.SubElement(notations, "tuplet", type="start")
                    if index in stops:
                        etree.SubElement(notations, "tuplet", type="stop")
    return etree.tostring(score, encoding="unicode")


def option_svg(song: state.Song, oid: str, letter: str) -> str:
    """The option engraved, as SVG — verovio, which needs no MuseScore and is quick."""
    import verovio  # noqa: PLC0415 - only the picker needs it

    from src.scrollvideo.engrave import RESOURCE_PATH  # noqa: PLC0415

    offer = _find(song, oid)
    option = next((o for o in offer["options"] if o["letter"] == letter), None)
    if option is None:
        raise FixError(f"there is no option {letter!r}")
    system = song.data["scan"]["systems"][str(offer["system"])]
    fragment = os.path.join(song.dir, system["musicxml"])
    entry = {"part": offer["part_index"], "staff": offer["staff_index"],
             "bar": offer["bar_in_system"]}
    moments = option["moments"]
    xml = option_musicxml(fragment, entry, [m["value"] for m in moments], moments,
                          option["differs"])
    tk = verovio.toolkit(False)
    tk.setResourcePath(RESOURCE_PATH)
    tk.setOptions({"adjustPageWidth": True, "adjustPageHeight": True, "header": "none",
                   "footer": "none", "scale": 45, "pageMarginLeft": 20,
                   "pageMarginRight": 20, "pageMarginTop": 20, "pageMarginBottom": 20})
    if not tk.loadData(xml):
        raise RuntimeError("verovio could not engrave that option")
    return tk.renderToSVG(1)
