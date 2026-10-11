"""PROTOTYPE for #393 -- throwaway. One printed system at a time in the lyric editor.

Mounted only when LYRICS_PROTOTYPE=1, against a scratch songs/ folder. It reads and
writes verse N on voice 1 or 2 of one system with its own small placer, which is not
lyric_txt's: the question is how the screen feels, not how the import should be built.
"""
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from lxml import etree

from src.clean_score import lyric_txt as L
from src.song_app import pdf_systems, pipeline, state

router = APIRouter()


def _song(slug: str):
    song = state.load(slug)
    if song is None:
        raise HTTPException(404, "no such song")
    cleaned = song.cleaned_path()
    if not cleaned:
        raise HTTPException(400, "Clean the score first")
    return song, cleaned


def _staves(score) -> Dict[int, etree._Element]:
    return {int(s.get("id")): s for s in score.findall("Staff") if s.find("Measure") is not None}


def _eligible(staff, voice: int) -> List[Tuple[int, etree._Element]]:
    """(1-based measure, chord) for every chord of `voice` that takes a syllable."""
    out = []
    slur = tie = False
    for mi, measure in enumerate(staff.findall("Measure"), start=1):
        voices = measure.findall("voice")
        if len(voices) <= voice:
            continue
        for el in voices[voice]:
            if el.tag != "Chord":
                continue
            if L._is_continuation_no_lyric(el):
                slur, tie = L._after_continuation(el, slur, tie)
                continue
            if slur and not L._has_slur_start(el) and not L._is_slur_continuation(el):
                continue
            if tie and not L._has_tie_start(el) and not L._is_tie_continuation(el):
                continue
            out.append((mi, el))
            slur = slur or L._has_slur_start(el)
            tie = tie or L._has_tie_start(el)
    return out


def _has_voice(staff, voice: int, start: int, end: int) -> bool:
    ms = staff.findall("Measure")
    for m in ms[start - 1:end]:
        vs = m.findall("voice")
        if len(vs) > voice and vs[voice].find("Chord") is not None:
            return True
    return False


def _lyric(chord, verse: int):
    for ly in chord.findall("Lyrics"):
        no = (ly.findtext("no") or "").strip()
        if (verse == 1 and no in ("", "0")) or no == str(verse - 1):
            return ly
    return None


def _text(slots, verse: int) -> str:
    tokens = []
    for _, chord in slots:
        ly = _lyric(chord, verse)
        if ly is None or not (ly.findtext("text") or "").strip():
            tokens.append("_")
            continue
        syl = (ly.findtext("syllabic") or "single").strip()
        t = ly.findtext("text").strip()
        tokens.append(t + "-" if syl in ("begin", "middle") else t)
    if not any(t != "_" for t in tokens):
        return ""
    return L._merge_tokens(tokens).strip()


def _syllables(text: str, joined_before: bool) -> List[Optional[Tuple[str, str]]]:
    """Text -> one (syllabic, text) per slot, None for `_`. A word carries over `_`."""
    raw: List[Optional[List]] = []  # [text, joins_left, joins_right]
    carry = joined_before
    for token in text.split():
        if token == "_":
            raw.append(None)
            continue
        lead = token.startswith("-")
        trail = token.endswith("-")
        pieces = [p for p in token.strip("-").split("-") if p]
        for i, p in enumerate(pieces):
            left = (carry or lead) if i == 0 else True
            right = trail if i == len(pieces) - 1 else True
            raw.append([p, left, right])
        carry = trail
    out = []
    for r in raw:
        if r is None:
            out.append(None)
            continue
        p, left, right = r
        out.append(({(False, False): "single", (False, True): "begin",
                     (True, True): "middle", (True, False): "end"}[(left, right)], p))
    return out


def _systems(song) -> List[Dict]:
    ranges = pipeline._printed_systems(song.dir) or []
    crops = {b.measure_start: b.index for b in pdf_systems.load_bounds(song.dir) if b.measure_start}
    return [{"n": i, "start": a, "end": b, "crop": crops.get(a)} for i, (a, b) in enumerate(ranges)]


def _printed_map(root, start: int) -> Dict[int, List[int]]:
    """printed staff -> output staff ids, for the system starting at `start`."""
    import json
    tags = {m.get("name"): (m.text or "") for m in root.iter("metaTag")}
    if tags.get("lyricsSystemMap"):
        for entry in json.loads(tags["lyricsSystemMap"]):
            if entry["start"] <= start <= entry["end"]:
                return {int(k): v for k, v in entry["map"].items()}
    out = {}
    for pair in tags.get("lyricsStaffMap", "").split(";"):
        if ":" in pair:
            k, v = pair.split(":")
            out[int(k)] = [int(x) for x in v.split(",") if x]
    return out


def _staff_rows(song, crop: Optional[int]) -> List[Dict]:
    """Every staff found in the printed crop, as fractions of its height."""
    if crop is None:
        return []
    import numpy as np
    from PIL import Image
    from src.song_app import system_finder
    pdf = song.source_path("pdf")
    path = pipeline.system_crop(song.dir, pdf, crop, 150)
    with Image.open(path) as image:
        ink = np.asarray(image.convert("L")) < system_finder._INK
        h = ink.shape[0]
        return [{"top": max(0, t - 1.5 * s) / h, "bottom": min(h, b + 4.5 * s) / h}
                for t, b, s in system_finder._staves(ink)]


def _view(song, cleaned, n: int, warnings: Optional[Dict] = None) -> Dict:
    systems = _systems(song)
    if not 0 <= n < len(systems):
        raise HTTPException(404, "no such system")
    sys = systems[n]
    root = etree.parse(cleaned).getroot()
    score = root.find("Score")
    staves = _staves(score)
    parts = L.lyric_parts(root)
    verses = 1
    for ly in score.iter("Lyrics"):
        no = (ly.findtext("no") or "").strip()
        if no.isdigit():
            verses = max(verses, int(no) + 1)
    printed = _printed_map(root, sys["start"])
    printed_of = {sid: k for k, ids in printed.items() for sid in ids}
    boxes = []
    for p in parts:
        st = staves.get(p.id)
        if st is None:
            continue
        for voice in (0, 1):
            if voice == 1 and not _has_voice(st, 1, sys["start"], sys["end"]):
                continue
            if voice == 0 and not _has_voice(st, 0, sys["start"], sys["end"]):
                continue
            slots = [(m, c) for m, c in _eligible(st, voice) if sys["start"] <= m <= sys["end"]]
            if not slots:
                continue
            boxes.append({
                "key": f"{p.name}@{voice + 1}", "part": p.name, "voice": voice + 1,
                "printed": printed_of.get(p.id), "slots": len(slots),
                "text": {str(v): _text(slots, v) for v in range(1, verses + 1)},
            })
    # Printed staves that sing in this system, top first: what the crop should show.
    singing = sorted({b["printed"] for b in boxes if b["printed"] is not None})
    rows = _staff_rows(song, sys["crop"])
    row_of = {k: i for i, k in enumerate(singing)} if len(rows) == len(singing) else {}
    for b in boxes:
        b["row"] = row_of.get(b["printed"])
    return {"slug": song.slug, "name": song.data.get("name", song.slug), "system": sys,
            "count": len(systems), "verses": verses, "boxes": boxes, "rows": rows,
            "warnings": warnings or {}, "mtime": state.file_fingerprint(cleaned)}


@router.get("/api/proto/songs")
def proto_songs() -> List[Dict]:
    return [{"slug": s.slug, "name": s.data.get("name", s.slug)} for s in state.list_songs()
            if s.cleaned_path()]


@router.get("/api/proto/songs/{slug}/system/{n}")
def proto_system(slug: str, n: int) -> Dict:
    song, cleaned = _song(slug)
    return _view(song, cleaned, n)


@router.post("/api/proto/songs/{slug}/system/{n}")
def proto_import(slug: str, n: int, body: Dict) -> Dict:
    """Write the sent boxes of one verse into this system. Nothing else changes."""
    song, cleaned = _song(slug)
    sys = _systems(song)[n]
    verse = int(body.get("verse", 1))
    tree = etree.parse(cleaned)
    root = tree.getroot()
    staves = _staves(root.find("Score"))
    ids = {p.name: p.id for p in L.lyric_parts(root)}
    warnings = {}
    for key, text in (body.get("boxes") or {}).items():
        name, voice = key.rsplit("@", 1)
        voice = int(voice) - 1
        st = staves.get(ids.get(name))
        if st is None:
            continue
        everything = _eligible(st, voice)
        slots = [(m, c) for m, c in everything if sys["start"] <= m <= sys["end"]]
        before = [c for m, c in everything if m < sys["start"]]
        prev = _lyric(before[-1], verse) if before else None
        joined = prev is not None and (prev.findtext("syllabic") or "") in ("begin", "middle")
        # Clear this verse on every chord of this voice in these bars, then place.
        for m, measure in enumerate(st.findall("Measure"), start=1):
            if not sys["start"] <= m <= sys["end"]:
                continue
            vs = measure.findall("voice")
            if len(vs) > voice:
                for chord in vs[voice].findall("Chord"):
                    ly = _lyric(chord, verse)
                    if ly is not None:
                        chord.remove(ly)
        syls = _syllables(text or "", joined)
        if not (text or "").strip():
            continue
        for (m, chord), syl in zip(slots, syls):
            if syl is None:
                continue
            ly = etree.SubElement(chord, "Lyrics")
            if verse > 1:
                etree.SubElement(ly, "no").text = str(verse - 1)
            etree.SubElement(ly, "syllabic").text = syl[0]
            etree.SubElement(ly, "text").text = syl[1]
        if len(syls) != len(slots):
            warnings[key] = (f"{len(syls)} syllables for {len(slots)} notes"
                             + (" — the extra ones were left out" if len(syls) > len(slots) else ""))
    tree.write(cleaned, encoding="utf-8", xml_declaration=True, pretty_print=True)
    return _view(song, cleaned, n, warnings)


@router.get("/api/proto/songs/{slug}/landed/{n}")
def proto_landed(slug: str, n: int):
    """This system's bars of the edited score, drawn by verovio, words and all."""
    import hashlib
    import os
    import verovio
    from fastapi.responses import Response
    from src.scrollvideo import audio, engrave
    song, cleaned = _song(slug)
    sys = _systems(song)[n]
    stamp = hashlib.sha1(open(cleaned, "rb").read()).hexdigest()[:12]
    cache = os.path.join(song.dir, ".proto")
    os.makedirs(cache, exist_ok=True)
    xml = os.path.join(cache, f"{stamp}.musicxml")
    if not os.path.exists(xml):
        for old in os.listdir(cache):
            os.remove(os.path.join(cache, old))
        audio.run_musescore(cleaned, xml)
    tk = verovio.toolkit(False)
    tk.setResourcePath(engrave.RESOURCE_PATH)
    tk.setOptions({"pageWidth": 2400, "adjustPageHeight": True, "header": "none",
                   "footer": "none", "breaks": "auto", "scale": 45})
    tk.loadFile(xml)
    tk.select({"measureRange": f"{sys['start']}-{sys['end']}"})
    tk.redoLayout()
    return Response(tk.renderToSVG(1), media_type="image/svg+xml")
