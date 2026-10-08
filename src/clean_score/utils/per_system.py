#!/usr/bin/env python3
"""
Per-system score assignment (opt-in) for badly-parsed scores.

Some OCR'd scores assign parts to physical staves inconsistently: the same staff
carries different parts in different systems (e.g. staff 1 is T1+T2 at the start,
T3 at measure 20, T1 at measure 26). The only reliable cut is the printed system,
i.e. each line break.

This module owns the whole assignment-to-score behavior: it discovers the printed
systems, describes each system's staves so a caller can ask a human what they hold,
carries answers forward between systems, rebuilds the score as one clean staff per
named part (pulling notes from whichever (staff, voice) was declared per system,
measure-rests where the part is absent), restores the printed line breaks, and
writes the lyric-routing metadata the lyric importer reads back.

Two adapters sit at the seam and both go through the same interface:

  * the CLI prompt (`per_system_prompt.prompt_for_answers`), used by clean_score;
  * the web assignment grid (`song_app.pipeline.system_grid` /
    `save_system_answers`), used by the song app.

Both produce the same `Answers` mapping ({system_index: {staff_id: "T1,T2"}}),
which is all this module needs to rebuild a score:

    result = clean_per_system(root, input_path="laulun_aika.mscx")   # recorded answers
    result = clean_per_system(root, input_path=p, answers_from=prompt_for_answers)

Answers are remembered per input file, so re-running a score needs no retyping and
the song app can clean headless after the grid is submitted.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple

from lxml import etree

from .missing_ties import add_missing_ties
from .problem_marks import PREFIX as MARK_PREFIX
from .rejected_bars import cut_spanners_between
from .revoice import _voice_summary
from .utils import delete_all_elements_by_selector, starts_new_system

logger = logging.getLogger(__name__)

# {system_index: {staff_id: "T1,T2"}} — one answer string per staff per system.
# An empty answer means "unanswered": the staff keeps whatever it was named in the
# previous system (layouts usually change at only a few systems). To say a staff holds
# nothing from here on, answer CLEARED.
Answers = Dict[int, Dict[int, str]]

CLEARED = "-"

# {system_index: {(staff_id, voice_index): part_name}} — internal, resolved form.
_Decls = Dict[int, Dict[Tuple[int, int], str]]

# Part letter -> sort rank / clef. Unknown letters sort last.
_PART_ORDER = {"S": 0, "A": 1, "T": 2, "B": 3, "M": 4, "W": 5}
_PART_CLEF = {"S": "G", "A": "G", "T": "G8vb", "B": "F", "M": "G8vb", "W": "G"}

# Voice elements provided by the staff skeleton (not copied from the source voice).
# Everything else (Chord, Rest, location, Tuplet, endTuplet, Beam, Spanner, ...) is
# note content and IS copied, so tuplets/beams/ties survive the rebuild.
_SKELETON_KEEP = {"TimeSig", "KeySig", "Clef"}

# Decorations dropped from the rebuilt score (same set the normal split removes).
_STRIP_SELECTORS = (
    ".//Lyrics", ".//offset", ".//Dynamic",
    ".//Spanner[@type='HairPin']", ".//Articulation", ".//Tempo",
    ".//Harmony", ".//bracket", ".//barLineSpan",
)


# --------------------------------------------------------------------------- #
# What a caller sees: the systems, and what each system's staves hold.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SystemRange:
    """A printed system, as 1-based inclusive measure numbers."""

    index: int
    start: int
    end: int


@dataclass(frozen=True)
class StaffRow:
    """One note-bearing staff within one system, as an adapter should show it."""

    staff_id: int
    voices: int
    summary: str
    answer: str = ""  # the saved answer for this cell ("" = never answered)

    def to_dict(self) -> Dict:
        return {
            "staff_id": self.staff_id,
            "voices": self.voices,
            "summary": self.summary,
            "answer": self.answer,
        }


@dataclass(frozen=True)
class SystemLayout:
    """One printed system plus the staves a caller must name for it."""

    index: int
    start: int
    end: int
    staves: List[StaffRow] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "system": self.index,
            "measure_start": self.start,
            "measure_end": self.end,
            "staves": [s.to_dict() for s in self.staves],
        }


@dataclass(frozen=True)
class PerSystemResult:
    """What a rebuild produced: the ordered output parts and the lyric routing map."""

    parts: List[str] = field(default_factory=list)
    lyric_map: List[Dict] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.parts)


@dataclass(frozen=True)
class DroppedVoice:
    """A line with notes in a system that its staff's answer gives no part of its own
    (#330).

    The rebuild takes one name per voice, top first, so a two-voice staff answered
    with one name keeps the upper voice and loses the lower one. The usual way in is
    inheritance: a staff named once in system 1 carries that one name into a later
    system where the page prints two lines on it. Three kinds, because the rebuild
    treats them differently (`_build_parts`):

    - ``voice``: a voice with no name is not copied — dropped.
    - ``beside``: a voice beside a top voice whose stacked chord the names are split
      across is not copied either, however many names there are — dropped.
    - ``chord``: a stacked chord split one notehead per name loses the noteheads past
      the names — dropped.
    - ``kept``: a stacked chord under a single name is copied whole, so its lower
      notes stay in that part's chords with no part of their own — not lost.
    """

    system: int  # 0-based system index
    start: int  # 1-based measure range of the system
    end: int
    staff_id: int
    voice: int  # 0-based voice index (``voice``) or notehead from the top (``chord``/``kept``)
    notes: int  # notes of that line over the system
    answer: str  # the answer in force for the staff in this system
    answered_in: int  # 0-based system the answer was typed in (== system unless inherited)
    kind: str = "voice"  # "voice" | "beside" | "chord" | "kept"

    @property
    def kept_in_chord(self) -> bool:
        return self.kind == "kept"

    def message(self) -> str:
        if self.kind in ("voice", "beside"):
            lost = "the lower voice" if self.voice == 1 else f"voice {self.voice + 1} from the top"
        else:
            lost = ("the lower notes of its chords" if self.voice == 1
                    else f"note {self.voice + 1} from the top of its chords")
        said = (f'"{self.answer}"' if self.answered_in == self.system
                else f'"{self.answer}" (carried over from system {self.answered_in + 1})')
        labels = _labels(self.answer)
        base = next((l for l in labels if l and l != CLEARED), "")
        names = [labels[k] if k < len(labels) and labels[k] else base + chr(ord("a") + k)
                 for k in range(max(len(labels), self.voice + 1))]
        count = f"{self.notes} note" + ("" if self.notes == 1 else "s")
        where = (f"System {self.system + 1} (bars {self.start}–{self.end}), staff "
                 f"{self.staff_id}: {lost} ({count})")
        if self.kind == "kept":
            return (f"{where} stay in {base}'s chords with no part of their own, because "
                    f"the staff is named only {said}. Name them in the Clean grid "
                    f'(e.g. "{", ".join(names)}") to split them, and clean again.')
        verb = "were" if self.kind == "chord" and self.voice == 1 else "was"
        if self.kind == "beside":
            # Named, but in a bar whose top voice stacks a chord: the names are split
            # across those noteheads and this voice is never copied. More names do
            # not help; the bar's voices have to be put right.
            return (f"{where} {verb} dropped: in a bar where the top voice stacks a "
                    f"chord, the names {said} are split across its notes and the other "
                    f"voices are not copied. Put the bar's lines into separate voices "
                    f"(or one chord) in the score, and clean again.")
        return (f"{where} {verb} dropped, because the staff is named only {said}. "
                f'Name every line in the Clean grid (e.g. "{", ".join(names)}", or "-" '
                f"for a line left silent on purpose) and clean again.")

    def to_dict(self) -> Dict:
        return {"system": self.system, "start": self.start, "end": self.end,
                "staff_id": self.staff_id, "voice": self.voice, "notes": self.notes,
                "answer": self.answer, "answered_in": self.answered_in,
                "kind": self.kind, "message": self.message()}


# An adapter that turns the layout into answers (the CLI prompt, or a test double).
AnswerSource = Callable[[List[SystemLayout]], Answers]


# --------------------------------------------------------------------------- #
# Answer persistence (internal; the file is swappable for tests)
# --------------------------------------------------------------------------- #

# Answers for every score live in one JSON file at the repo root, keyed by the input
# score's folder and file name. That is what lets you re-run a score without retyping, and lets
# the song app clean headless after its grid is submitted.
_ANSWER_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".persystem_cache.json")
)


@contextlib.contextmanager
def use_answer_file(path: str) -> Iterator[None]:
    """Record answers in `path` for the duration of the block (tests, alternate hosts)."""
    global _ANSWER_FILE
    previous, _ANSWER_FILE = _ANSWER_FILE, path
    try:
        yield
    finally:
        _ANSWER_FILE = previous


def _read_answer_file() -> Dict[str, Dict[str, Dict[str, str]]]:
    """The whole answer file, or an empty mapping if it is missing or unreadable."""
    if not os.path.exists(_ANSWER_FILE):
        return {}
    try:
        with open(_ANSWER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _key_for(input_path: Optional[str]) -> str:
    """Answers are keyed by the score's folder and file name, without extension.

    The file name alone was the key, and every scanned song's input is called
    `scanned`, so they all shared one answer set: answering one song's grid
    silently replaced another's, and a re-clean then rebuilt it from the wrong
    song's staves.
    """
    if not input_path:
        return ""
    stem = os.path.splitext(os.path.basename(input_path))[0]
    folder = os.path.basename(os.path.dirname(os.path.abspath(input_path)))
    return f"{folder}/{stem}" if folder else stem


def _legacy_key_for(input_path: Optional[str]) -> str:
    """The file-name-only key answers were recorded under before the folder was."""
    if not input_path:
        return ""
    return os.path.splitext(os.path.basename(input_path))[0]


def saved_answers(input_path: Optional[str]) -> Optional[Answers]:
    """Return the answers previously recorded for this input score, or None."""
    key = _key_for(input_path)
    if not key:
        return None
    raw = _read_answer_file()
    entry = raw.get(key) or raw.get(_legacy_key_for(input_path))
    if not entry:
        return None
    return {int(sidx): {int(sid): ans for sid, ans in staves.items()}
            for sidx, staves in entry.items()}


def has_answers(input_path: Optional[str]) -> bool:
    """True if this input score has a recorded answer set (i.e. it is a per-system score)."""
    return bool(saved_answers(input_path))


def save_answers(input_path: Optional[str], answers: Answers) -> None:
    """Record answers for this input score so a later rebuild needs no prompting."""
    key = _key_for(input_path)
    if not key:
        return
    raw = _read_answer_file()
    raw[key] = {str(sidx): {str(sid): ans for sid, ans in staves.items()}
                for sidx, staves in answers.items()}
    try:
        with open(_ANSWER_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Could not write per-system answers: %s", exc)


# --------------------------------------------------------------------------- #
# System discovery + layout
# --------------------------------------------------------------------------- #

def _score_of(root: etree._Element) -> etree._Element:
    return root if root.tag == "Score" else root.find(".//Score")


def _system_bounds(root: etree._Element) -> List[Tuple[int, int]]:
    """(start, end) 0-based inclusive measure ranges, split at system breaks."""
    score = _score_of(root)
    staff = score.find("Staff")
    measures = staff.findall("Measure")
    breaks = {i for i, m in enumerate(measures) if starts_new_system(m)}
    systems: List[Tuple[int, int]] = []
    start = 0
    for i in range(len(measures)):
        if i in breaks:
            systems.append((start, i))
            start = i + 1
    if start < len(measures):
        systems.append((start, len(measures) - 1))
    return systems


def system_ranges(root: etree._Element) -> List[SystemRange]:
    """The score's printed systems as 1-based inclusive measure ranges.

    The printed system is the unit the lyric JSON is written in (one block per
    line), so lyric handling shares this discovery rather than repeating it.
    """
    return [SystemRange(i, a + 1, b + 1) for i, (a, b) in enumerate(_system_bounds(root))]


def _max_voices_in_range(staff: etree._Element, a: int, b: int) -> int:
    """How many parts this staff can carry across the range.

    The voices up to the last one with notes (all-rest voices after it don't count),
    but a chord in a lone top voice counts as one part per notehead: an engraver writes two singers holding a chord together as a single
    voice with the notes stacked, and a staff that is only ever asked for one name
    there hands both notes to the upper part and leaves the lower one silent.
    Voices are counted by their written index, the way the rebuild hands them out:
    a sung voice 2 under an all-rest voice 1 is two lines, or naming the second one
    would be capped away and the lower line could never get a part (#330).
    """
    measures = staff.findall("Measure")
    best = 0
    for m in range(a, b + 1):
        voices = measures[m].findall("voice")
        sung = [i for i, v in enumerate(voices) if v.find("Chord") is not None]
        lines = sung[-1] + 1 if sung else 0
        # The rebuild splits a stack only in the top voice (`_build_parts`).
        stacked = (max((len(ch.findall("Note")) for ch in voices[0].findall("Chord")),
                       default=0) if sung == [0] else 0)
        best = max(best, lines, stacked)
    return best


def _first_nonempty_summary(staff: etree._Element, a: int, b: int) -> str:
    measures = staff.findall("Measure")
    for m in range(a, b + 1):
        voices = measures[m].findall("voice")
        summaries = [_voice_summary(v) for v in voices]
        if any(s != "(rest)" for s in summaries):
            return " || ".join(summaries)
    return "(empty)"


def system_layout(
    root: etree._Element, input_path: Optional[str] = None
) -> List[SystemLayout]:
    """Describe every printed system: its measures and its note-bearing staves.

    This is what both assignment adapters render — the CLI prompt and the web grid.
    Each staff row carries the answer recorded for that exact cell (empty when never
    answered); carrying an answer forward to later systems is the rebuild's job, not
    the adapter's.
    """
    score = _score_of(root)
    staves = score.findall("Staff")
    recorded = saved_answers(input_path) or {}
    layouts: List[SystemLayout] = []
    for sidx, (a, b) in enumerate(_system_bounds(root)):
        rows = []
        for staff in staves:
            sid = int(staff.get("id", "0"))
            nv = _max_voices_in_range(staff, a, b)
            if nv == 0:
                continue
            rows.append(StaffRow(
                staff_id=sid,
                voices=nv,
                summary=_first_nonempty_summary(staff, a, b),
                answer=recorded.get(sidx, {}).get(sid, ""),
            ))
        layouts.append(SystemLayout(index=sidx, start=a + 1, end=b + 1, staves=rows))
    return layouts


def layout_for_file(mscx_path: str) -> List[SystemLayout]:
    """`system_layout` for a score on disk, prefilled with its recorded answers."""
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    return system_layout(root, input_path=mscx_path)


# --------------------------------------------------------------------------- #
# Answers -> declarations
# --------------------------------------------------------------------------- #

def _part_sort_key(name: str) -> Tuple[int, int, str]:
    letter = name[0].upper() if name else "Z"
    rank = _PART_ORDER.get(letter, 99)
    digits = "".join(c for c in name if c.isdigit())
    return (rank, int(digits) if digits else 0, name)


def _fallback_of(parts: List[str]) -> Dict[str, str]:
    """{child: base} for each part named as another part plus one lowercase letter.

    `S1b` sings `S1` wherever it has no notes of its own: the name is how a person
    says the two are one line split in places (a divisi), so where the page prints one
    line — or the system names only `S1` — that line is S1b's too. Only one level:
    `S1bc` does not sing `S1b`, since what `S1b` borrowed is not its own line.
    """
    names = set(parts)
    links = {name: name[:-1] for name in parts
             if len(name) > 1 and name[-1].islower() and name[:-1] in names}
    return {child: base for child, base in links.items() if base not in links}


def _answers_in_force(
    layouts: List[SystemLayout], answers: Answers
) -> Iterator[Tuple[SystemLayout, StaffRow, str, int]]:
    """Each (system, staff) with the answer that applies there and where it was typed.

    A staff left unanswered in a system inherits its answer from the previous system;
    CLEARED is yielded as itself and stops that inheritance.
    """
    last_answer: Dict[int, Tuple[str, int]] = {}
    for layout in layouts:
        sys_ans = answers.get(layout.index, {})
        for row in layout.staves:
            raw = sys_ans.get(row.staff_id, "")
            if raw == "":
                raw, typed_in = last_answer.get(row.staff_id, ("", layout.index))
            else:
                typed_in = layout.index
                last_answer[row.staff_id] = (raw, typed_in)
            yield layout, row, raw, typed_in


def _labels(raw: str) -> List[str]:
    return [n.strip() for n in raw.split(",")] if raw and raw != CLEARED else []


def _decls_from_answers(layouts: List[SystemLayout], answers: Answers) -> _Decls:
    """Resolve answer strings ("T1,T2") into {(staff_id, voice_index): part} per system.

    A staff left unanswered in a system inherits its answer from the previous system;
    CLEARED declares nothing and stops that inheritance (the staff stays unnamed until
    it is answered again). Names beyond the staff's voice count are ignored.
    """
    decls: _Decls = {}
    for layout, row, raw, _ in _answers_in_force(layouts, answers):
        for vidx, name in enumerate(_labels(raw)):
            if vidx < row.voices and name and name != CLEARED:
                decls.setdefault(layout.index, {})[(row.staff_id, vidx)] = name
    return decls


def _unnamed_lines(
    staff: etree._Element, a: int, b: int, declared: Set[int]
) -> Dict[Tuple[str, int], int]:
    """{(kind, line): notes} the rebuild leaves without a part, over measures a..b.

    Mirrors `_build_parts` bar by bar, given the voice indices `declared` as parts on
    the staff (`_decls_from_answers`): a bar with more parts than voices whose top
    voice stacks chords is cut one notehead per part, part N taking notehead N (the
    lowest when the chord is shorter) and the bar's other voices copied nowhere; any
    other bar hands its voices out by their index as written, rest-only voices
    included, so a sung voice under an all-rest one is still the second voice. Kinds
    are those of `DroppedVoice`.
    """
    found: Dict[Tuple[str, int], int] = {}

    def add(kind: str, line: int, n: int) -> None:
        if n:
            found[(kind, line)] = found.get((kind, line), 0) + n

    def notes_of(voice: etree._Element) -> int:
        return sum(len(ch.findall("Note")) for ch in voice.findall("Chord"))

    for measure in staff.findall("Measure")[a:b + 1]:
        voices = measure.findall("voice")
        if not voices:
            continue
        if len(declared) > len(voices) and _has_chord_stack(voices[0]):
            for chord in voices[0].findall("Chord"):
                size = len(chord.findall("Note"))
                taken = {min(d, size - 1) for d in declared}
                for rank in range(size):
                    if rank not in taken:
                        add("chord", rank, 1)
            for vidx in range(1, len(voices)):
                add("beside" if vidx in declared else "voice", vidx, notes_of(voices[vidx]))
            continue
        for vidx in range(len(voices)):
            if vidx not in declared:
                add("voice", vidx, notes_of(voices[vidx]))
        if declared == {0} and len([v for v in voices if v.find("Chord") is not None]) == 1:
            # Copied whole: a single voice's stacked noteheads stay in the one part.
            add("kept", 1, sum(len(ch.findall("Note")) - 1
                               for ch in voices[0].findall("Chord")))
    return found


def dropped_voices(root: etree._Element, answers: Answers) -> List[DroppedVoice]:
    """Every line with notes that the answers leave without a part, on a staff they name.

    A line counts as answered when its slot holds a name or `-`: `-` says the line is
    silent on purpose, so it is dropped and not reported, while an empty slot ("A1,")
    is not an answer and is. A staff named nowhere is not reported here: the grid
    already asks about that before cleaning. This is the quieter case — named, but
    with fewer names than the lines the page prints there — which used to cost the
    lower voice in silence.
    """
    score = _score_of(root)
    staves = {int(s.get("id", "0")): s for s in score.findall("Staff")}
    layouts = system_layout(root)
    out: List[DroppedVoice] = []
    order = {"voice": 0, "beside": 1, "chord": 2, "kept": 3}
    for layout, row, raw, typed_in in _answers_in_force(layouts, answers):
        labels = _labels(raw)
        # The same declarations the rebuild makes (`_decls_from_answers`).
        declared = {i for i, name in enumerate(labels)
                    if i < row.voices and name and name != CLEARED}
        if not declared:
            continue
        silent = {i for i, name in enumerate(labels) if name == CLEARED}
        lines = _unnamed_lines(staves[row.staff_id], layout.start - 1, layout.end - 1, declared)
        for (kind, line), notes in sorted(lines.items(), key=lambda kv: (order[kv[0][0]], kv[0][1])):
            if line in silent and kind != "beside":
                continue
            out.append(DroppedVoice(
                system=layout.index, start=layout.start, end=layout.end,
                staff_id=row.staff_id, voice=line, notes=notes, answer=raw,
                answered_in=typed_in, kind=kind,
            ))
    return out


def dropped_voices_for_file(mscx_path: str) -> List[DroppedVoice]:
    """`dropped_voices` for a score on disk, against its recorded answers."""
    answers = saved_answers(mscx_path) or {}
    with open(mscx_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    return dropped_voices(root, answers)


# --------------------------------------------------------------------------- #
# Score reconstruction
# --------------------------------------------------------------------------- #

def _system_of(measure_index: int, bounds: List[Tuple[int, int]]) -> int:
    for sidx, (a, b) in enumerate(bounds):
        if a <= measure_index <= b:
            return sidx
    return len(bounds) - 1


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


def _clefs_by_measure(staff: etree._Element, default: str = "G") -> List[str]:
    """The clef in force at the start of each measure of a source staff."""
    current = default
    out: List[str] = []
    for measure in staff.findall("Measure"):
        first = measure.find("voice")
        if first is not None:
            for el in first:
                if el.tag == "Clef":
                    current = el.findtext("concertClefType") or current
                elif el.tag in ("Chord", "Rest"):
                    break
        out.append(current)
        for clef in measure.iter("Clef"):
            current = clef.findtext("concertClefType") or current
    return out


def _lower_octave(elements: List[etree._Element]) -> None:
    """Move every notehead in these elements down twelve semitones."""
    for el in elements:
        for pitch in el.iter("pitch"):
            if pitch.text and pitch.text.strip().isdigit():
                pitch.text = str(int(pitch.text.strip()) - 12)


def _has_chord_stack(voice: etree._Element) -> bool:
    """True if any chord here stacks more than one notehead (divisi in one voice)."""
    return any(len(ch.findall("Note")) > 1 for ch in voice.findall("Chord"))


def _voice_at_notehead(voice: etree._Element, rank: int) -> List[etree._Element]:
    """This voice with each chord reduced to one notehead: `rank` 0 = top, 1 = next.

    For the bar where the engraver writes two singers as one stack of notes. A chord
    with fewer noteheads than `rank` asks for is a moment where the two converge, so
    the part takes the lowest note there rather than falling silent — silence would
    leave a hole in that singer's practice track.
    """
    out: List[etree._Element] = []
    for el in voice:
        if el.tag in _SKELETON_KEEP:
            continue
        copy = deepcopy(el)
        if copy.tag == "Chord":
            notes = copy.findall("Note")
            if len(notes) > 1:
                by_pitch = sorted(notes, key=lambda n: int(n.findtext("pitch") or 0),
                                  reverse=True)
                keep = by_pitch[rank] if rank < len(by_pitch) else by_pitch[-1]
                for note in notes:
                    if note is not keep:
                        copy.remove(note)
        out.append(copy)
    return out


def _build_parts(
    root: etree._Element, bounds: List[Tuple[int, int]], decls: _Decls
) -> List[str]:
    """
    Rebuild the score as one staff per declared part. Returns the ordered part names.
    Old Parts/Staves are removed (so empty/undeclared staves are deleted).
    """
    score = _score_of(root)
    source_staves = {int(s.get("id", "0")): s for s in score.findall("Staff")}
    defaults = {int(st.get("id", "0")): st.findtext("defaultClef") or "G"
                for st in score.findall("Part/Staff")}
    source_clefs = {sid: _clefs_by_measure(s, defaults.get(sid, "G"))
                    for sid, s in source_staves.items()}
    template_part = score.find("Part")
    ref_staff = score.find("Staff")

    parts = sorted({name for d in decls.values() for name in d.values()}, key=_part_sort_key)
    if not parts:
        return []

    new_staves: List[etree._Element] = []
    new_parts: List[etree._Element] = []
    # Per part, per bar: the (staff, voice) its notes came from, or None for a filler rest.
    sources: Dict[str, List[Optional[Tuple[int, int]]]] = {}
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
            system = _system_of(mi, bounds)
            src: Optional[Tuple[int, int]] = None
            for (sid, vidx), name in decls.get(system, {}).items():
                if name == part:
                    src = (sid, vidx)
                    break
            placed = False
            filled_from = len(voice)
            if src is not None:
                src_staff = source_staves.get(src[0])
                if src_staff is not None:
                    src_measure = src_staff.findall("Measure")[mi]
                    src_voices = src_measure.findall("voice")
                    declared_here = sum(1 for (sid, _) in decls.get(system, {})
                                        if sid == src[0])
                    # Divisi written as a chord: the staff declares more parts than it
                    # has voices here, and the notes really are stacked in one voice.
                    # Each part takes its own notehead — copying the voice whole would
                    # give the upper part both notes and leave the lower one silent.
                    # The stack itself has to be there: a staff that simply has one
                    # voice in this bar is a bar where the page shows one line, and
                    # whether that means unison or a tacit voice is a reading of the
                    # page, not something to infer here (a wrong guess is silent —
                    # a note and a rest are both well-formed).
                    stacked = (declared_here > len(src_voices) and bool(src_voices)
                               and _has_chord_stack(src_voices[0]))
                    if stacked:
                        for el in _voice_at_notehead(src_voices[0], src[1]):
                            voice.append(el)
                        placed = True
                        logger.debug(
                            "Measure %d staff %d: %s takes notehead %d of a shared chord",
                            mi + 1, src[0], part, src[1],
                        )
                    elif src[1] < len(src_voices):
                        for el in src_voices[src[1]]:
                            if el.tag not in _SKELETON_KEEP:
                                voice.append(deepcopy(el))
                        placed = True
            # A tenor read off a plain treble staff was read an octave high: the
            # notes sit where an 8vb clef puts them but were taken at face value.
            # The staff is about to be marked G8vb, so the pitches have to move
            # too, or the practice track sings the line an octave above the men.
            if (placed and _PART_CLEF.get(part[0].upper()) == "G8vb"
                    and source_clefs.get(src[0], [])[mi:mi + 1] == ["G"]):
                _lower_octave(list(voice)[filled_from:])
            if not placed:
                voice.append(_measure_rest(sig_n, sig_d))
            sources.setdefault(part, []).append(src if placed else None)
        _set_clef(staff, part[0] if part else "")
        new_staves.append(staff)

        new_part = deepcopy(template_part)
        pstaff = new_part.find(".//Staff")
        if pstaff is not None:
            pstaff.set("id", str(out_idx))
        tn = new_part.find("trackName")
        if tn is not None:
            tn.text = part
        for tag, val in (("longName", part), ("shortName", part), ("trackName", part)):
            el = new_part.find(f".//Instrument/{tag}")
            if el is not None:
                el.text = val
        new_parts.append(new_part)

    _fill_from_fallbacks(parts, new_staves, sources)

    # Re-add a line break at the end of each system (except the last) on the top staff,
    # so the rebuilt score keeps the original system layout.
    if new_staves:
        top_measures = new_staves[0].findall("Measure")
        for (a, b) in bounds[:-1]:
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


def _is_problem_mark(el: etree._Element) -> bool:
    return el.tag == "StaffText" and (el.findtext("text") or "").startswith(MARK_PREFIX)


def _fill_from_fallbacks(
    parts: List[str],
    staves: List[etree._Element],
    sources: Dict[str, List[Optional[Tuple[int, int]]]],
) -> None:
    """Give a `S1b` the notes of `S1` in every bar it was handed a filler rest.

    The rebuild leaves a bar with one unstacked line to rest in the lower part, since
    unison and a tacit voice look alike on the page. Naming the part `S1b` is a person
    saying which it is, so here the filler becomes the base part's bar. A rest the
    scan wrote in the part's own voice is not a filler and stays.
    """
    by_name = dict(zip(parts, staves))
    for child, base in _fallback_of(parts).items():
        child_bars = by_name[child].findall("Measure")
        base_bars = by_name[base].findall("Measure")
        # Only the seams this adds are cut: own bar against borrowed bar. Seams between
        # systems are the rebuild's as before, on both staves alike.
        origin: List[object] = ["own"] * len(child_bars)
        borrowed = False
        for mi, src in enumerate(sources.get(child, [])):
            if src is not None or mi >= len(base_bars):
                continue
            voice = child_bars[mi].find("voice")
            for el in list(voice):
                if el.tag not in _SKELETON_KEEP:
                    voice.remove(el)
            base_voice = base_bars[mi].find("voice")
            for el in (base_voice if base_voice is not None else []):
                # The base part's red mark stays with the base part: copied, it lists the
                # same doubt twice and has to be deleted twice (#330).
                if el.tag not in _SKELETON_KEEP and not _is_problem_mark(el):
                    voice.append(deepcopy(el))
            origin[mi] = "borrowed"
            borrowed = True
        if borrowed:
            logger.info("Per-system: %s sings %s where it has no notes of its own", child, base)
            cut_spanners_between(by_name[child], origin)


# --------------------------------------------------------------------------- #
# Lyric routing metadata
# --------------------------------------------------------------------------- #

def _build_lyric_map(
    bounds: List[Tuple[int, int]], decls: _Decls, parts: List[str],
    staves: Optional[List[int]] = None,
) -> List[Dict]:
    """
    Per-system printed-staff -> output-staff(s) map for lyric placement.

    The JSON lyric format numbers printed staves top-to-bottom *within each system*,
    skipping any part that is omitted there. Output staves, by contrast, are a fixed
    T1<T2<...<B set. This bridges them per system:
      - parts that share a source staff (divisi: two voices on one staff) become ONE
        printed staff (voice 0 -> 'above', voice 1 -> 'below');
      - printed staves are ordered by musical rank (S<A<T<B, then number), NOT by the
        OCR's source-staff order, which can be shuffled;
      - omitted/undeclared parts simply don't appear (so they're "missing").

    Returns a list of {"start", "end", "map": {printed_no: [output_ids]}} with 1-based
    inclusive measure ranges. A system where a `S1b` is left out and sings `S1`'s notes
    (`_fallback_of`) also carries "follow": {S1's id: [S1b's id]}, so S1b takes S1's
    words whichever lane of the printed staff S1 is on. It is kept out of "map" because
    "map" is the printed grouping: S1b is not on that staff, and a third id beside a
    divisi pair would break its above/below split.

    Every entry also carries "source": {source staff id: [output ids, upper voice
    first]} — which parts the grid put on which staff of the scanned input, in page
    order rather than rank order — and, given `staves`, "staves": how many staves that
    system prints. "map" ranks the staves, which is what the lyric JSON numbers; the
    Fix panel needs the page position instead, to know which part homr read (#310).
    """
    part_id = {name: i + 1 for i, name in enumerate(parts)}
    fallbacks = _fallback_of(parts)
    out: List[Dict] = []
    for sidx, (a, b) in enumerate(bounds):
        groups: Dict[int, List[Tuple[int, str]]] = {}
        for (sid, vidx), name in decls.get(sidx, {}).items():
            groups.setdefault(sid, []).append((vidx, name))
        ordered = sorted(
            groups.values(),
            key=lambda items: min(_part_sort_key(n) for _, n in items),
        )
        declared = {n for items in groups.values() for _, n in items}
        pmap: Dict[int, List[int]] = {}
        for printed_no, items in enumerate(ordered, start=1):
            ids = [part_id[n] for _, n in sorted(items) if n in part_id]
            if ids:
                pmap[printed_no] = ids
        entry: Dict = {"start": a + 1, "end": b + 1, "map": pmap}
        source = {sid: [part_id[n] for _, n in sorted(items) if n in part_id]
                  for sid, items in sorted(groups.items())}
        entry["source"] = {sid: ids for sid, ids in source.items() if ids}
        if staves is not None and sidx < len(staves):
            entry["staves"] = staves[sidx]
        follow: Dict[int, List[int]] = {}
        for child, base in fallbacks.items():
            if child not in declared and base in declared:
                follow.setdefault(part_id[base], []).append(part_id[child])
        if follow:
            entry["follow"] = follow
        out.append(entry)
    return out


def _write_lyric_metadata(
    root: etree._Element, parts: List[str], lyric_map: List[Dict]
) -> None:
    """Store the lyric routing maps in the score, where lyric import reads them back.

    `lyricsSystemMap` is the real one (the printed numbering shifts per system as
    parts are omitted); `lyricsStaffMap` is the identity fallback for readers that
    only know the single-map form.
    """
    score = root.find(".//Score") if root.tag != "Score" else root
    existing_meta = score.findall("metaTag")
    insert_at = (
        score.index(existing_meta[-1]) + 1 if existing_meta else len(score)
    )
    sys_meta = etree.Element("metaTag", name="lyricsSystemMap")
    sys_meta.text = json.dumps(lyric_map, separators=(",", ":"))
    score.insert(insert_at, sys_meta)
    meta = etree.Element("metaTag", name="lyricsStaffMap")
    meta.text = ";".join(f"{i}:{i}" for i in range(1, len(parts) + 1))
    score.insert(insert_at, meta)


# --------------------------------------------------------------------------- #
# The one entry point
# --------------------------------------------------------------------------- #

def clean_per_system(
    root: etree._Element,
    input_path: Optional[str] = None,
    answers_from: Optional[AnswerSource] = None,
) -> PerSystemResult:
    """Rebuild `root` in place as one staff per part, from per-system assignments.

    The assignments come from `answers_from` — an adapter that is handed the layout
    and returns answers, i.e. the CLI prompt; its answers are recorded for next
    time — or, with no adapter, from the answers already recorded for `input_path`
    by an earlier run or by the web grid.

    Returns the ordered part names and the per-system lyric map (also written into
    the score). An empty result means nothing was rebuilt and the score is untouched:
    no systems, no answers to work from, or no part named in any answer.
    """
    layouts = system_layout(root, input_path=input_path)
    if not layouts:
        return PerSystemResult()

    if answers_from is not None:
        answers = answers_from(layouts)
        save_answers(input_path, answers)
    else:
        answers = saved_answers(input_path)
        if answers:
            logger.info("Per-system: using recorded answers for %s", _key_for(input_path))
        else:
            logger.warning(
                "Per-system needs a terminal to prompt, or a recorded answer set for '%s'.",
                _key_for(input_path),
            )
            return PerSystemResult()

    bounds = [(l.start - 1, l.end - 1) for l in layouts]
    for lost in dropped_voices(root, answers):
        logger.warning("Per-system: %s", lost.message())
    decls = _decls_from_answers(layouts, answers)
    parts = _build_parts(root, bounds, decls)
    if not parts:
        return PerSystemResult()

    # Post-rebuild cleanup: recover ties the OCR dropped, then strip the decorations
    # the normal split also removes. LayoutBreaks are kept — the rebuild re-adds the
    # system breaks and they carry the printed layout.
    add_missing_ties(root)
    for selector in _STRIP_SELECTORS:
        delete_all_elements_by_selector(root, selector)

    lyric_map = _build_lyric_map(bounds, decls, parts, [len(l.staves) for l in layouts])
    _write_lyric_metadata(root, parts, lyric_map)
    return PerSystemResult(parts=parts, lyric_map=lyric_map)
