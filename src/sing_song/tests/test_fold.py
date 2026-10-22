import sys
from pathlib import Path

import pytest
from lxml import etree

from src.sing_song.fold import fold, plan  # noqa: E402


def chord(pitch, dur="quarter", syl=None):
    words = f"<Lyrics><text>{syl}</text></Lyrics>" if syl else ""
    return f"<Chord><durationType>{dur}</durationType>{words}<Note><pitch>{pitch}</pitch></Note></Chord>"


def rest(dur="measure"):
    return f"<Rest><durationType>{dur}</durationType><duration>4/4</duration></Rest>" if dur == "measure" \
        else f"<Rest><durationType>{dur}</durationType></Rest>"


def score(path, staves):
    """staves: [(name, [bar content, ...])], one voice a bar"""
    parts = "".join(f'<Part><Staff id="{i}"/><trackName>{n}</trackName></Part>'
                    for i, (n, _) in enumerate(staves, 1))
    body = "".join(f'<Staff id="{i}">' + "".join(f"<Measure><voice>{b}</voice></Measure>" for b in bars)
                   + "</Staff>" for i, (_, bars) in enumerate(staves, 1))
    path.write_text(f'<museScore version="3.02"><Score>{parts}{body}</Score></museScore>')
    return str(path)


def voices(path):
    s = etree.parse(path).getroot().find("Score")
    return {p.findtext("trackName"): [[[n.findtext("pitch") for n in v.iter("Note")] for v in m.findall("voice")]
                                      for m in st.findall("Measure")]
            for p, st in zip(s.findall("Part"), s.findall("Staff"))}


def test_lettered_lines_go_to_their_part_and_the_rest_stay():
    assert plan(["S1", "S1b", "S1c", "S2", "S2b", "A1", "Tb", "Solo"]) == {1: 0, 2: 0, 4: 3}


def test_a_divisi_line_becomes_a_further_voice_of_its_part(tmp_path):
    src = score(tmp_path / "in.mscx", [
        ("S1", [chord(72) * 4, chord(72) * 4, chord(72) * 4]),
        ("S1b", [chord(69) * 4, rest(), chord(72) * 4]),       # bar 2 rests, bar 3 is S1's own line
        ("S1c", [chord(65) * 4, chord(67) * 4, chord(72, syl="la") * 4]),
        ("A1", [chord(60) * 4] * 3),
    ])
    out = tmp_path / "out.mscx"
    groups = fold(src, str(out))
    assert groups == [("S1", ["1", "2", "3"]), ("A1", ["4"])]
    v = voices(str(out))
    assert list(v) == ["S1", "A1"]
    assert v["S1"][0] == [["72"] * 4, ["69"] * 4, ["65"] * 4]
    assert v["S1"][1] == [["72"] * 4, ["67"] * 4]      # a resting line adds no voice
    assert v["S1"][2] == [["72"] * 4]                  # nor one singing the part's own notes, words aside
    s = etree.parse(str(out)).getroot().find("Score")
    assert [p.find("Staff").get("id") for p in s.findall("Part")] == ["1", "2"]
    assert [st.get("id") for st in s.findall("Staff")] == ["1", "2"]


def test_more_lines_than_a_staff_has_voices_stop(tmp_path):
    src = score(tmp_path / "in.mscx", [("S1", [chord(72) * 4])]
                + [(f"S1{c}", [chord(60 + k) * 4]) for k, c in enumerate("bcde")])
    with pytest.raises(SystemExit, match="more than 4"):
        fold(src, str(tmp_path / "out.mscx"))


def test_a_clef_or_time_signature_stays_with_the_parts_own_voice(tmp_path):
    src = score(tmp_path / "in.mscx", [
        ("T1", [chord(60) * 4]),
        ("T1b", ["<Clef><concertClefType>G8vb</concertClefType></Clef><TimeSig><sigN>4</sigN></TimeSig>"
                 + chord(55) * 4]),
    ])
    out = tmp_path / "out.mscx"
    fold(src, str(out))
    second = etree.parse(str(out)).getroot().find("Score/Staff/Measure").findall("voice")[1]
    assert [el.tag for el in second] == ["Chord"] * 4


def test_a_struck_note_is_not_the_same_as_a_held_one(tmp_path):
    # #104 bar 236: B2 held Eb2 over from the bar before while B2b struck it again; the fold
    # dropped B2b's bar as a copy and the piano lost the onset the B2b line sings
    held = ('<Chord><durationType>whole</durationType><Note><Spanner type="Tie"><prev><location>'
            '<measures>-1</measures></location></prev></Spanner><pitch>39</pitch></Note></Chord>')
    src = score(tmp_path / "in.mscx", [("B2", [chord(39, "whole"), held]),
                                       ("B2b", [chord(39, "whole"), chord(39, "whole")])])
    out = tmp_path / "out.mscx"
    fold(src, str(out))
    assert voices(str(out))["B2"][1] == [["39"], ["39"]]


def test_a_bar_where_the_part_itself_has_no_voice_takes_the_divisi_line(tmp_path):
    src = score(tmp_path / "in.mscx", [("S1", [chord(72) * 4, chord(72) * 4]), ("S1b", [chord(69) * 4] * 2)])
    tree = etree.parse(src)
    first = tree.getroot().find("Score").find("Staff").find("Measure")
    first.remove(first.find("voice"))
    tree.write(src)
    out = tmp_path / "out.mscx"
    fold(src, str(out))
    assert voices(str(out))["S1"] == [[["69"] * 4], [["72"] * 4, ["69"] * 4]]


@pytest.mark.parametrize("shift", [
    "<location><fractions>1/4</fractions></location>",                                   # starts a beat later
    "<Tuplet><normalNotes>2</normalNotes><actualNotes>3</actualNotes></Tuplet>",         # other ratio
])
def test_the_same_notes_at_another_time_are_another_line(tmp_path, shift):
    base = "<Tuplet><normalNotes>4</normalNotes><actualNotes>3</actualNotes></Tuplet>" \
        if "Tuplet" in shift else ""
    src = score(tmp_path / "in.mscx", [("S1", [base + chord(72)]), ("S1b", [shift + chord(72)])])
    out = tmp_path / "out.mscx"
    fold(src, str(out))
    assert voices(str(out))["S1"] == [[["72"], ["72"]]]


def test_a_measure_rest_of_another_length_is_not_the_same():
    from src.sing_song.fold import notes_of
    a = etree.fromstring(f"<voice>{rest()}</voice>")
    b = etree.fromstring("<voice><Rest><durationType>measure</durationType><duration>3/4</duration></Rest></voice>")
    assert notes_of(a) != notes_of(b)
