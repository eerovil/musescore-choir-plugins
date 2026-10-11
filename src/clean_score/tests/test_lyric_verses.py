"""A lyric import writes verse 1 and leaves verses 2+ exactly as they are (#392).

It used to delete every verse but the first on the whole score at the end of each
import: Saunatrilogia's verses 2 and 3 (bars 43-62) would have gone with the next one.
"""

from lxml import etree

from src.clean_score.lyric_txt import editor_grid, export_lyrics, place_lyrics

from .scorebuilder import build_score


def _add_verse(root, staff_id, measure, chord_index, no, text, syllabic="single"):
    staff = [s for s in root.find(".//Score").findall("Staff")
             if s.get("id") == str(staff_id)][0]
    chord = staff.findall("Measure")[measure - 1].findall(".//Chord")[chord_index]
    lyrics = etree.SubElement(chord, "Lyrics")
    etree.SubElement(lyrics, "no").text = str(no)
    etree.SubElement(lyrics, "syllabic").text = syllabic
    etree.SubElement(lyrics, "text").text = text


def _verses(root, wanted):
    """Every Lyrics element of the given verse numbers, serialised, in score order."""
    out = []
    for ly in root.iter("Lyrics"):
        no = (ly.findtext("no") or "").strip()
        if no in wanted:
            out.append(etree.tostring(ly))
    return out


def _verse1(root, staff_id, measure):
    staff = [s for s in root.find(".//Score").findall("Staff")
             if s.get("id") == str(staff_id)][0]
    m = staff.findall("Measure")[measure - 1]
    return [ly.findtext("text") for ly in m.iter("Lyrics")
            if not (ly.findtext("no") or "").strip()]


def _score_with_verses():
    root = build_score(staff_ids=(1, 2), measures=2, chords=2)
    _add_verse(root, 1, 1, 0, 1, "toi", "begin")
    _add_verse(root, 1, 1, 1, 1, "nen", "end")
    _add_verse(root, 1, 2, 0, 2, "kol")
    _add_verse(root, 2, 2, 1, 1, "toi")
    return root


def test_a_full_replace_import_keeps_verses_2_and_3():
    root = _score_with_verses()
    before = _verses(root, {"1", "2"})
    result = place_lyrics(root, [{"measure_start": 1, "lyrics": [
        {"parts": ["P1"], "text": "yk-si kak-si"},
        {"parts": ["P2"], "text": "a b c d"}]}], fmt="json", replace=True)
    assert not result.mismatches
    assert _verses(root, {"1", "2"}) == before
    assert _verse1(root, 1, 1) == ["yk", "si"]
    assert _verse1(root, 2, 2) == ["c", "d"]


def test_a_txt_import_keeps_verses_too():
    root = _score_with_verses()
    before = _verses(root, {"1", "2"})
    place_lyrics(root, "# Measure 1\n1: la la\n", fmt="txt")
    assert _verses(root, {"1", "2"}) == before
    assert _verse1(root, 1, 1) == ["la", "la"]


def _add_verse1(root, staff_id, measure, chord_index, text):
    staff = [s for s in root.find(".//Score").findall("Staff")
             if s.get("id") == str(staff_id)][0]
    chord = staff.findall("Measure")[measure - 1].findall(".//Chord")[chord_index]
    lyrics = etree.SubElement(chord, "Lyrics")
    etree.SubElement(lyrics, "syllabic").text = "single"
    etree.SubElement(lyrics, "text").text = text


def test_a_blank_verse_1_does_not_show_verse_2():
    """Once a score has verse 1, export and the editor read verse 1 only, so the next
    import cannot copy a kept verse 2 into verse 1."""
    root = _score_with_verses()
    _add_verse1(root, 2, 1, 0, "yy")
    assert "yy" in export_lyrics(root)
    assert "toi" not in export_lyrics(root)
    assert "kol" not in export_lyrics(root)
    grid = editor_grid(root).to_dict()
    assert "toi" not in str(grid["cells"]) and "kol" not in str(grid["cells"])
    verse_1 = [b["text"]["1"] for boxes in grid["boxes"].values() for b in boxes]
    assert "toi" not in str(verse_1) and "kol" not in str(verse_1)
