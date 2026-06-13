"""
Tests for the cross-voice slur mirror: copy a slur onto a parallel voice that lost it.
"""

from lxml import etree

from src.clean_score.utils.missing_slurs import add_missing_slurs, _slur_with


def _chord(pitch, dur="quarter", slur=None):
    ch = etree.Element("Chord")
    etree.SubElement(ch, "durationType").text = dur
    etree.SubElement(etree.SubElement(ch, "Note"), "pitch").text = str(pitch)
    if slur == "start":
        sp = etree.SubElement(ch, "Spanner", type="Slur")
        etree.SubElement(etree.SubElement(sp, "Slur"), "up").text = "down"
        loc = etree.SubElement(etree.SubElement(sp, "next"), "location")
        etree.SubElement(loc, "fractions").text = "1/4"
    elif slur == "end":
        sp = etree.SubElement(ch, "Spanner", type="Slur")
        loc = etree.SubElement(etree.SubElement(sp, "prev"), "location")
        etree.SubElement(loc, "fractions").text = "-1/4"
    return ch


def _voice(*chords):
    v = etree.Element("voice")
    for c in chords:
        v.append(c)
    return v


def _score(*staff_voices):
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    for sid, voices in enumerate(staff_voices, start=1):
        staff = etree.SubElement(score, "Staff", id=str(sid))
        m = etree.SubElement(staff, "Measure")
        for v in voices:
            m.append(v)
    return root


def _has_slur_span(voice):
    chords = voice.findall("Chord")
    return any(_slur_with(c, "next") is not None for c in chords) and \
        any(_slur_with(c, "prev") is not None for c in chords)


def test_mirror_slur_within_same_staff():
    donor = _voice(_chord(64, slur="start"), _chord(62, slur="end"))
    broken = _voice(_chord(60), _chord(59))
    root = _score([donor, broken])
    assert add_missing_slurs(root) == 1
    assert _has_slur_span(broken)


def test_mirror_slur_across_staves():
    donor = _voice(_chord(64, slur="start"), _chord(62, slur="end"))
    broken = _voice(_chord(48), _chord(47))
    root = _score([donor], [broken])  # different staves, same measure index
    assert add_missing_slurs(root) == 1
    assert _has_slur_span(broken)


def test_no_mirror_when_note_count_differs():
    """Endpoint tick present but a different number of notes between -> not parallel."""
    donor = _voice(_chord(64, slur="start"), _chord(62, slur="end"))
    # broken voice has an extra note in the span (eighths), so count differs
    broken = _voice(_chord(60, "eighth"), _chord(59, "eighth"), _chord(58))
    root = _score([donor, broken])
    assert add_missing_slurs(root) == 0
    assert not _has_slur_span(broken)


def test_voice_that_already_has_slur_untouched():
    donor = _voice(_chord(64, slur="start"), _chord(62, slur="end"))
    already = _voice(_chord(60, slur="start"), _chord(59, slur="end"))
    root = _score([donor, already])
    assert add_missing_slurs(root) == 0


def test_no_donor_no_change():
    a = _voice(_chord(60), _chord(62))
    b = _voice(_chord(64), _chord(65))
    root = _score([a, b])
    assert add_missing_slurs(root) == 0
