"""Give each singing part a voice instrument, so MuseScore 4 puts dynamics above it (#481).

A cleaned part's `<Instrument>` carries no instrument id. MuseScore 4 reads a part
without one as a grand piano, and a dynamic added to a piano staff goes below it,
where a choir score has its words. With a voice instrument (`voice.soprano` and so
on) the same dynamic goes above the staff. MuseScore 3 shows no difference.

The id comes from the part's name, the names cleaning gives (`S1`, `A2b`, `T`,
`B2`, `Bar`, or a spelt-out `Soprano 1`); any other name gets the plain `voice`.
Only the id is written: the `Channel` and its `program` stay as they are, so the
sound of the score and of its MIDI does not change. Percussion and parts with only
rests (the click staff) are left alone, and so is a part named as an instrument
(`Piano RH`, `Violin`, `Urut`, `Alto Sax`). Only a part with no id is given one, or a part with
a piano id: MuseScore 3 gives an imported MusicXML part `piano`, and MuseScore 4
saves a part with no id as `grand-piano`. Any other id is someone's choice and is kept.

Dynamics already in a score keep the side they were written on: MuseScore 4 writes
that side out when it converts an older file.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from lxml import etree

# MuseScore's own ids: `<Instrument id=...>` and its `<instrumentId>`.
VOICES: Dict[str, Tuple[str, str]] = {
    "soprano": ("soprano", "voice.soprano"),
    "alto": ("alto", "voice.alto"),
    "tenor": ("tenor", "voice.tenor"),
    "baritone": ("baritone", "voice.baritone"),
    "bass": ("bass", "voice.bass"),
}
OTHER: Tuple[str, str] = ("voice", "voice.vocals")

_NAME = re.compile(
    r"^(?:(?P<baritone>bar|baritone?i?)|(?P<soprano>s|sop\w*)|(?P<alto>a|alt\w*)"
    r"|(?P<tenor>t|ten\w*)|(?P<bass>b|bass\w*|basso))"
    r"[\s\d,/&+-]*[a-z]?$",
    re.IGNORECASE,
)


# What MuseScore 3 and 4 give a part nobody chose an instrument for.
_PIANO = {"piano", "grand-piano", "keyboard.piano", "keyboard.piano.grand"}

# A part named like this is an instrument, not a singer (English and Finnish).
_INSTRUMENT = re.compile(
    r"piano|organ|urut|viol|viulu|cello|sello|kontrabass|contrabass|flute|huilu"
    r"|guitar|kitara|harp|clarinet|klarinetti|trumpet|horn|torvi|oboe|fagot|bassoon"
    r"|keyboard|synth|strings|jouset|accomp|säestys|drum|rumpu|perc"
    r"|trombon|pasuuna|tuba|tuuba|sax|timpan|cembal|harpsi|harmon|celest|accordion"
    r"|haitari|continuo|double bass|kontrabasso|fagott",
    re.IGNORECASE,
)


def voice_for(name: Optional[str]) -> Tuple[str, str]:
    """The `(id, instrumentId)` for a part called `name`."""
    match = _NAME.match((name or "").strip())
    if not match:
        return OTHER
    for kind in ("baritone", "soprano", "alto", "tenor", "bass"):
        if match.group(kind):
            return VOICES[kind]
    return OTHER


def needs_voice_instruments(root: etree._Element) -> bool:
    """Whether `set_voice_instruments` would change anything."""
    return bool(_plan(root))


def set_voice_instruments(root: etree._Element) -> int:
    """Write a voice instrument id onto every singing part. Returns how many parts changed."""
    parts = set()
    for instrument, (ident, instrument_id) in _plan(root):
        instrument.set("id", ident)
        node = instrument.find("instrumentId")
        if node is None:
            node = etree.Element("instrumentId")
            anchor = instrument.find("trackName")
            if anchor is None:
                anchor = instrument.find("shortName")
            if anchor is None:
                anchor = instrument.find("longName")
            if anchor is not None:
                anchor.addnext(node)
            else:
                instrument.insert(0, node)
        node.text = instrument_id
        parts.add(instrument.getparent())
    return len(parts)


def _plan(root: etree._Element):
    # Imported here: score_export reads clean_score, not the other way round at load.
    from src.score_export.score import _part_name, _staff_ids

    score = root.find("Score")
    if score is None:
        return []
    music = {staff.get("id"): staff for staff in score.findall("Staff")}
    out = []
    for index, part in enumerate(score.findall("Part")):
        name = _part_name(part, index)
        # Silent as `score_export.score.silent_parts` says, but judged per part:
        # two parts may share a name, and only the one that rests is left alone.
        percussion = (part.find(".//Instrument[@id='drumset']") is not None
                      or (part.findtext(".//useDrumset") or "").strip() == "1")
        if percussion or not any(music[i].find(".//Chord") is not None
                                 for i in _staff_ids(part) if i in music):
            continue
        if _INSTRUMENT.search(name):
            continue
        wanted = voice_for(name)
        for instrument in part.findall("Instrument"):
            have = {v for v in (instrument.get("id"),
                                (instrument.findtext("instrumentId") or "").strip()) if v}
            if have <= _PIANO:
                out.append((instrument, wanted))
    return out
