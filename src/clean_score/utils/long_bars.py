"""Cut a bar that runs longer than its time signature back to it (#238).

A scan that misreads a rhythm can leave a bar longer than the page prints: on
Sangerhilsen bars 21 and 37 a triplet was read as plain eighths, MuseScore's import
gave the bar `len="9/8"` under 4/4, and the practice track played an extra eighth in
every part. Nothing earlier repairs it — `fix_overfull_measures` needs one voice that
already fills the real bar, and here none does.

So every voice is cut at the barline the signature puts there: what lies past it is
taken out, a note across it is shortened to fit (or, if that length cannot be written
as one note, taken out too), a tuplet across it goes whole, and the voice is filled
back up to the barline with rests. The bar's own length goes. The rhythm inside the bar
is not rebuilt — that is the page's to say — so each staff whose notes changed gets a
red mark naming them.
"""
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from lxml import etree

from .problem_marks import mark_bar
from .rejected_bars import _DOT, _DUR, _fraction, staff_names
from .score_fixes import note_name

# Longest bar a printed time signature plausibly asks for; past it the score carries an
# oversized nominal instead of a meter (see `health._PLAUSIBLE_METER`), and there is no
# signature for a bar to run past.
_PLAUSIBLE_METER = Fraction(2)

_SPELLING = {}
for _kind, _value in _DUR.items():
    for _dots in (0, 1, 2):
        _SPELLING.setdefault(_value * _DOT[_dots], (_kind, _dots))


def _meters(staff: etree._Element) -> List[Tuple[Fraction, str, bool]]:
    """Per bar: (meter in force, how it is printed, whether this bar prints one)."""
    meter, shown = Fraction(1), "4/4"
    out = []
    for measure in staff.findall("Measure"):
        ts = measure.find(".//TimeSig")
        if ts is not None and ts.findtext("sigN") and ts.findtext("sigD"):
            n, d = int(ts.findtext("sigN")), int(ts.findtext("sigD"))
            meter, shown = Fraction(n, d), f"{n}/{d}"
        out.append((meter, shown, ts is not None))
    return out


def _length(el: etree._Element, scale: Fraction) -> Fraction:
    kind = (el.findtext("durationType") or "").strip()
    dots = int((el.findtext("dots") or "0").strip() or 0)
    return _DUR.get(kind, Fraction(0)) * _DOT.get(dots, Fraction(1)) * scale


def _notes(el: etree._Element) -> List[str]:
    out = []
    for note in el.iter("Note"):
        pitch, tpc = note.findtext("pitch"), note.findtext("tpc")
        if pitch and pitch.strip().isdigit():
            out.append(note_name(int(pitch), int(tpc) if tpc and tpc.strip().lstrip("-").isdigit() else None))
    return out


def _set_length(el: etree._Element, value: Fraction) -> bool:
    spelt = _SPELLING.get(value)
    if spelt is None:
        return False
    kind, dots = spelt
    for old in el.findall("dots"):
        el.remove(old)
    if dots:
        el.insert(0, etree.Element("dots"))
        el[0].text = str(dots)
    duration = el.find("durationType")
    if duration is None:
        duration = etree.SubElement(el, "durationType")
    duration.text = kind
    return True


def _rests(length: Fraction) -> List[etree._Element]:
    """Rests filling `length` exactly, or none when it cannot be written out.

    A scan can leave a voice off the beat grid (a stray 1/12 gap at the head of the
    bar); its gap is then left open, which MuseScore fills with rests as it reads.
    """
    out = []
    for kind, value in sorted(_DUR.items(), key=lambda kv: -kv[1]):
        while length >= value:
            rest = etree.Element("Rest")
            etree.SubElement(rest, "durationType").text = kind
            out.append(rest)
            length -= value
    return out if length == 0 else []


def _cut_voice(voice: etree._Element, meter: Fraction) -> Optional[Dict]:
    """Cut one voice at `meter`. None if it already ends by then; else what changed."""
    for el in voice:
        if el.tag == "Rest" and (el.findtext("durationType") or "").strip() == "measure":
            duration = el.find("duration")
            if duration is not None:
                duration.text = f"{meter.numerator}/{meter.denominator}"
            return None
    children = list(voice)
    pos, scale = Fraction(0), Fraction(1)
    group: Optional[Tuple[int, Fraction]] = None  # where the open tuplet began
    cut: Optional[Tuple[int, Fraction]] = None
    shortened: List[str] = []
    for i, el in enumerate(children):
        if el.tag == "Tuplet":
            n, a = _fraction(el.findtext("normalNotes")), _fraction(el.findtext("actualNotes"))
            scale = (n / a) if (n and a) else Fraction(1)
            group = (i, pos)
            continue
        if el.tag == "endTuplet":
            scale, group = Fraction(1), None
            continue
        if el.tag == "location":
            step = _fraction(el.findtext("fractions")) or Fraction(0)
        elif el.tag in ("Chord", "Rest"):
            step = _length(el, scale)
        else:
            continue
        if pos + step <= meter:
            pos += step
            continue
        if group is not None:
            cut = group
        elif el.tag in ("Chord", "Rest") and pos < meter and _set_length(el, meter - pos):
            if el.tag == "Chord":
                shortened = _notes(el)
            cut = (i + 1, meter)
        else:
            cut = (i, pos)
        break
    if cut is None:
        return None
    index, at = cut
    removed: List[str] = []
    for el in children[index:]:
        if el.tag == "Chord":
            removed.extend(_notes(el))
        voice.remove(el)
    for rest in _rests(meter - at):
        voice.append(rest)
    return {"cut_at": at, "removed": removed, "shortened": shortened}


def _halves(staff: etree._Element) -> List[Tuple[etree._Element, etree._Element, int, int, Fraction]]:
    """Every slur or tie half on a staff: (parent, spanner, voice, bar it points at, pos)."""
    out = []
    for mi, measure in enumerate(staff.findall("Measure")):
        for vi, voice in enumerate(measure.findall("voice")):
            pos, scale = Fraction(0), Fraction(1)
            for el in voice:
                held = []
                if el.tag == "Spanner":
                    held = [(voice, el)]
                elif el.tag == "Chord":
                    held = [(el, sp) for sp in el.findall("Spanner")] + [
                        (note, sp) for note in el.findall("Note") for sp in note.findall("Spanner")]
                for parent, spanner in held:
                    for side in ("next", "prev"):
                        loc = spanner.find(f"{side}/location")
                        if loc is None:
                            continue
                        bar = mi + int((loc.findtext("measures") or "0").strip() or 0)
                        target_voice = vi + int((loc.findtext("voices") or "0").strip() or 0)
                        at = pos + (_fraction(loc.findtext("fractions")) or 0)
                        out.append((parent, spanner, target_voice, bar, at))
                if el.tag == "Tuplet":
                    n, a = _fraction(el.findtext("normalNotes")), _fraction(el.findtext("actualNotes"))
                    scale = (n / a) if (n and a) else Fraction(1)
                elif el.tag == "endTuplet":
                    scale = Fraction(1)
                elif el.tag == "location":
                    pos += _fraction(el.findtext("fractions")) or 0
                elif el.tag in ("Chord", "Rest"):
                    pos += _length(el, scale)
    return out


def trim_long_bars(root: etree._Element) -> List[Dict]:
    """Cut every bar longer than its signature back to it. Returns one record per bar.

    A record is `{measure, was, meter, staves: [{staff, part, removed, shortened}]}`,
    with `measure` and `staff` 1-based and `staves` naming only staves whose notes
    changed. A bar that prints its own signature, the first bar (an anacrusis), and a
    bar shorter than its signature are left alone.
    """
    staves = root.findall(".//Score/Staff")
    if not staves:
        return []
    names = staff_names(root)
    meters = [_meters(staff) for staff in staves]
    measures = [staff.findall("Measure") for staff in staves]
    records = []
    for mi in range(1, len(measures[0])):
        if any(mi >= len(m) or meters[si][mi][2] for si, m in enumerate(measures)):
            continue
        meter, shown, _ = meters[0][mi]
        was = next((_fraction(m[mi].get("len")) for m in measures if m[mi].get("len")), None)
        if was is None or was <= meter or meter > _PLAUSIBLE_METER:
            continue
        changed = []
        for si, staff in enumerate(staves):
            measure = measures[si][mi]
            cuts = {}
            for vi, voice in enumerate(measure.findall("voice")):
                done = _cut_voice(voice, meter)
                if done is not None:
                    cuts[vi] = done
            for parent, spanner, voice_no, bar, at in _halves(staff):
                if bar == mi and voice_no in cuts and at >= cuts[voice_no]["cut_at"] \
                        and spanner.getparent() is parent:
                    parent.remove(spanner)
            removed = [n for c in cuts.values() for n in c["removed"]]
            shortened = [n for c in cuts.values() for n in c["shortened"]]
            if removed or shortened:
                changed.append({"staff": si + 1, "part": names.get(si + 1),
                                "removed": removed, "shortened": shortened})
        for m in measures:
            if "len" in m[mi].attrib:
                del m[mi].attrib["len"]
        record = {"measure": mi + 1, "was": f"{was.numerator}/{was.denominator}",
                  "meter": shown, "staves": changed}
        records.append(record)
        for one in changed or [{"staff": 1, "removed": [], "shortened": []}]:
            what = []
            if one["removed"]:
                what.append("removed " + " ".join(one["removed"]))
            if one["shortened"]:
                what.append("shortened " + " ".join(one["shortened"]))
            mark_bar(measures[one["staff"] - 1][mi],
                     f"was {record['was']}, cut to {shown}"
                     + (f"; {', '.join(what)}" if what else ""))
    return records
