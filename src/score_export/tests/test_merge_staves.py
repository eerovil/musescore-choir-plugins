"""Parts sharing a staff in the published picture (#246, #420).

Small hand-written scores, so no MuseScore is needed for most of it: which voices
each part lands in, the clef, the words, the rests, the stems, and the refusal.
The last test exports a real score and needs the CLI.
"""

import os

import pytest
from lxml import etree

from src.score_export import musescore, score as score_mod

from .conftest import needs_musescore


def _chord(pitch, text=None, stem=True, length="half"):
    lyric = f"<Lyrics><text>{text}</text></Lyrics>" if text else ""
    stem = "<StemDirection>up</StemDirection>" if stem else ""
    return (f"<Chord><durationType>{length}</durationType>{lyric}{stem}"
            f"<Note><pitch>{pitch}</pitch></Note></Chord>")


MEASURE_REST = "<Rest><durationType>measure</durationType><duration>4/4</duration></Rest>"


def _score(staves):
    """`staves` is [(name, clef, [bar...])]; a bar is one voice's contents, or a
    list of them for a bar with more than one voice."""
    xml = ["<museScore version=\"3.02\"><Division>480</Division><Score>"]
    for i, (name, _clef, _bars) in enumerate(staves, 1):
        xml.append(f'<Part><Staff id="{i}"><StaffType group="pitched"><name>stdNormal</name>'
                   f'</StaffType></Staff><trackName>{name}</trackName>'
                   f"<Instrument><longName>{name}</longName><shortName>{name}</shortName>"
                   f"<trackName>{name}</trackName></Instrument></Part>")
    for i, (_name, clef, bars) in enumerate(staves, 1):
        xml.append(f'<Staff id="{i}">')
        for number, bar in enumerate(bars):
            head = (f"<Clef><concertClefType>{clef}</concertClefType>"
                    f"<transposingClefType>{clef}</transposingClefType></Clef>"
                    "<TimeSig><sigN>4</sigN><sigD>4</sigD></TimeSig>") if number == 0 else ""
            voices = bar if isinstance(bar, list) else [bar]
            xml.append("<Measure>" + "".join(f"<voice>{head if j == 0 else ''}{v}</voice>"
                                             for j, v in enumerate(voices)) + "</Measure>")
        xml.append("</Staff>")
    xml.append("</Score></museScore>")
    return etree.fromstring("".join(xml).encode())


def _four():
    return _score([
        ("S1", "G", [_chord(72, "la") + _chord(74, "lo"), MEASURE_REST]),
        ("S2", "G", [_chord(67, "la") + _chord(69, "lo"), MEASURE_REST]),
        ("T", "G8vb", [_chord(60, "ka") + _chord(62, "ki"), _chord(60) + _chord(62)]),
        ("B", "F", [_chord(48, "ku") + _chord(50, "ko"), MEASURE_REST]),
    ])


def _voices(root, staff_id):
    staff = root.find(f"Score/Staff[@id='{staff_id}']")
    return [[[int(p.text) for p in v.iter("pitch")] for v in m.findall("voice")]
            for m in staff.findall("Measure")]


def test_the_lower_part_becomes_voice_2_of_the_upper_staff():
    root = _four()
    where = score_mod.merge_staves(root, [("S1", "S2"), ("T", "B")])
    assert where == {"S1": {"staff": 0, "voices": [1]}, "S2": {"staff": 0, "voices": [2]},
                     "T": {"staff": 1, "voices": [1]}, "B": {"staff": 1, "voices": [2]}}
    assert len(root.findall("Score/Part")) == 2
    assert [s.get("id") for s in root.findall("Score/Staff")] == ["1", "2"]
    assert [s.get("id") for s in root.findall("Score/Part/Staff")] == ["1", "2"]
    assert _voices(root, "1")[0] == [[72, 74], [67, 69]]
    assert _voices(root, "2")[1] == [[60, 62], []]   # the bass rests here


def test_a_part_that_sings_two_voices_takes_voices_1_and_3():
    """MuseScore stems voices 1 and 3 up and 2 and 4 down, so the upper part keeps
    the up-stem voices and a unison with the lower part shares one notehead."""
    root = _score([
        ("S1", "G", [[_chord(72) + _chord(74), _chord(67) + _chord(69)], _chord(72) + _chord(74)]),
        ("S1b", "G", [_chord(65) + _chord(64), _chord(65) + _chord(64)]),
    ])
    where = score_mod.merge_staves(root, [("S1", "S1b")])
    assert where == {"S1": {"staff": 0, "voices": [1, 3]}, "S1b": {"staff": 0, "voices": [2]}}
    assert _voices(root, "1") == [[[72, 74], [65, 64], [67, 69]], [[72, 74], [65, 64]]]


def test_a_bar_both_rest_through_is_one_rest():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    bar = root.find("Score/Staff[@id='1']").findall("Measure")[1]
    assert len(bar.findall("voice")) == 1


def test_the_staff_is_named_after_both_parts_and_the_rest_are_untouched():
    root = _four()
    score_mod.merge_staves(root, [("T", "B")])
    names = [p.findtext("trackName") for p in root.findall("Score/Part")]
    assert names == ["S1", "S2", "T/B"]
    assert root.find("Score/Part[3]/Instrument/longName").text == "T/B"


def test_the_staff_is_counted_as_the_part_it_lands_in():
    root = _four()
    where = score_mod.merge_staves(root, [("S1", "S2")])
    assert where["T"] == {"staff": 1, "voices": [1]}
    assert where["B"] == {"staff": 2, "voices": [1]}


def test_the_lower_parts_clef_is_the_staffs_clef():
    root = _four()
    score_mod.merge_staves(root, [("T", "B")])
    staff = root.find("Score/Staff[@id='3']")
    assert [c.findtext("concertClefType") for c in staff.iter("Clef")] == ["F"]


def _clef(kind):
    return (f"<Clef><concertClefType>{kind}</concertClefType>"
            f"<transposingClefType>{kind}</transposingClefType></Clef>")


def _clefs(staff):
    """[(bar, clef, how many chords or rests come before it in its voice)]."""
    found = []
    for number, measure in enumerate(staff.findall("Measure"), 1):
        for voice in measure.findall("voice"):
            before = 0
            for element in voice:
                if element.tag == "Clef":
                    found.append((number, element.findtext("concertClefType"), before))
                elif element.tag in ("Chord", "Rest"):
                    before += 1
    return found


def test_a_clef_change_in_the_lower_part_is_kept_where_it_happens():
    root = _score([
        ("T", "G8vb", [_chord(60) + _chord(62), _chord(60) + _chord(62)]),
        ("B", "F", [_chord(48) + _chord(50), _chord(48) + _clef("G8vb") + _chord(50)]),
    ])
    score_mod.merge_staves(root, [("T", "B")])
    assert _clefs(root.find("Score/Staff[@id='1']")) == [(1, "F", 0), (2, "G8vb", 1)]


def test_a_clef_change_only_the_upper_part_makes_is_kept():
    """Review of #421: the upper part's own change used to go, leaving the rest of
    the song under the wrong clef."""
    root = _score([
        ("S", "G", [_chord(72) + _chord(74), _chord(72) + _clef("F") + _chord(50),
                    _chord(48) + _chord(50)]),
        ("A", "G", [_chord(67) + _chord(69), _chord(67) + _chord(69), _chord(55) + _chord(57)]),
    ])
    score_mod.merge_staves(root, [("S", "A")])
    assert _clefs(root.find("Score/Staff[@id='1']")) == [(1, "G", 0), (2, "F", 1)]


def test_stems_are_left_for_musescore_to_choose():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    assert root.find("Score/Staff[@id='1']").find(".//StemDirection") is None
    assert root.find("Score/Staff[@id='2']").find(".//StemDirection") is not None


def test_words_both_voices_sing_are_printed_once():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    second = root.find("Score/Staff[@id='1']/Measure").findall("voice")[1]
    assert second.find(".//Lyrics") is None


def test_different_words_go_on_the_line_below_the_upper_parts_verses():
    root = _score([
        ("T", "G8vb", [_chord(60, "ka") + _chord(62, "ki")]),
        ("B", "F", [_chord(48, "ku") + _chord(50, "ko")]),
    ])
    # The tenor sings a second verse too, so the bass's words go on the third line.
    for lyric in list(root.find("Score/Staff[@id='1']").iter("Lyrics")):
        verse2 = etree.fromstring("<Lyrics><no>1</no><text>x</text></Lyrics>")
        lyric.addnext(verse2)
    score_mod.merge_staves(root, [("T", "B")])
    second = root.find("Score/Staff[@id='1']/Measure").findall("voice")[1]
    assert [(l.findtext("text"), l.findtext("no")) for l in second.iter("Lyrics")] == \
        [("ku", "2"), ("ko", "2")]


def test_the_moved_voice_does_not_restate_the_staffs_signatures():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    second = root.find("Score/Staff[@id='1']/Measure").findall("voice")[1]
    assert second.find("TimeSig") is None and second.find("Clef") is None


def test_parts_singing_more_voices_than_a_staff_holds_are_refused():
    three = [_chord(60), _chord(55), _chord(52)]
    root = _score([("A", "G", [three]), ("B", "G", [_chord(55) + _chord(57)])])
    assert not score_mod.can_share(root, "A", "B")
    with pytest.raises(ValueError, match="A and B sing 3 and 1 voices"):
        score_mod.merge_staves(root, [("A", "B")])


@needs_musescore
def test_musescore_exports_the_shared_staff_as_one_part_with_two_voices(tmp_path):
    source = os.path.join(os.path.dirname(__file__), "test_files", "repeat.mscx")
    picture, where = score_mod.merged_copy(source, str(tmp_path), [("S1", "A1")])
    out = musescore.run_musescore(picture, os.path.join(str(tmp_path), "shared.musicxml"))
    xml = etree.parse(out).getroot()
    parts = xml.findall("part")
    assert len(parts) == 1
    assert sorted({v.text for v in parts[0].iter("voice")}) == ["1", "2"]
    assert xml.findtext("part-list/score-part/part-name") == "S1/A1"
    assert where == {"S1": {"staff": 0, "voices": [1]}, "A1": {"staff": 0, "voices": [2]}}
    # The score the MIDI comes from is untouched.
    assert len(etree.parse(source).getroot().findall("Score/Part")) == 2
