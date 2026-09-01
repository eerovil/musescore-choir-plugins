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
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def test_a_score_whose_bars_all_declare_their_own_length_is_counted_not_listed(tmp_path):
    """Mixed or free meter: the length is per bar by construction.

    One score here overrides 20 of 25 bars. Listing those one by one would flag most
    of the piece for being what it is, so they are collapsed into a single line.
    """
    bars = [[8]] + [[9, 9]] * 4
    lens = [None] + ["9/8"] * 4                     # 4 of 5 bars overridden
    path = _score(tmp_path, bars, lens=lens)
    assert not _kinds(path, "unprinted-meter")      # not one per bar
    assert len(_kinds(path, "meter-collapsed")) == 1

    # The same shape with only one override is an ordinary score, and is listed.
    bars = [[8]] + [[8]] * 3 + [[9, 9]]
    lens = [None, None, None, None, "9/8"]
    path = _score(tmp_path, bars, lens=lens)
    assert _kinds(path, "unprinted-meter")
    assert not _kinds(path, "meter-collapsed")


def test_a_score_cannot_buy_silence_by_being_worse(tmp_path):
    """The B6 defect, in the shape it was found in.

    The share used to switch the meter rule off, and "carries a length override" is
    what a badly parsed score looks like as much as a free-metered one. On the
    benchmark's roughest page, misreading one more bar wrote eight more overrides,
    carried the share from 50% to 56%, and took the report from 32 findings to 3.

    Here: the same four bars disagree with the printed meter on either side of the
    line. Crossing it must not lose them.
    """
    bars = [[8]] + [[9, 9]] * 4
    below = _score(tmp_path / "below", [[8]] * 4 + bars[1:],
                   lens=[None] * 4 + ["9/8"] * 4)   # 4 of 8 bars overridden
    above = _score(tmp_path / "above", bars, lens=[None] + ["9/8"] * 4)

    listed = _kinds(below, "unprinted-meter")
    counted = _kinds(above, "meter-collapsed")
    assert len(listed) == 4                          # under the line: one per bar
    assert len(counted) == 1                         # over it: one line...
    assert "4 bar(s)" in counted[0]["detail"]        # ...carrying the same 4
    assert "56%" not in counted[0]["detail"] and "80%" in counted[0]["detail"]


def test_the_collapsed_line_points_at_the_first_bar_to_look_at(tmp_path):
    """It is a summary, not a verdict — the answer is in the score, so say where."""
    bars = [[8], [8]] + [[9, 9]] * 3
    path = _score(tmp_path, bars, lens=[None, None] + ["9/8"] * 3)
    found = _kinds(path, "meter-collapsed")[0]
    assert found["measure"] == 3
    assert "m3" in found["detail"]


def test_a_free_metered_score_that_agrees_with_itself_stays_clean(tmp_path):
    """Nothing to count is nothing to say. Describing a score is not accusing it."""
    # Every bar overridden, and every bar sits at the length its override declares.
    bars = [[8]] * 5
    path = _score(tmp_path, bars, lens=["4/4"] * 5)
    assert not health.scan(path)


def test_a_worse_score_reopens_a_dismissed_summary(tmp_path):
    """Dismissing "4 bars disagree" must not silently cover "6 bars disagree"."""
    four = _kinds(_score(tmp_path / "a", [[8]] + [[9, 9]] * 4,
                         lens=[None] + ["9/8"] * 4), "meter-collapsed")[0]
    six = _kinds(_score(tmp_path / "b", [[8]] + [[9, 9]] * 6,
                        lens=[None] + ["9/8"] * 6), "meter-collapsed")[0]
    assert four["id"] != six["id"]
    dismissed = [dict(four, status="dismissed")]
    assert health.merge_issues([six], dismissed)[0]["status"] == "open"


def test_the_fixture_is_clean(tmp_path):
    """m26 is repaired -- two paddings stripped automatically, the dot by a
    recorded fix -- so neither check has anything to say about it."""
    import os
    fixture = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))),
        "fixtures", "virta-venhetta-vie", "20-lyrics",
        "Virta-venhetta-vie_cleaned.mscx")
    if not os.path.exists(fixture):
        pytest.skip("prototyping fixture not present")
    assert not _kinds(fixture, "unprinted-meter")
    assert not _kinds(fixture, "malformed-measure")


def test_the_count_a_person_reads_does_not_collapse_with_the_rows(tmp_path):
    """Crossing the line must change the presentation and not the magnitude.

    This is the defect one level up from the rule itself. Collapsing 32 findings
    into one row and then counting rows puts B6 back where it was — "4 open
    issue(s)" against a per-system parse's 28 — which is exactly the comparison
    that nearly cost a real decision. Every count the app shows reads
    `finding_count`, so a collapsed row weighs what it stands for.
    """
    # One voice a bar, so nothing but the meter rule has anything to say.
    bars = [[8]] + [[9]] * 4                         # 4 bars disagree, either way
    below = _score(tmp_path / "below", [[8]] * 4 + bars[1:],
                   lens=[None] * 4 + ["9/8"] * 4)    # 4 of 8 -> listed
    above = _score(tmp_path / "above", bars, lens=[None] + ["9/8"] * 4)  # 4 of 5

    listed, counted = health.scan(below), health.scan(above)
    assert len(listed) == 4 and len(counted) == 1    # rows do collapse
    assert health.finding_count(listed) == health.finding_count(counted) == 4

    # And the magnitude is a number on the row, not prose inside its sentence.
    assert counted[0]["collapsed"] == 4
    assert counted[0]["collapsed_bars"] == 4


def test_the_verification_summary_reports_the_underlying_count(tmp_path, monkeypatch):
    """The summary is the surface two scans get compared on, so it has to add up."""
    from src.song_app import state, verification

    monkeypatch.setattr(state, "SONGS_DIR", str(tmp_path / "songs"))
    song = state.create("Meter Cliff", per_system=False)
    cleaned = song.path("meter_cliff_cleaned.mscx")
    with open(cleaned, "w") as fh:
        fh.write("<museScore><Score/></museScore>")
    song.data["cleaned"] = "meter_cliff_cleaned.mscx"

    found = health.scan(_score(tmp_path / "above", [[8]] + [[9]] * 4,
                               lens=[None] + ["9/8"] * 4))
    song.data["health"] = {
        "checked_against": state.file_fingerprint(cleaned),
        "issues": health.merge_issues(found, []),
    }
    song.save()

    result = verification.summary(song, systems=0)["health"]
    assert result["open_count"] == 4                 # not 1
    assert result["row_count"] == 1
    assert result["collapsed_count"] == 4
    assert "4 open issue(s)" in result["detail"]
    assert "shown as 1 line(s)" in result["detail"]
    assert result["status"] == "warning"

    # The library badge is the other place two songs are compared by a number.
    assert song.to_summary()["open_issues"] == 4
