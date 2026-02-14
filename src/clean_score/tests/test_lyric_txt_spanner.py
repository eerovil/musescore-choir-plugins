"""
Unit test: export spanner.mscx to TXT (slur rule: only first note of slur gets a token),
assert format, then import and assert XML equals original.
"""

import json
import os
import re
import tempfile

import pytest
from lxml import etree

from src.clean_score.lyric_txt import (
    add_rests_to_empty_measures,
    export_mscx_to_txt,
    import_json_txt_into_mscx,
    import_txt_into_mscx,
    load_mscx,
    parse_json_txt,
    parse_txt,
    save_mscx,
)
from src.clean_score.lyric_txt import (
    _is_continuation_no_lyric,  # for test_cross_measure_*
    _merge_tokens,
)


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LYRIC_2_DIR = os.path.join(CURRENT_DIR, "lyric_2")
SPANNER_MSCX = os.path.join(LYRIC_2_DIR, "spanner.mscx")
SPANNER_RESULT_TXT = os.path.join(LYRIC_2_DIR, "spanner_result.txt")
SPANNER_JSON = os.path.join(LYRIC_2_DIR, "spanner.json")
SPANNER_PARTIAL_JSON = os.path.join(LYRIC_2_DIR, "spanner_partial.json")
NEW_JSON = os.path.join(LYRIC_2_DIR, "new_json.json")
NEW_JSON_CONVERTED = os.path.join(LYRIC_2_DIR, "new_json_converted.json")
NEW_JSON_MSCX = os.path.join(LYRIC_2_DIR, "new_json.mscx")
NEW_JSON_TXT = os.path.join(LYRIC_2_DIR, "new_json.txt")
MULTIMEASURE_MSCX = os.path.join(LYRIC_2_DIR, "multimeasure.mscx")

EXPECTED_TXT_1 = "tä-mä tes-ti lau-"
EXPECTED_TXT_2 = "lu on! O-ma-ni!"

# Multimeasure: 4 staves, measure 1 ends with his-to-ri-, measure 2 starts with aan!
MULTIMEASURE_M1 = "his-to-ri-"
MULTIMEASURE_M2_STAFF_1_3 = "aan!"
MULTIMEASURE_M2_STAFF_4 = "aan! _ _ _"
MULTIMEASURE_M3_STAFF_4 = "aan!"


def test_export_spanner_matches_result_file():
    """spanner.mscx must export exactly to spanner_result.txt."""
    with open(SPANNER_RESULT_TXT, "r", encoding="utf-8") as f:
        expected = f.read().strip()
    root = load_mscx(SPANNER_MSCX)
    txt = export_mscx_to_txt(root).strip()
    assert txt == expected, f"Export must match {SPANNER_RESULT_TXT}. Got:\n{txt}"


def test_new_json_converts_to_legacy_format():
    """new_json.json (measure_start + lyrics with parts) must convert to new_json_converted.json (measure_start + part keys)."""
    with open(NEW_JSON, "r", encoding="utf-8") as f:
        new_content = f.read()
    with open(NEW_JSON_CONVERTED, "r", encoding="utf-8") as f:
        expected = json.load(f)
    converted = parse_json_txt(new_content)
    assert converted == expected, (
        f"Conversion of new_json.json must match new_json_converted.json. Got: {converted}"
    )


def _mscx_has_end_man_followed_by_begin_vi(root: etree._Element) -> bool:
    """
    Return True if the score XML contains the wrong pattern: a Chord with Lyrics
    (syllabic=end, text=man) immediately followed by a Chord with Lyrics (syllabic=begin, text=vi).
    That indicates the distribution bug (il-man-vi- in one measure).
    """
    for chord in root.iter("Chord"):
        lyrics = chord.find("Lyrics")
        if lyrics is None:
            continue
        no_el = lyrics.find("no")
        if no_el is not None and (no_el.text or "").strip() == "1":
            continue  # verse 2
        syllabic = lyrics.find("syllabic")
        text_el = lyrics.find("text")
        if syllabic is None or text_el is None:
            continue
        if (syllabic.text or "").strip() != "end" or (text_el.text or "").strip() != "man":
            continue
        # This chord has (end, man). Find next Chord in same voice.
        parent = chord.getparent()
        if parent is None or parent.tag != "voice":
            continue
        siblings = list(parent)
        try:
            idx = siblings.index(chord)
        except ValueError:
            continue
        for i in range(idx + 1, len(siblings)):
            el = siblings[i]
            if el.tag != "Chord":
                continue
            next_lyrics = el.find("Lyrics")
            if next_lyrics is None:
                continue
            s2 = next_lyrics.find("syllabic")
            t2 = next_lyrics.find("text")
            if s2 is not None and t2 is not None:
                if (s2.text or "").strip() == "begin" and (t2.text or "").strip() == "vi":
                    return True
            break
    return False


def test_new_json_import_export_measure_14_matches_expected():
    """
    Import new_json.json into new_json.mscx then export: # Measure 14 must match new_json.txt
    (all four staves with "il-man il-ki-rii-vi-").
    """
    with open(NEW_JSON, "r", encoding="utf-8") as f:
        json_content = f.read()
    with open(NEW_JSON_TXT, "r", encoding="utf-8") as f:
        expected_full = f.read()
    expected_lines = expected_full.strip().splitlines()
    # Extract "# Measure 14" block from expected (up to next # Measure or end)
    m14_start = next((i for i, ln in enumerate(expected_lines) if ln.strip() == "# Measure 14"), None)
    assert m14_start is not None, f"Expected {NEW_JSON_TXT} to contain '# Measure 14'"
    m14_end = next(
        (i for i in range(m14_start + 1, len(expected_lines)) if expected_lines[i].strip().startswith("# Measure")),
        len(expected_lines),
    )
    expected_m14 = "\n".join(expected_lines[m14_start:m14_end])

    root = load_mscx(NEW_JSON_MSCX)
    import_json_txt_into_mscx(root, json_content)
    txt = export_mscx_to_txt(root)
    got_lines = txt.strip().splitlines()
    got_start = next((i for i, ln in enumerate(got_lines) if ln.strip() == "# Measure 14"), None)
    assert got_start is not None, f"Export should contain '# Measure 14'. Got export (first 30 lines):\n" + "\n".join(got_lines[:30])
    got_end = next(
        (i for i in range(got_start + 1, len(got_lines)) if got_lines[i].strip().startswith("# Measure")),
        len(got_lines),
    )
    got_m14 = "\n".join(got_lines[got_start:got_end]).strip()
    expected_m14 = expected_m14.strip()

    assert got_m14 == expected_m14, (
        f"Measure 14 after import+export must match {NEW_JSON_TXT}. Expected:\n{expected_m14}\n\nGot:\n{got_m14}"
    )


def test_new_json_import_measure_14_not_wrong_syllables():
    """
    Import new_json.json into new_json.mscx: measure 14 must not show 'il-man-vi-' for staff 1/2/3.
    (Regression: line 'si il-man tuot-ta ... il-man il-ki-rii-vi-' was wrongly crammed into m14 as 'il-man-vi-'.)
    The score must not contain the XML pattern: Lyrics (end, man) immediately followed by Lyrics (begin, vi).
    Measure 15 must show 'öt-tä.' for staff 1.
    """
    with open(NEW_JSON, "r", encoding="utf-8") as f:
        json_content = f.read()
    root = load_mscx(NEW_JSON_MSCX)
    import_json_txt_into_mscx(root, json_content)
    # Must not have (end, man) followed by (begin, vi) in the XML
    assert not _mscx_has_end_man_followed_by_begin_vi(root), (
        "Score must not contain Lyrics (end, man) followed by Lyrics (begin, vi) (distribution bug)."
    )
    txt = export_mscx_to_txt(root)
    blocks = parse_txt(txt)
    by_measure = {b["measure"]: b["staff_lines"] for b in blocks}
    # Measure 14 must not show the wrong chunk 'il-man-vi-' (syllables from wrong offset)
    wrong_m14 = "il-man-vi-"
    for staff_id in (1, 2, 3):
        if 14 in by_measure and staff_id in by_measure[14]:
            merged = _merge_tokens(by_measure[14][staff_id])
            assert merged != wrong_m14, (
                f"Measure 14 staff {staff_id} must not be '{wrong_m14}' (distribution bug). Got: {merged!r}"
            )
    # Measure 15 must have 'öt-tä.' (or export form 'öt tä.') for staff 1
    assert 15 in by_measure, "Export should have measure 15"
    assert 1 in by_measure[15], "Export should have staff 1 in measure 15"
    merged_15 = _merge_tokens(by_measure[15][1])
    assert "öt" in merged_15 and "tä" in merged_15, (
        f"Measure 15 staff 1 should contain öt-tä. Got: {merged_15!r}"
    )


def test_spanner_json_import_matches_original_export():
    """Import spanner.json into spanner.mscx then export: must match spanner_result.txt (same as original)."""
    with open(SPANNER_RESULT_TXT, "r", encoding="utf-8") as f:
        expected = f.read().strip()
    with open(SPANNER_JSON, "r", encoding="utf-8") as f:
        json_content = f.read()
    root = load_mscx(SPANNER_MSCX)
    import_json_txt_into_mscx(root, json_content)
    txt = export_mscx_to_txt(root).strip()
    assert txt == expected, f"Import spanner.json then export must match {SPANNER_RESULT_TXT}. Got:\n{txt}"


def test_spanner_partial_json_only_edits_from_first_measure():
    """Partial JSON (measure_start=2 only): measure 1 must be unchanged, only measure 2 updated."""
    with open(SPANNER_PARTIAL_JSON, "r", encoding="utf-8") as f:
        json_content = f.read()
    root = load_mscx(SPANNER_MSCX)
    import_json_txt_into_mscx(root, json_content)
    txt = export_mscx_to_txt(root)
    lines = [ln.strip() for ln in txt.strip().splitlines()]
    assert "# Measure 1" in lines and "# Measure 2" in lines
    m1_line = m2_line = None
    for i in range(1, len(lines)):
        if lines[i].startswith("1 [") and lines[i - 1] == "# Measure 1":
            m1_line = lines[i]
        if lines[i].startswith("1 [") and lines[i - 1] == "# Measure 2":
            m2_line = lines[i]
    assert m1_line is not None, "Export should have staff line for measure 1"
    assert m2_line is not None, "Export should have staff line for measure 2"
    assert "tä-mä tes-ti lau-" in m1_line, f"Measure 1 must be unchanged (tä-mä tes-ti lau-). Got: {m1_line}"
    assert "lu on! O-ma-ni!" in m2_line, f"Measure 2 must have partial JSON text. Got: {m2_line}"


def test_export_spanner_has_measure1_and_expected_line():
    root = load_mscx(SPANNER_MSCX)
    txt = export_mscx_to_txt(root)
    lines = [ln.strip() for ln in txt.strip().splitlines()]
    assert "# Measure 1" in lines, f"Expected '# Measure 1' in export. Got:\n{txt}"
    assert "# Measure 2" in lines, f"Expected '# Measure 2' in export. Got:\n{txt}"
    data_lines = [l.strip() for l in txt.strip().splitlines() if l.strip() and not l.strip().startswith("#")]
    assert len(data_lines) == 2, f"Expected 2 staff lines. Got:\n{txt}"
    for i, line in enumerate(data_lines):
        assert re.match(r"^1\s*\[\d+\]\s*:", line), f"Staff line format. Got: {line}"
    blocks = parse_txt(txt)
    assert len(blocks) == 2 and blocks[0]["measure"] == 1 and blocks[1]["measure"] == 2


def test_import_roundtrip_ineligible_cleared():
    """Export -> import: verse 1 lyrics on ineligible chords (inside spanner) are cleared."""
    root_orig = load_mscx(SPANNER_MSCX)
    txt = export_mscx_to_txt(root_orig)
    assert "# Measure 1" in txt and "# Measure 2" in txt

    root_copy = load_mscx(SPANNER_MSCX)
    import_txt_into_mscx(root_copy, txt)

    # No chord that is inside spanner (continuation) may have verse 1 lyrics after import
    score = root_copy if root_copy.tag == "Score" else root_copy.find(".//Score")
    assert score is not None
    for chord in score.findall(".//Chord"):
        if not _is_continuation_no_lyric(chord):
            continue
        for lyrics in chord.findall(".//Lyrics"):
            no_el = lyrics.find("no")
            if (no_el is None or (no_el.text or "").strip() in ("", "1")):
                raise AssertionError(
                    "Chord that is inside spanner (continuation) must not have verse 1 lyrics after import"
                )


def test_roundtrip_no_hups_in_result():
    """Export spanner.mscx -> TXT -> import into copy; result must contain no lyric text 'hups'."""
    txt = export_mscx_to_txt(load_mscx(SPANNER_MSCX))
    root = load_mscx(SPANNER_MSCX)
    import_txt_into_mscx(root, txt)
    score = root if root.tag == "Score" else root.find(".//Score")
    assert score is not None
    for lyrics in score.findall(".//Lyrics"):
        t_el = lyrics.find("text")
        t = (t_el.text or "").strip() if t_el is not None else ""
        assert t != "hups", f"Round-trip result must not contain 'hups'; found in Lyrics (no={lyrics.find('no').text if lyrics.find('no') is not None else '?'})"


def test_cross_measure_syllabic_continuation():
    """
    When measure N ends with a trailing hyphen (e.g. 'lau-'), the first syllable
    of measure N+1 must be imported as syllabic 'end' (e.g. 'lu').
    """
    txt = f"# Measure 1\n1: {EXPECTED_TXT_1}\n# Measure 2\n1: {EXPECTED_TXT_2}"
    root = load_mscx(SPANNER_MSCX)
    import_txt_into_mscx(root, txt)
    score = root if root.tag == "Score" else root.find(".//Score")
    assert score is not None
    # Measures are under Score > Staff (the one that has Measure children), not under Part > Staff
    measures = score.findall(".//Measure")
    assert len(measures) >= 2, "need at least two measures"
    measure2 = measures[1]
    voices = measure2.findall("voice")
    assert voices, "measure 2 has no voice"
    voice = voices[0]
    # First lyric-eligible chord in measure 2 should be "lu" with syllabic "end"
    first_lyric_chord = None
    for el in voice:
        if el.tag != "Chord":
            continue
        if _is_continuation_no_lyric(el):
            continue
        first_lyric_chord = el
        break
    assert first_lyric_chord is not None, "measure 2 should have at least one lyric-eligible chord"
    lyrics_el = first_lyric_chord.find(".//Lyrics")
    assert lyrics_el is not None, "first chord of measure 2 should have Lyrics"
    syllabic_el = lyrics_el.find("syllabic")
    text_el = lyrics_el.find("text")
    syllabic = (syllabic_el.text or "").strip() if syllabic_el is not None else ""
    text = (text_el.text or "").strip() if text_el is not None else ""
    assert syllabic == "end", (
        f"First syllable of measure 2 must be 'end' (cross-measure continuation), got syllabic={syllabic!r} text={text!r}"
    )
    assert text == "lu", f"Expected text 'lu', got {text!r}"


def test_parse_txt():
    blocks = parse_txt("# Measure 1\n1: il-man kuu-ta ja")
    assert len(blocks) == 1
    assert blocks[0]["measure"] == 1
    assert blocks[0]["staff_lines"][1] == ["il-man", "kuu-ta", "ja"]


def test_parse_txt_accepts_syllable_count():
    """Optional [syllable_count] is stripped; tokens are unchanged."""
    blocks = parse_txt("# Measure 1\n1 [3]: il-man kuu-ta ja")
    assert len(blocks) == 1
    assert blocks[0]["staff_lines"][1] == ["il-man", "kuu-ta", "ja"]


# --- multimeasure.mscx (4 staves, 3 measures, cross-measure his-to-ri- / aan!) ---


def test_export_multimeasure_has_expected_structure():
    """Export multimeasure.mscx and assert measure 1 ends with his-to-ri-, measure 2 has aan!."""
    root = load_mscx(MULTIMEASURE_MSCX)
    txt = export_mscx_to_txt(root)
    lines = [ln.rstrip() for ln in txt.strip().splitlines()]
    assert "# Measure 1" in lines and "# Measure 2" in lines and "# Measure 3" in lines
    # Format: staffNum [syllable_count]: tokens. Staff 1,2,4 have his-to-ri- in M1; staff 3 has _ to-ri-
    for sid in (1, 2, 4):
        assert any(
            re.match(rf"^{re.escape(str(sid))}\s*\[\d+\]\s*: " + re.escape(MULTIMEASURE_M1) + r"$", line)
            for line in lines
        ), f"Expected line '{sid} [N]: {MULTIMEASURE_M1}' in:\n{txt}"
    assert any(re.match(r"^3\s*\[\d+\]\s*: _ to-ri-$", line) for line in lines), f"Expected '3 [N]: _ to-ri-' in:\n{txt}"
    assert any(re.match(r"^1\s*\[\d+\]\s*: " + re.escape(MULTIMEASURE_M2_STAFF_1_3) + r"$", line) for line in lines), f"Expected '1 [N]: aan!' in:\n{txt}"
    assert any(re.match(r"^4\s*\[\d+\]\s*: " + re.escape(MULTIMEASURE_M2_STAFF_4) + r"$", line) for line in lines), f"Expected '4 [N]: aan! _ _ _' in:\n{txt}"
    assert any(re.match(r"^4\s*\[\d+\]\s*: " + re.escape(MULTIMEASURE_M3_STAFF_4) + r"$", line) for line in lines), f"Expected '4 [N]: aan!' in measure 3 in:\n{txt}"


def test_multimeasure_cross_measure_syllabic_continuation():
    """
    multimeasure.mscx: measure 1 ends with 'his-to-ri-' (last syllable must be begin/middle, not end);
    first syllable of measure 2 must be 'end' ('aan!') so the word continues across the bar.
    """
    root = load_mscx(MULTIMEASURE_MSCX)
    txt = export_mscx_to_txt(root)
    root2 = load_mscx(MULTIMEASURE_MSCX)
    import_txt_into_mscx(root2, txt)
    score = root2 if root2.tag == "Score" else root2.find(".//Score")
    assert score is not None
    measures = score.findall(".//Measure")
    assert len(measures) >= 2
    measure1 = measures[0]
    measure2 = measures[1]
    # Staff 1: last syllable of measure 1 must be begin or middle (so we export "ri-" and get continuation)
    voices1 = measure1.findall("voice")
    assert voices1
    last_lyric_chord_m1 = None
    for el in voices1[0]:
        if el.tag != "Chord":
            continue
        if _is_continuation_no_lyric(el):
            continue
        if el.find(".//Lyrics") is not None:
            last_lyric_chord_m1 = el
    assert last_lyric_chord_m1 is not None, "measure 1 (staff 1) should have lyric chords"
    last_lyric = last_lyric_chord_m1.find(".//Lyrics")
    assert last_lyric is not None
    last_s = (last_lyric.find("syllabic").text or "").strip() if last_lyric.find("syllabic") is not None else ""
    last_t = (last_lyric.find("text").text or "").strip() if last_lyric.find("text") is not None else ""
    assert last_s in ("begin", "middle"), (
        f"Last syllable of measure 1 must be begin/middle (continuation), got syllabic={last_s!r} text={last_t!r}"
    )
    assert last_t == "ri", f"Expected last syllable of measure 1 to be 'ri', got {last_t!r}"
    # First syllable of measure 2 must be 'end' ('aan!')
    voices2 = measure2.findall("voice")
    assert voices2
    voice = voices2[0]
    first_lyric_chord = None
    for el in voice:
        if el.tag != "Chord":
            continue
        if _is_continuation_no_lyric(el):
            continue
        first_lyric_chord = el
        break
    assert first_lyric_chord is not None, "measure 2 (staff 1) should have one lyric-eligible chord"
    lyrics_el = first_lyric_chord.find(".//Lyrics")
    assert lyrics_el is not None
    syllabic_el = lyrics_el.find("syllabic")
    text_el = lyrics_el.find("text")
    syllabic = (syllabic_el.text or "").strip() if syllabic_el is not None else ""
    text = (text_el.text or "").strip() if text_el is not None else ""
    assert syllabic == "end", (
        f"First syllable of measure 2 must be 'end' (cross-measure from his-to-ri-), got syllabic={syllabic!r} text={text!r}"
    )
    assert text == "aan!", f"Expected text 'aan!', got {text!r}"


def test_add_rests_to_empty_measure_multimeasure():
    """
    multimeasure.mscx has one measure with no voice (fully empty).
    add_rests_to_empty_measures must add a voice with a full-measure rest so export/import work.
    """
    root = load_mscx(MULTIMEASURE_MSCX)
    score = root if root.tag == "Score" else root.find(".//Score")
    assert score is not None
    empty_measure = None
    for staff in score.findall(".//Staff"):
        for measure in staff.findall(".//Measure"):
            if len(measure.findall("voice")) == 0:
                empty_measure = measure
                break
        if empty_measure is not None:
            break
    assert empty_measure is not None, "multimeasure.mscx must contain one measure with no voice"

    add_rests_to_empty_measures(root)

    voices = empty_measure.findall("voice")
    assert len(voices) == 1, "Empty measure must get exactly one voice"
    voice = voices[0]
    rest_el = voice.find("Rest")
    assert rest_el is not None, "Voice must contain a Rest"
    dt = rest_el.find("durationType")
    assert dt is not None and (dt.text or "").strip() in ("4/4", "3/4", "2/4"), (
        f"Rest must have durationType for full measure, got {dt.text!r}"
    )
    chords = voice.findall("Chord")
    assert len(chords) == 0, "Voice must contain only the rest, no chords"
