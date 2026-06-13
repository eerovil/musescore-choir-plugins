"""
Tests for melisma marks ('~') in lyric import: a syllable sung over several notes.
Import must place the syllable on the first note, leave the '~' notes blank, AND draw
a slur from the syllable note across them (repairing an OCR-dropped slur).
"""

from lxml import etree

from src.clean_score.lyric_txt import import_txt_into_mscx


def _chord(pitch, dur="eighth"):
    ch = etree.Element("Chord")
    etree.SubElement(ch, "durationType").text = dur
    etree.SubElement(etree.SubElement(ch, "Note"), "pitch").text = str(pitch)
    return ch


def _one_staff_measure(*chords):
    root = etree.Element("museScore")
    score = etree.SubElement(root, "Score")
    part = etree.SubElement(score, "Part")
    etree.SubElement(part, "Staff", id="1")
    staff = etree.SubElement(score, "Staff", id="1")
    m = etree.SubElement(staff, "Measure")
    v = etree.SubElement(m, "voice")
    for c in chords:
        v.append(c)
    return root, [c for c in v.findall("Chord")]


def _slur(chord, kind):
    for sp in chord.findall("Spanner[@type='Slur']"):
        if sp.find(kind) is not None:
            return sp
    return None


def _text(chord):
    return chord.findtext(".//Lyrics/text")


def test_melisma_places_syllable_and_draws_slur():
    # 4 eighth notes: "lu" sung over notes 1-3 (melisma), "la" on note 4.
    root, chords = _one_staff_measure(_chord(60), _chord(62), _chord(64), _chord(65))
    import_txt_into_mscx(root, by_measure={1: {1: ["lu", "~", "~", "la"]}}, clear_existing=True)
    # syllable placement
    assert _text(chords[0]) == "lu"
    assert _text(chords[1]) is None
    assert _text(chords[2]) is None
    assert _text(chords[3]) == "la"
    # slur from note 1 to note 3 (the two melisma notes), eighth apart x2 = 1/4
    assert _slur(chords[0], "next") is not None
    assert _slur(chords[2], "prev") is not None
    frac = chords[0].findtext("Spanner[@type='Slur']/next/location/fractions")
    assert frac == "1/4"
    # the middle melisma note carries no endpoint (it's inside the slur)
    assert _slur(chords[1], "next") is None and _slur(chords[1], "prev") is None


def test_underscore_blank_does_not_draw_slur():
    """A plain '_' (eligible blank, not a melisma) must NOT create a slur."""
    root, chords = _one_staff_measure(_chord(60), _chord(62), _chord(64))
    import_txt_into_mscx(root, by_measure={1: {1: ["do", "_", "re"]}}, clear_existing=True)
    assert _text(chords[0]) == "do"
    assert _text(chords[1]) is None
    assert _text(chords[2]) == "re"
    assert chords[0].find("Spanner[@type='Slur']") is None
    assert chords[1].find("Spanner[@type='Slur']") is None


def test_melisma_keeps_following_syllables_aligned():
    """The note after a melisma gets the next syllable (no cascade shift)."""
    root, chords = _one_staff_measure(_chord(60), _chord(62), _chord(64), _chord(65))
    import_txt_into_mscx(root, by_measure={1: {1: ["a", "~", "b", "c"]}}, clear_existing=True)
    assert [_text(c) for c in chords] == ["a", None, "b", "c"]
    assert _slur(chords[0], "next") is not None
    assert _slur(chords[1], "prev") is not None
