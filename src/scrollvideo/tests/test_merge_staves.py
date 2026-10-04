"""Two parts on one staff in the picture (#246), and nothing else changed by it.

Most of this works on small hand-written scores, so it needs no MuseScore: which
voice a part lands in, the clef, the words, the rests, the stems, and every way a
grouping is refused. The last tests engrave a real score and need the CLI.
"""

import os

import pytest
from lxml import etree

from src.scrollvideo import score as score_mod
from src.scrollvideo.build import prepare

from .conftest import needs_musescore

SIMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "clean_score", "tests",
                      "test_files", "simple_1_output.mscx")


def _chord(pitch, text=None, stem=True):
    lyric = f"<Lyrics><text>{text}</text></Lyrics>" if text else ""
    stem = "<StemDirection>up</StemDirection>" if stem else ""
    return (f"<Chord><durationType>half</durationType>{lyric}{stem}"
            f"<Note><pitch>{pitch}</pitch></Note></Chord>")


MEASURE_REST = "<Rest><durationType>measure</durationType><duration>4/4</duration></Rest>"


def _score(staves):
    """`staves` is [(name, clef, [bar contents...])]: one voice per bar."""
    xml = ["<museScore><Score>"]
    for i, (name, _clef, _bars) in enumerate(staves, 1):
        xml.append(f'<Part><Staff id="{i}"/><trackName>{name}</trackName>'
                   f"<Instrument><longName>{name}</longName>"
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


def test_the_lower_part_becomes_the_second_voice_of_the_upper_staff():
    root = _four()
    staff_of = score_mod.merge_staves(root, [("S1", "S2"), ("T", "B")])
    assert staff_of == {"S1": 0, "S2": 0, "T": 1, "B": 1}
    assert len(root.findall("Score/Part")) == 2
    assert [s.get("id") for s in root.findall("Score/Staff")] == ["1", "2"]
    assert [s.get("id") for s in root.findall("Score/Part/Staff")] == ["1", "2"]
    assert _voices(root, "1")[0] == [[72, 74], [67, 69]]
    assert _voices(root, "2")[1] == [[60, 62], []]   # the bass rests here


def test_a_bar_both_voices_rest_through_is_one_rest():
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


def test_the_lower_parts_clef_is_the_staffs_clef():
    root = _four()
    score_mod.merge_staves(root, [("T", "B")])
    staff = root.find("Score/Staff[@id='3']")
    assert [c.findtext("concertClefType") for c in staff.iter("Clef")] == ["F"]


def test_stems_are_left_for_musescore_to_choose():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    assert root.find("Score/Staff[@id='1']").find(".//StemDirection") is None
    # A staff nobody merged keeps what cleaning wrote.
    assert root.find("Score/Staff[@id='2']").find(".//StemDirection") is not None


def test_words_both_voices_sing_are_printed_once():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    second = root.find("Score/Staff[@id='1']/Measure").findall("voice")[1]
    assert second.find(".//Lyrics") is None


def test_different_words_go_on_a_second_line():
    root = _four()
    score_mod.merge_staves(root, [("T", "B")])
    second = root.find("Score/Staff[@id='3']/Measure").findall("voice")[1]
    lyrics = second.findall(".//Lyrics")
    assert [(l.findtext("text"), l.findtext("no")) for l in lyrics] == \
        [("ku", "1"), ("ko", "1")]


def test_words_under_a_voice_that_has_none_above_it_stay_on_the_first_line():
    root = _score([("A", "G", [_chord(60) + _chord(62)]),
                   ("B", "G", [_chord(55, "la") + _chord(57, "lo")])])
    score_mod.merge_staves(root, [("A", "B")])
    second = root.find("Score/Staff[@id='1']/Measure").findall("voice")[1]
    assert [l.findtext("no") for l in second.iter("Lyrics")] == [None, None]


def test_the_moved_voice_does_not_restate_the_staffs_signatures():
    root = _four()
    score_mod.merge_staves(root, [("S1", "S2")])
    second = root.find("Score/Staff[@id='1']/Measure").findall("voice")[1]
    assert second.find("TimeSig") is None and second.find("Clef") is None


def test_a_part_with_two_voices_already_is_refused_by_bar():
    root = _score([("A", "G", [_chord(60) + _chord(62), [_chord(60), _chord(55)]]),
                   ("B", "G", [_chord(55) + _chord(57), _chord(55) + _chord(57)])])
    with pytest.raises(ValueError, match="A has more than one voice in bar 2"):
        score_mod.merge_staves(root, [("A", "B")])


@pytest.mark.parametrize("text, groups", [
    ("S1+S2, A1+A2", [("S1", "S2"), ("A1", "A2")]),
    ("S1+S2 A1+A2", [("S1", "S2"), ("A1", "A2")]),
    ("S1 + S2;\nA1+A2", [("S1", "S2"), ("A1", "A2")]),
    ("Soprano 1+Soprano 2", [("Soprano 1", "Soprano 2")]),
    ("  ", []),
    (None, []),
])
def test_a_grouping_is_read_the_ways_people_type_it(text, groups):
    assert score_mod.parse_groups(text) == groups
    assert score_mod.parse_groups(score_mod.format_groups(groups)) == groups


@pytest.mark.parametrize("text", ["S1", "S1+S2+A1", "S1+", "+S2"])
def test_a_group_is_exactly_two_parts(text):
    with pytest.raises(ValueError, match="two parts"):
        score_mod.parse_groups(text)


@pytest.mark.parametrize("groups, message", [
    ([("S1", "X")], "No such part: X"),
    ([("S1", "S2"), ("S2", "A1")], "S2 is in more than one"),
    ([("S1", "Click")], "Click has nothing to sing"),
])
def test_a_grouping_the_score_cannot_have_is_refused(groups, message):
    with pytest.raises(ValueError, match=message):
        score_mod.validate_groups(groups, ["S1", "S2", "A1"], ["Click"])


def test_merging_writes_a_copy_and_leaves_the_score_alone(tmp_path):
    source = tmp_path / "score.mscx"
    etree.ElementTree(_four()).write(str(source))
    before = source.read_bytes()
    picture, staff_of = score_mod.merged_copy(str(source), str(tmp_path), [("S1", "S2")])
    assert picture != str(source) and source.read_bytes() == before
    assert staff_of["S2"] == 0


@needs_musescore
def test_two_parts_engrave_as_one_staff_and_still_line_up_with_the_sound(tmp_path):
    """The real thing: S1 and A1 of a cleaned score drawn on one staff.

    `prepare` refuses a render whose highlights miss the notes MuseScore plays, and
    the clock and the sound still come from the unmerged score, so getting here at
    all is the check that the merged picture lines up.
    """
    (tmp_path / "plain").mkdir()
    (tmp_path / "merged").mkdir()
    plain = prepare(SIMPLE, str(tmp_path / "plain"))
    merged = prepare(SIMPLE, str(tmp_path / "merged"), staff_groups=[("S1", "A1")])
    assert plain.singing_staves == 2
    assert merged.singing_staves == 1
    assert merged.staff_of == {"S1": 0, "A1": 0}
    assert merged.focus_staff("A1") == 0
    assert len(merged.notes) == len(plain.notes)
    # The audio is made from the same score either way, so the mixes are too.
    assert merged.source == plain.source == SIMPLE
