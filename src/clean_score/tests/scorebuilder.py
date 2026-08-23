"""Small synthetic scores for lyric tests, plus reading the placed lyrics back."""

import json

from lxml import etree

from src.clean_score.lyric_txt import export_lyrics


def build_score(staff_ids=(1, 2, 3, 4, 5, 6), measures=2, chords=8, names=None,
                staff_map=None, system_map=None, line_breaks=()):
    """A minimal score: one voice per staff, `chords` lyric slots in every measure.

    `line_breaks` are 1-based measure numbers that end a printed system (the unit the
    lyric JSON is written in).
    """
    xml = ["<museScore><Score>"]
    if staff_map:
        xml.append(f'<metaTag name="lyricsStaffMap">{staff_map}</metaTag>')
    if system_map:
        xml.append('<metaTag name="lyricsSystemMap">'
                   + json.dumps(system_map, separators=(",", ":")) + "</metaTag>")
    for sid in staff_ids:
        name = (names or {}).get(sid, f"P{sid}")
        xml.append(f'<Part><trackName>{name}</trackName><Staff id="{sid}"/></Part>')
    for sid in staff_ids:
        xml.append(f'<Staff id="{sid}">')
        for m in range(1, measures + 1):
            xml.append("<Measure><voice>")
            for _ in range(chords):
                xml.append("<Chord><durationType>eighth</durationType>"
                           "<Note><pitch>60</pitch></Note></Chord>")
            xml.append("</voice>")
            if m in line_breaks and sid == staff_ids[0]:
                xml.append("<LayoutBreak><subtype>line</subtype></LayoutBreak>")
            xml.append("</Measure>")
        xml.append("</Staff>")
    xml.append("</Score></museScore>")
    return etree.fromstring("".join(xml).encode("utf-8"))


def placed_lyrics(root):
    """{staff_id: "the words that ended up there"} — read back out of the score."""
    out = {}
    measure = None
    for line in export_lyrics(root).splitlines():
        line = line.strip()
        if line.startswith("# Measure"):
            measure = int(line.split()[-1])
        elif line and measure is not None:
            head, _, tokens = line.partition(":")
            sid = int(head.split("[")[0].strip())
            words = " ".join(t for t in tokens.split() if t != "_")
            if words:
                out[sid] = (out.get(sid, "") + " " + words).strip()
    return out
