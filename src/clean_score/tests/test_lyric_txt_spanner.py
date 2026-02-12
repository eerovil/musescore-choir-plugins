"""
Unit test: export spanner.mscx to TXT (slur rule: only first note of slur gets a token),
assert format, then import and assert XML equals original.
"""

import os
import re
import tempfile

import pytest
from lxml import etree

from src.clean_score.lyric_txt import (
    export_mscx_to_txt,
    import_txt_into_mscx,
    load_mscx,
    parse_txt,
    save_mscx,
)
from src.clean_score.lyric_txt import _is_continuation_no_lyric  # for test_cross_measure_*


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
LYRIC_2_DIR = os.path.join(CURRENT_DIR, "lyric_2")
SPANNER_MSCX = os.path.join(LYRIC_2_DIR, "spanner.mscx")
MULTIMEASURE_MSCX = os.path.join(LYRIC_2_DIR, "multimeasure.mscx")
EXPECTED_TXT_1 = "tä-mä tes-ti lau-"
EXPECTED_TXT_2 = "lu on! O-ma-ni!"

# Multimeasure: 4 staves, measure 1 ends with his-to-ri-, measure 2 starts with aan!
MULTIMEASURE_M1 = "his-to-ri-"
MULTIMEASURE_M2_STAFF_1_3 = "aan!"
MULTIMEASURE_M2_STAFF_4 = "aan! _ _ _"
MULTIMEASURE_M3_STAFF_4 = "aan!"


def test_export_spanner_has_measure1_and_expected_line():
    root = load_mscx(SPANNER_MSCX)
    txt = export_mscx_to_txt(root)
    lines = [ln.strip() for ln in txt.strip().splitlines()]
    assert "# Measure 1" in lines, f"Expected '# Measure 1' in export. Got:\n{txt}"
    assert "# Measure 2" in lines, f"Expected '# Measure 2' in export. Got:\n{txt}"
    # Format: staffNum [syllable_count]: tokens (count = lyric slots in measure)
    assert any(
        line.strip().startswith("1 [") and line.strip().endswith(f": {EXPECTED_TXT_1}") for line in lines
    ), f"Expected a line '1 [N]: {EXPECTED_TXT_1}'. Got:\n{txt}"
    assert any(
        line.strip().startswith("1 [") and line.strip().endswith(f": {EXPECTED_TXT_2}") for line in lines
    ), f"Expected a line '1 [N]: {EXPECTED_TXT_2}'. Got:\n{txt}"
    # Exact format: two measure blocks with one staff line each
    data_lines = [l.strip() for l in txt.strip().splitlines() if l.strip() and not l.strip().startswith("#")]
    assert len(data_lines) == 2, f"Expected 2 staff lines. Got:\n{txt}"
    assert data_lines[0].endswith(f": {EXPECTED_TXT_1}") and re.match(r"^1\s*\[\d+\]\s*:", data_lines[0]), f"Measure 1 line format. Got:\n{txt}"
    assert data_lines[1].endswith(f": {EXPECTED_TXT_2}") and re.match(r"^1\s*\[\d+\]\s*:", data_lines[1]), f"Measure 2 line format. Got:\n{txt}"


def test_import_roundtrip_xml_unchanged():
    root_orig = load_mscx(SPANNER_MSCX)
    txt = export_mscx_to_txt(root_orig)
    assert "# Measure 1" in txt and EXPECTED_TXT_1 in txt
    assert "# Measure 2" in txt and EXPECTED_TXT_2 in txt

    # Import into a copy
    root_copy = load_mscx(SPANNER_MSCX)
    import_txt_into_mscx(root_copy, txt)

    # Compare semantics: lyrics (verse 1) and slur presence per chord, in order
    def chord_semantics(root: etree._Element) -> list:
        score = root if root.tag == "Score" else root.find(".//Score")
        if score is None:
            return []
        out = []
        for staff in score.findall(".//Staff"):
            for measure in staff.findall(".//Measure"):
                voices = measure.findall("voice")
                if not voices:
                    continue
                for el in voices[0]:
                    if el.tag != "Chord":
                        continue
                    slur_prev = el.find(".//Spanner[@type='Slur']//prev") is not None
                    lyric_el = None
                    for ly in el.findall(".//Lyrics"):
                        no_el = ly.find("no")
                        if (no_el is None or (no_el.text or "").strip() in ("", "1")):
                            lyric_el = ly
                            break
                    if lyric_el is not None:
                        s_el = lyric_el.find("syllabic")
                        t_el = lyric_el.find("text")
                        s = (s_el.text or "").strip() if s_el is not None else ""
                        if s == "":
                            s = "single"  # normalize: MuseScore may omit for single syllable
                        t = (t_el.text or "").strip() if t_el is not None else ""
                        out.append(("lyric", s, t, slur_prev))
                    else:
                        out.append(("no_lyric", None, None, slur_prev))
        return out

    orig_sem = chord_semantics(root_orig)
    copy_sem = chord_semantics(root_copy)
    assert orig_sem == copy_sem, (
        f"Lyrics and slur structure should match after import. "
        f"Orig: {orig_sem}, Copy: {copy_sem}"
    )


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
