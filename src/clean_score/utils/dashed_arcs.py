"""Mark a bar holding a dashed tie or slur in a sung part (#482).

A strophic song prints a tie or slur that belongs to a later verse only as a
**dashed** arc: verse 1 sings the two notes apart, verse 2 holds them. homr reads
it as a plain arc, so verse 1 lost a syllable. The fork now passes the dashes on
(`line-type="dashed"`, eerovil/homr#100) and MuseScore keeps them as
`<lineType>2</lineType>`.

Whether the arc should stay is a person's call: it is right for the verse that
holds the note and wrong for the one that does not, and the practice track sings
one. So the arc is kept and the bar it starts in gets a red `⚠ dashed tie?` or
`⚠ dashed slur?`. An `untie` or `unslur` fix answers it, and `unmark` keeps the arc.
"""

from lxml import etree

from .problem_marks import mark_bar
from .rejected_bars import staff_names
from .voice_instruments import _INSTRUMENT

#: MuseScore's `lineType` for a dashed line (1 is dotted, 0 or none is solid).
DASHED = "2"

#: The mark's words for each kind of arc. `score_fixes` reads these too.
WORDS = {"Tie": "dashed tie?", "Slur": "dashed slur?"}


def _dashed(spanner: etree._Element) -> bool:
    kind = spanner.get("type")
    return (spanner.find("next") is not None
            and (spanner.findtext(f"{kind}/lineType") or "").strip() == DASHED)


def mark_dashed_arcs(root: etree._Element) -> list[dict]:
    """Mark each bar where a dashed tie or slur starts in a sung part.

    Returns `{part, measure, kind}` per mark written (1-based bars). The arc is left
    as it is. A part named as an instrument is passed over: only a singer has a verse
    the arc can be wrong for.
    """
    names = staff_names(root)
    out: list[dict] = []
    for si, staff in enumerate(root.findall(".//Score/Staff"), start=1):
        if _INSTRUMENT.search(names.get(si, "")):
            continue
        for mi, measure in enumerate(staff.findall("Measure"), start=1):
            kinds = []
            for spanner in measure.iter("Spanner"):
                kind = spanner.get("type")
                if kind in WORDS and kind not in kinds and _dashed(spanner):
                    kinds.append(kind)
            for kind in kinds:
                if mark_bar(measure, WORDS[kind]):
                    out.append({"part": names.get(si, f"staff {si}"), "measure": mi,
                                "kind": kind.lower()})
    return out
