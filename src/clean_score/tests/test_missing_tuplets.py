"""
Tests for the missing-tuplet auto-fix: mirror a dropped tuplet bracket from a
parallel voice (any staff, same measure) onto the malformed voice.
"""

from fractions import Fraction

from lxml import etree

from src.clean_score.utils.missing_tuplets import fix_missing_tuplets

_B = {"whole": Fraction(1), "half": Fraction(1, 2), "quarter": Fraction(1, 4),
      "eighth": Fraction(1, 8), "16th": Fraction(1, 16)}


def _ticks(voice):
    pos = Fraction(0)
    scale = Fraction(1)
    for el in voice:
        if el.tag == "Tuplet":
            scale = Fraction(int(el.findtext("normalNotes")), int(el.findtext("actualNotes")))
        elif el.tag == "endTuplet":
            scale = Fraction(1)
        elif el.tag in ("Chord", "Rest"):
            pos += _B.get(el.findtext("durationType"), Fraction(0)) * scale
    return pos


def _chord(pitch, dur):
    ch = etree.Element("Chord")
    etree.SubElement(ch, "durationType").text = dur
    etree.SubElement(etree.SubElement(ch, "Note"), "pitch").text = str(pitch)
    return ch


def _triplet():
    t = etree.Element("Tuplet")
    etree.SubElement(t, "normalNotes").text = "2"
    etree.SubElement(t, "actualNotes").text = "3"
    etree.SubElement(t, "baseNote").text = "eighth"
    return t


def _voice(*elements):
    v = etree.Element("voice")
    for e in elements:
        v.append(e)
    return v


def _score(staff_voices):
    """staff_voices: list of staves, each a list of voices for a single measure."""
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for sid, voices in enumerate(staff_voices, start=1):
        staff = etree.SubElement(score, "Staff", id=str(sid))
        m = etree.SubElement(staff, "Measure")
        for v in voices:
            m.append(v)
    return root


def _donor_voice():
    """quarter + triplet(3 eighths) + half rest = a full 4/4 measure."""
    rest = etree.Element("Rest")
    etree.SubElement(rest, "durationType").text = "half"
    return _voice(_chord(64, "quarter"), _triplet(),
                  _chord(64, "eighth"), _chord(62, "eighth"), _chord(61, "eighth"),
                  etree.Element("endTuplet"), rest)


def _broken_voice():
    """quarter + 3 plain eighths = 5/8 — the OCR result missing the bracket + rest."""
    return _voice(_chord(54, "quarter"),
                  _chord(54, "eighth"), _chord(54, "eighth"), _chord(54, "eighth"))


def test_mirror_within_same_staff():
    root = _score([[_donor_voice(), _broken_voice()]])
    assert fix_missing_tuplets(root) == 1
    broken = root.findall(".//Staff")[0].findall("Measure")[0].findall("voice")[1]
    assert broken.find("Tuplet") is not None
    assert broken.find("endTuplet") is not None
    assert _ticks(broken) == Fraction(1)  # padded to a full measure


def test_mirror_across_staves():
    """Donor on staff 1, broken voice alone on staff 2 — different Measure elements."""
    root = _score([[_donor_voice()], [_broken_voice()]])
    assert fix_missing_tuplets(root) == 1
    broken = root.findall(".//Staff")[1].findall("Measure")[0].find("voice")
    assert broken.find("Tuplet") is not None
    assert _ticks(broken) == Fraction(1)


def test_wellformed_voice_untouched():
    """A voice that already adds up must not be wrapped, even with a donor present."""
    ok = _voice(_chord(60, "half"), _chord(60, "half"))  # 4/4 exactly
    root = _score([[_donor_voice()], [ok]])
    assert fix_missing_tuplets(root) == 0
    assert ok.find("Tuplet") is None


def test_no_donor_no_fix():
    """A malformed voice with no matching tuplet anywhere is left alone (no guessing)."""
    root = _score([[_broken_voice()]])
    assert fix_missing_tuplets(root) == 0
    broken = root.findall(".//Staff")[0].find("Measure").find("voice")
    assert broken.find("Tuplet") is None


def test_voice_with_own_tuplet_skipped():
    """A voice already carrying a tuplet is not a fix target."""
    root = _score([[_donor_voice(), _donor_voice()]])
    assert fix_missing_tuplets(root) == 0
