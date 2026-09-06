"""Health check — surface OCR damage in the cleaned score as a punch list.

Validation only; never mutates the score. Findings:
  - malformed-measure: a voice whose note/rest durations don't fill the bar
    (the auto-fixers couldn't repair it — likely lost notes or a bad tuplet).
  - extra-voices: a staff measure with more than one note-bearing voice
    (the split didn't fully separate the voices).
  - unprinted-meter: a bar every voice agrees on, at a length the engraving never
    prints. The engraving is the only authority on meter, and this is the one
    finding that does not compare the score against itself — every other check
    here can be satisfied by a wrong answer that is merely self-consistent. It
    exists because a repair pass once "fixed" a 4/4 bar by padding every voice to
    9/8 and passed everything. It stays out of music that has no meter to violate:
    scores carrying an oversized nominal instead of a signature.
  - meter-collapsed: the same finding, counted instead of listed, for a score
    whose bars mostly declare their own length. See below — this used to be
    silence, and silence was the bug.

Missing notes that *do* fill the bar (a half-rest standing in for lost notes)
aren't tick-detectable; they surface as lyric syllable overflow at import time.
Missing slurs are undetectable and stay manual.

All durations are computed as exact whole-note Fractions so tuplets don't cause
rounding false-positives.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Dict, Iterable, List, Optional

from lxml import etree

# durationType (and fraction) -> whole-note fraction
_DUR = {
    "whole": Fraction(1, 1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8), "16th": Fraction(1, 16), "32nd": Fraction(1, 32),
    "64th": Fraction(1, 64), "128th": Fraction(1, 128), "256th": Fraction(1, 256),
}
_DOT_MULT = {0: Fraction(1), 1: Fraction(3, 2), 2: Fraction(7, 4), 3: Fraction(15, 8)}

# Longest bar a printed time signature plausibly asks for (4/2, 8/4, 12/8 all fit).
# Anything longer is not a meter: music printed without one is carried in MuseScore
# as an oversized nominal — one score here declares 16/2, eight whole notes — with
# each phrase given its own length. There is no meter to violate, so the
# unprinted-meter rule stays silent under one.
_PLAUSIBLE_METER = Fraction(2)

# A score where most bars declare their own length may not be in a fixed meter -- it
# may be mixed or free, with MuseScore carrying the length bar by bar. One score here
# (Venematka) has an override on 20 of its 25 bars and would otherwise be reported 66
# times for being what it is.
#
# It used to switch the meter rule off entirely, and that was a hole. "Carries a length
# override" is also what a badly parsed score looks like: `fix_overfull_measures` writes
# one on every bar whose content contradicts the running signature, so the check turned
# itself off exactly when the score was worst. Benchmark page B6 crossed the line by
# being MORE wrong -- it misread an opening 5/4 as 3/4, which needed an override on bars
# 1 and 2 of all four staves, and those eight overrides carried it from 50% to 56%. It
# reported 3 issues where the same score judged on the same rules as its per-system
# parse has 32. A score bought silence by being worse.
#
# So the share no longer decides WHETHER to judge, only HOW TO SAY IT: above the line
# the bars are counted into one `meter-collapsed` finding instead of listed one by one.
# The noise the escape exists to prevent is still prevented -- Venematka's 66 become a
# single line -- and the count is on the wire either way, so the two sides of the line
# are comparable rather than one of them being blank.
#
# 0.5 is left where it was. Across the 35 cleaned scores in `songs/` the override share
# is 0.19 or below for every score except Venematka at 0.76-0.80, so the line sits in an
# empty gap and nothing real is near it; B6 is, and that now costs presentation instead
# of silence.
_FREE_METER_SHARE = 0.5


def _parse_fraction(text: Optional[str]) -> Optional[Fraction]:
    if not text:
        return None
    try:
        if "/" in text:
            n, d = text.split("/")
            return Fraction(int(n), int(d))
        return Fraction(int(text), 1)
    except (ValueError, ZeroDivisionError):
        return None


def _chord_rest_len(el: etree._Element, tuplet_scale: Fraction) -> Optional[Fraction]:
    dt = el.findtext("durationType")
    if dt == "measure":
        return None  # measure-length rest; handled by caller as the full bar
    base = _DUR.get((dt or "").strip())
    if base is None:
        return Fraction(0)
    dots = 0
    de = el.find("dots")
    if de is not None and (de.text or "").strip().isdigit():
        dots = int(de.text.strip())
    return base * _DOT_MULT.get(dots, Fraction(1)) * tuplet_scale


def _voice_length(voice: etree._Element, nominal: Fraction) -> tuple[Fraction, bool, bool]:
    """Return (summed length, has_chord, is_measure_rest) for a voice element."""
    total = Fraction(0)
    has_chord = False
    tuplet_scale = Fraction(1)
    measure_rest = False
    for el in voice:
        if el.tag == "Tuplet":
            actual = _parse_fraction(el.findtext("actualNotes")) or Fraction(1)
            normal = _parse_fraction(el.findtext("normalNotes")) or Fraction(1)
            if actual:
                tuplet_scale = normal / actual
        elif el.tag == "endTuplet":
            tuplet_scale = Fraction(1)
        elif el.tag in ("Chord", "Rest"):
            if el.tag == "Chord":
                has_chord = True
            length = _chord_rest_len(el, tuplet_scale)
            if length is None:  # measure rest
                total += nominal
                measure_rest = True
            else:
                total += length
        elif el.tag == "location":
            frac = _parse_fraction(el.findtext("fractions"))
            if frac is not None:
                total += frac
    return total, has_chord, measure_rest


def scan(cleaned_path: str) -> List[Dict]:
    """Return a list of issue dicts (without status) for the cleaned score."""
    with open(cleaned_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    score = root if root.tag == "Score" else root.find(".//Score")

    # Map staff id -> part display name (for friendly labels).
    staff_name: Dict[int, str] = {}
    for part in score.findall("Part"):
        name = part.findtext("trackName") or part.findtext("Instrument/trackName") or ""
        for st in part.findall("Staff"):
            staff_name[int(st.get("id", "0"))] = name.strip()

    # Does this score mostly carry its own bar lengths? Decided once, for the whole
    # score -- and it decides how the meter finding is *said*, not whether it is made.
    all_measures = score.findall(".//Staff/Measure")
    overridden = sum(1 for m in all_measures if m.get("len"))
    override_share = overridden / len(all_measures) if all_measures else 0.0
    collapse_meter = bool(all_measures) and override_share > _FREE_METER_SHARE

    issues: List[Dict] = []
    collapsed: List[Dict] = []
    for staff in score.findall("Staff"):
        sid = int(staff.get("id", "0"))
        label = staff_name.get(sid) or f"staff {sid}"
        sig = Fraction(4, 4)
        for mi, measure in enumerate(staff.findall("Measure"), start=1):
            # Time signature can change at a measure (in any voice).
            ts = measure.find(".//TimeSig")
            if ts is not None:
                n = _parse_fraction(ts.findtext("sigN"))
                d = _parse_fraction(ts.findtext("sigD"))
                if n and d:
                    sig = Fraction(int(n), int(d))
            # Anacrusis / pickup measures override the nominal length.
            nominal = sig
            len_attr = _parse_fraction(measure.get("len"))
            if len_attr is not None:
                nominal = len_attr

            voices = measure.findall("voice")
            note_bearing = 0
            uneven = False
            for vi, voice in enumerate(voices):
                total, has_chord, _ = _voice_length(voice, nominal)
                if has_chord:
                    note_bearing += 1
                # Only flag voices that carry notes and don't fill the bar.
                if has_chord and total != nominal:
                    uneven = True
                    issues.append({
                        "id": f"malformed-m{mi}-s{sid}-v{vi}",
                        "kind": "malformed-measure",
                        "measure": mi,
                        "staff": label,
                        "detail": f"voice {vi + 1} fills {total} of {nominal}",
                    })
            # The engraving is the only authority on meter. A measure whose
            # voices agree on a length the printed time signature does not give,
            # and which prints no signature of its own, is claiming a meter
            # nobody wrote down. That is almost always an OCR artefact -- or a
            # repair that "fixed" a bar by agreeing with the damage. Measure 1 is
            # exempt: an anacrusis is a real engraving feature with no signature.
            # Only when the bar is otherwise sound: an uneven bar is already
            # reported, and this is about the case nothing else can see -- every
            # voice agreeing on a meter that was never printed.
            if mi > 1 and ts is None and not uneven and sig <= _PLAUSIBLE_METER:
                agreed = {t for t, has, _ in
                          (_voice_length(v, nominal) for v in voices) if has}
                if len(agreed) == 1 and agreed != {sig}:
                    got = agreed.pop()
                    found = {
                        "id": f"unprinted-meter-m{mi}-s{sid}",
                        "kind": "unprinted-meter",
                        "measure": mi,
                        "staff": label,
                        "detail": f"bar is {got} but the engraving says {sig}",
                    }
                    (collapsed if collapse_meter else issues).append(found)
            if note_bearing > 1:
                issues.append({
                    "id": f"extra-voices-m{mi}-s{sid}",
                    "kind": "extra-voices",
                    "measure": mi,
                    "staff": label,
                    "detail": f"{note_bearing} note-bearing voices on one staff",
                })

    # One line instead of dozens -- but a line. A score whose bars mostly declare
    # their own length may be free-metered, in which case this is describing it
    # rather than accusing it; it may equally be a parse damaged enough that the
    # repairs wrote a length onto half the bars, and those two look identical from
    # here. Saying how many bars and where the first one is lets a person tell them
    # apart in the score, which is where the answer actually is. Nothing to count is
    # nothing to say: a free-metered score that agrees with itself stays clean.
    if collapsed:
        bars = sorted({i["measure"] for i in collapsed})
        issues.append({
            "id": f"meter-collapsed-{len(collapsed)}",
            "kind": "meter-collapsed",
            "measure": bars[0],
            "staff": "whole score",
            # How many findings this one row stands for. It is a number rather than
            # a sentence because every count the app shows has to be able to add it
            # up: a row that reads as 1 puts the magnitude back in the detail string,
            # where the summary cannot see it, and the score is silent all over
            # again in the one place a person compares two scans.
            "collapsed": len(collapsed),
            "collapsed_bars": len(bars),
            # The bars themselves, not just how many. `verdict` asks how much of the
            # score is affected, and that is a union over rows -- a count cannot be
            # unioned with anything, so a collapsed row would have had to be guessed
            # at exactly where the score is worst.
            "collapsed_measures": bars,
            "detail": (
                f"{len(bars)} bar(s) sit at a length the engraving never prints "
                f"({len(collapsed)} staff-bars, first at m{bars[0]}), listed as one "
                f"line because {override_share:.0%} of bars carry their own length — "
                f"free or mixed meter looks like this, and so does a badly parsed score"
            ),
        })
    return issues


def finding_count(issues: Iterable[Dict]) -> int:
    """How many findings these rows stand for, not how many rows there are.

    A `meter-collapsed` row is one row carrying `collapsed` findings, so every count
    a person reads has to weigh it. Counting rows is what made B6's whole-page parse
    look like 3 problems next to a per-system parse's 28 -- the comparison that
    nearly cost a real decision (#122). Collapsing the *presentation* is the point;
    collapsing the *number* would be the same bug in a new place.
    """
    return sum(int(i.get("collapsed") or 1) for i in issues)


# ---------------------------------------------------------------------------
# Is this parse worth repairing at all?
#
# A count is not a verdict. Sixty findings was shown to an operator as sixty rows
# each with a Dismiss button, and what he said back was "sixty is very probably a
# garbage scan, but I would have to see it with my eyes" -- which is a different
# claim from "sixty issues", and it is the claim the app was never making. A parse
# that rough is not a repair list; it is a reading to go and check against the page,
# and possibly to throw away. So the app says that, in those words, and then gets
# out of the way: this is a warning that aims attention, never a refusal. The call
# he described is one he keeps.
#
# WHICH SIGNAL. Raw count is the obvious candidate and is the wrong one -- a long
# song earns more findings than a short one for being long, and a wide score earns
# more than a narrow one for having more staves. Three candidates were measured over
# the 46 scores in `songs/` that carry a health record (see the pull request for the
# table): findings per bar, findings per staff-bar, and the share of bars carrying at
# least one finding. The last is the one used here. It is bounded, it does not move
# with the number of staves or the length of the piece, and it says something a person
# can act on out loud -- "more than a fifth of the bars of this score have something
# wrong with them" -- where a density does not.
#
# WHERE THE LINE IS. Measured, not picked. On those 46 scores the share runs
# 0.000 (30 scores), then 0.015 up to 0.121 (13 scores), then nothing at all until
# 0.260, 0.299 and 0.538. The three above the gap are the two worst parses on this
# host and the walk's own song, the one the operator called garbage. 0.2 sits in the
# empty middle with nothing near it, which is the same shape of argument
# `_FREE_METER_SHARE` rests on.
_UNUSABLE_BAR_SHARE = 0.2

# ...and a share alone would condemn a short score for one bad bar: three bars out of
# fourteen is 0.21. A verdict about a whole parse should not be reachable by two
# findings, so it takes a handful of affected bars as well as a large share of them.
_UNUSABLE_MIN_BARS = 3


def bars_touched(issues: Iterable[Dict]) -> int:
    """How many distinct bars these findings land in.

    A collapsed meter row stands for many bars, so it contributes all of them --
    otherwise the score most worth judging is the one that looks smallest, which is
    exactly the hole #124 closed one level up.
    """
    bars = set()
    for issue in issues:
        listed = issue.get("collapsed_measures")
        if listed:
            bars.update(int(b) for b in listed)
        elif issue.get("collapsed"):
            # Written before the bar numbers were recorded: all we have is how many.
            # Offsetting them past the real bars would double-count nothing, but it
            # would also be a lie about *which* bars, so they are simply added on.
            bars.update(range(-int(issue["collapsed_bars"] or 0), 0))
        elif issue.get("measure") is not None:
            bars.add(int(issue["measure"]))
    return len(bars)


def score_bars(cleaned_path: str) -> int:
    """How many bars the cleaned score is long (the longest staff)."""
    with open(cleaned_path, "r", encoding="utf-8") as f:
        root = etree.fromstring(f.read().encode("utf-8"))
    score = root if root.tag == "Score" else root.find(".//Score")
    if score is None:
        return 0
    return max((len(st.findall("Measure")) for st in score.findall("Staff")), default=0)


def verdict(issues: Iterable[Dict], bars: int) -> Dict:
    """Judge the parse as a whole: `clean`, `repairable`, or `unusable`.

    `issues` are the open findings; `bars` is the length of the score they were
    found in. Returns the numbers as well as the sentence, so a caller can say it
    its own way without recomputing the rule.
    """
    issues = list(issues)
    findings = finding_count(issues)
    touched = min(bars_touched(issues), bars) if bars else bars_touched(issues)
    share = (touched / bars) if bars else 0.0
    if not findings:
        return {"level": "clean", "findings": 0, "bars": bars, "bars_touched": 0,
                "share": 0.0, "message": "Nothing to repair."}
    if bars and share > _UNUSABLE_BAR_SHARE and touched >= _UNUSABLE_MIN_BARS:
        return {
            "level": "unusable", "findings": findings, "bars": bars,
            "bars_touched": touched, "share": share,
            "message": (
                f"This parse looks unusable: {touched} of {bars} bars ({share:.0%}) "
                f"carry a finding. That is not a repair list — read it against the "
                f"page before going on. Nothing here stops you; the judgement is yours."
            ),
        }
    return {
        "level": "repairable", "findings": findings, "bars": bars,
        "bars_touched": touched, "share": share,
        "message": (f"{findings} finding(s) in {touched} of {bars} bars"
                    if bars else f"{findings} finding(s)"),
    }


def merge_issues(found: List[Dict], previous: List[Dict]) -> List[Dict]:
    """Carry over dismissed status; mark previously-open issues that are gone as fixed.

    Returns the new issue list: current findings (status preserved if dismissed),
    plus previously-fixed/dismissed entries that no longer appear are dropped, and
    a previously-open issue absent from `found` is recorded as fixed (so the UI can
    show it ticked off briefly — callers may filter to status=='open').
    """
    prev_by_id = {i["id"]: i for i in previous}
    found_ids = {i["id"] for i in found}
    merged: List[Dict] = []
    for issue in found:
        prev = prev_by_id.get(issue["id"])
        issue = dict(issue)
        issue["status"] = "dismissed" if prev and prev.get("status") == "dismissed" else "open"
        merged.append(issue)
    # Previously-open issues now gone -> fixed.
    for prev in previous:
        if prev["id"] not in found_ids and prev.get("status") == "open":
            done = dict(prev)
            done["status"] = "fixed"
            merged.append(done)
    return merged
