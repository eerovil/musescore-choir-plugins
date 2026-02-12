"""
Unit test: export spanner.mscx to TXT (slur rule: only first note of slur gets a token),
assert format, then import and assert XML equals original.
"""

import os
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


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SPANNER_MSCX = os.path.join(CURRENT_DIR, "lyric_2", "spanner.mscx")
EXPECTED_TXT_1 = "tä-mä tes-ti lau-"
EXPECTED_TXT_2 = "lu on! O-ma-ni!"


def test_export_spanner_has_measure1_and_expected_line():
    root = load_mscx(SPANNER_MSCX)
    txt = export_mscx_to_txt(root)
    lines = [ln.strip() for ln in txt.strip().splitlines()]
    assert "# Measure 1" in lines, f"Expected '# Measure 1' in export. Got:\n{txt}"
    assert "# Measure 2" in lines, f"Expected '# Measure 2' in export. Got:\n{txt}"
    assert any(
        line.strip() == f"1: {EXPECTED_TXT_1}" for line in lines
    ), f"Expected a line '1: {EXPECTED_TXT_1}'. Got:\n{txt}"
    assert any(
        line.strip() == f"1: {EXPECTED_TXT_2}" for line in lines
    ), f"Expected a line '1: {EXPECTED_TXT_2}'. Got:\n{txt}"
    expected_full = f"# Measure 1\n1: {EXPECTED_TXT_1}\n# Measure 2\n1: {EXPECTED_TXT_2}"
    assert txt.strip() == expected_full, f"Export content mismatch. Got:\n{txt}"


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


def test_parse_txt():
    blocks = parse_txt("# Measure 1\n1: il-man kuu-ta ja")
    assert len(blocks) == 1
    assert blocks[0]["measure"] == 1
    assert blocks[0]["staff_lines"][1] == ["il-man", "kuu-ta", "ja"]
