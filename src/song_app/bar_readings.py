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

**A pick is a recorded fix** (`score_fixes`, kind `rhythm`), applied to the cleaned
score where it stands, the way the slur recorder does it: a re-clean rebuilds the
score from the scan and would throw imported lyrics away, and changing lengths
leaves every syllable on its note. `run_clean` replays it like any other entry.
Each entry carries the content stamp of the fragment it was offered from, and **when
that system is read again and comes back different the pick is dropped** before the
next clean, so the bar asks again rather than writing an old answer over a new
reading. "None of these" is kept in the song state against the same stamp: the
reading stays as homr wrote it, the red mark stays, and the bar is fixed in
MuseScore.
"""
import json
import os
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from src.clean_score import lyric_txt
from src.clean_score.utils import score_fixes
from src.clean_score.utils.score_fixes import FixError

from . import pipeline, state

FIELD = "homr-bar-readings"
#: What marks a `fixes.json` entry as a pick made here.
SOURCE = "reading"
LETTERS = "abcdefgh"

Logger = Callable[[str], None]


def _noop(_msg: str) -> None:
    pass


# ---------------------------------------------------------------- reading the field


def fragment_readings(path: str) -> List[Dict]:
    """The readings a fragment carries, or none (an older homr, or nothing doubted)."""
    try:
        root = etree.parse(path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return []
    for field in root.iterfind("identification/miscellaneous/miscellaneous-field"):
        if field.get("name") == FIELD and field.text:
            try:
                return list(json.loads(field.text).get("bars", []))
            except ValueError:
                return []
    return []


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
    return (f"s{system['index']}-{system.get('content', '')}-p{entry['part']}"
            f"-st{entry['staff']}-b{entry['bar']}-v{entry['voice']}")


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


def _matches(found, entry: Dict) -> bool:
    """Whether a cleaned bar is the voice homr offered readings of."""
    moments = entry.get("moments", [])
    if found is None or len(found) != len(moments):
        return False
    shift = None
    for (value, pitches), moment in zip(found, moments):
        if value != moment.get("value"):
            return False
        wanted = tuple(sorted(_midi(p) for p in moment.get("pitches", [])))
        if len(wanted) != len(pitches):
            return False
        if not wanted:
            continue
        here = pitches[0] - wanted[0]
        if here % 12 or any(a - b != here for a, b in zip(pitches, wanted)):
            return False
        if shift is None:
            shift = here
        elif here != shift:
            return False
    return True


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


def offers(song: state.Song, cleaned: Optional[str] = None) -> List[Dict]:
    """Every bar homr offered readings of that a person can still pick for, or did.

    Each offer: `id`, `system`, `measure`, `staff` (the cleaned staff id), `part`,
    `from` (the bar as tokens, what a recorded pick is checked against), `options`
    (each `{letter, values, current}`) and `decision` (`None`, `{"picked": letter}`
    or `{"none": True}`).
    """
    cleaned = cleaned or song.cleaned_path()
    if not cleaned or not os.path.exists(cleaned):
        return []
    systems = _systems(song)
    if not systems:
        return []
    root = etree.parse(cleaned).getroot()
    staves = _staves(root)
    picks, declined = _picks(song.dir), _declined(song)
    out: List[Dict] = []
    for system, start in systems:
        path = os.path.join(song.dir, system["musicxml"])
        claimed: Dict[int, set] = {}
        for entry in fragment_readings(path):
            readings = entry.get("readings") or []
            if len(readings) < 2:
                continue
            measure = start + int(entry["bar"])
            oid = offer_id(system, entry)
            now = [m.get("value") for m in entry.get("moments", [])]
            options = [{"letter": LETTERS[i], "values": r["values"],
                        "current": r["values"] == now}
                       for i, r in enumerate(readings[:len(LETTERS)])]
            base = {"id": oid, "system": int(system["index"]), "measure": measure,
                    "bar_in_system": int(entry["bar"]), "options": options}
            if oid in picks:
                fix = picks[oid]
                letter = next((o["letter"] for o in options if o["values"] == fix["to"]), "?")
                out.append({**base, "staff": fix["staff"], "part": fix.get("part", ""),
                            "from": fix["from"], "decision": {"picked": letter}})
                continue
            if oid in declined:
                d = declined[oid]
                out.append({**base, "staff": d.get("staff"), "part": d.get("part", ""),
                            "from": [], "decision": {"none": True}})
                continue
            # Doubled voices (one notehead, two stems) read alike, so the first
            # staff that matches and has not been offered this bar yet takes it.
            taken = claimed.setdefault(measure, set())
            for sid, name, staff in staves:
                if sid in taken:
                    continue
                bar = _bar(staff, measure)
                if bar is None or not _matches(_bar_reading(bar), entry):
                    continue
                taken.add(sid)
                out.append({**base, "staff": sid, "part": name,
                            "from": score_fixes._bar_tokens(bar), "decision": None})
                break
    return out


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
    if option is None:
        raise FixError(f"there is no option {choice!r}")
    system = song.data["scan"]["systems"][str(offer["system"])]
    entry = {"kind": "rhythm", "source": SOURCE, "offer": oid,
             "system": offer["system"], "content": system.get("content"),
             "staff": offer["staff"], "part": offer["part"], "measure": offer["measure"],
             "from": offer["from"], "to": option["values"],
             "why": (f"picked homr's reading {choice} of bar {offer['measure']} "
                     "against the page")}
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


def option_musicxml(fragment: str, entry: Dict, values: List[str]) -> str:
    """One bar of one voice, with `values` as its lengths, as MusicXML."""
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
    for index, (moment, value) in enumerate(zip(entry["moments"], values)):
        _, number, dots = score_fixes._parse_value(value)
        length = score_fixes.value_length(value)
        triplet = number % 3 == 0
        written = number * 2 // 3 if triplet else number
        for n, pitch in enumerate(moment.get("pitches") or [None]):
            note = etree.SubElement(m, "note")
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
    entry = next(e for e in fragment_readings(fragment) if offer_id(system, e) == oid)
    tk = verovio.toolkit(False)
    tk.setResourcePath(RESOURCE_PATH)
    tk.setOptions({"adjustPageWidth": True, "adjustPageHeight": True, "header": "none",
                   "footer": "none", "scale": 45, "pageMarginLeft": 20,
                   "pageMarginRight": 20, "pageMarginTop": 20, "pageMarginBottom": 20})
    if not tk.loadData(option_musicxml(fragment, entry, option["values"])):
        raise RuntimeError("verovio could not engrave that option")
    return tk.renderToSVG(1)
