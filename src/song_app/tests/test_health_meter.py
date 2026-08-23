"""The engraving is the only authority on meter.

This rule exists because of a specific failure: a repair pass "fixed" a 4/4 bar by
padding every voice to 9/8, and *every check passed*. The bar was internally
consistent, so no voice was malformed; the lyrics still fitted, so the syllable
arithmetic was happy. Nothing here compares the score against the page, so nothing
could object to a meter that was never printed. This is that objection.
"""
from fractions import Fraction

import pytest
from lxml import etree

from src.song_app import health


def _score(tmp_path, measures, sig=(4, 4), lens=None):
    """`measures` is a list of lists of voice lengths, in eighths.

    `lens` optionally gives each measure a `len` override, which is how MuseScore
    records an irregular bar — and how the OCR (and one repair pass) made a bar
    self-consistent at a length the engraving never printed.
    """
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "trackName").text = "T1"
    etree.SubElement(part, "Staff", id="1")
    staff = etree.SubElement(score, "Staff", id="1")
    for mi, voices in enumerate(measures):
        measure = etree.SubElement(staff, "Measure")
        if lens and lens[mi]:
            measure.set("len", lens[mi])
        for eighths in voices:
            v = etree.SubElement(measure, "voice")
            if mi == 0 and sig:
                ts = etree.SubElement(v, "TimeSig")
                etree.SubElement(ts, "sigN").text = str(sig[0])
                etree.SubElement(ts, "sigD").text = str(sig[1])
            for _ in range(eighths):
                c = etree.SubElement(v, "Chord")
                etree.SubElement(c, "durationType").text = "eighth"
                etree.SubElement(etree.SubElement(c, "Note"), "pitch").text = "60"
    path = tmp_path / "s.mscx"
    etree.ElementTree(root).write(str(path), encoding="UTF-8", xml_declaration=True)
    return str(path)


def _kinds(path, kind):
    return [i for i in health.scan(path) if i["kind"] == kind]


def test_a_bar_at_a_meter_nobody_printed_is_flagged(tmp_path):
    """The exact shape of the repair that slipped through: consistent, and wrong."""
    # A len override is what makes it self-consistent: nominal becomes 9/8, so
    # no voice is malformed and every existing check is satisfied.
    path = _score(tmp_path, [[8], [9, 9]], lens=[None, "9/8"])
    found = _kinds(path, "unprinted-meter")
    assert len(found) == 1
    assert found[0]["measure"] == 2
    assert "9/8" in found[0]["detail"] and "1" in found[0]["detail"]
    assert not _kinds(path, "malformed-measure")     # nothing else objects


def test_a_printed_signature_authorises_it(tmp_path):
    """Same bar, but the engraving says so."""
    path = _score(tmp_path, [[8], [9, 9]], sig=(9, 8), lens=[None, "9/8"])
    assert not _kinds(path, "unprinted-meter")


def test_the_first_bar_is_exempt(tmp_path):
    """An anacrusis is a real engraving feature that prints no signature."""
    path = _score(tmp_path, [[2], [8]], lens=["1/4", None])
    assert not _kinds(path, "unprinted-meter")


def test_an_uneven_bar_is_left_to_the_malformed_check(tmp_path):
    """Two complaints about one bar is noise; the specific one wins."""
    path = _score(tmp_path, [[8], [8, 9]])          # voices disagree
    assert _kinds(path, "malformed-measure")
    assert not _kinds(path, "unprinted-meter")


def test_music_printed_without_a_meter_is_not_judged(tmp_path):
    """An oversized nominal is MuseScore carrying unmetered music, not a meter.

    One score here declares 16/2 — eight whole notes — and gives each phrase its
    own length. There is nothing for a bar to disagree with.
    """
    path = _score(tmp_path, [[8], [9, 9]], sig=(16, 2), lens=[None, "9/8"])
    assert not _kinds(path, "unprinted-meter")


def test_a_score_whose_bars_all_declare_their_own_length_is_not_judged(tmp_path):
    """Mixed or free meter: the length is per bar by construction.

    One score here overrides 20 of 25 bars. Judging those against a carried-forward
    signature would flag most of the piece for being what it is.
    """
    bars = [[8]] + [[9, 9]] * 4
    lens = [None] + ["9/8"] * 4                     # 4 of 5 bars overridden
    assert not _kinds(_score(tmp_path, bars, lens=lens), "unprinted-meter")

    # The same shape with only one override is an ordinary score, and is judged.
    bars = [[8]] + [[8]] * 3 + [[9, 9]]
    lens = [None, None, None, None, "9/8"]
    assert _kinds(_score(tmp_path, bars, lens=lens), "unprinted-meter")


def test_the_fixture_is_not_flagged(tmp_path):
    """m26 is uneven, so it is reported as malformed and not twice over."""
    import os
    fixture = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))),
        "fixtures", "virta-venhetta-vie", "20-lyrics",
        "Virta-venhetta-vie_cleaned.mscx")
    if not os.path.exists(fixture):
        pytest.skip("prototyping fixture not present")
    assert not _kinds(fixture, "unprinted-meter")
    assert len(_kinds(fixture, "malformed-measure")) == 1
