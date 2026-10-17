"""Which parts share a staff on the site (#420): back the way the page prints them,
once the split score has more than five staves."""

import json

import pytest
from lxml import etree

from src.score_export import staff_groups


def _score(names, staff_map=None, system_map=None):
    bar = ("<Measure><voice><Chord><durationType>whole</durationType>"
           "<Note><pitch>60</pitch></Note></Chord></voice></Measure>")
    xml = ["<museScore><Score>"]
    if staff_map:
        xml.append(f'<metaTag name="lyricsStaffMap">{staff_map}</metaTag>')
    if system_map:
        text = json.dumps(system_map).replace('"', "&quot;")
        xml.append(f'<metaTag name="lyricsSystemMap">{text}</metaTag>')
    for i, name in enumerate(names, 1):
        xml.append(f'<Part><Staff id="{i}"/><trackName>{name}</trackName></Part>')
    for i, _ in enumerate(names, 1):
        xml.append(f'<Staff id="{i}">{bar * 8}</Staff>')
    xml.append("</Score></museScore>")
    return etree.fromstring("".join(xml).encode())


def test_five_parts_or_fewer_share_nothing():
    root = _score(["S1", "S2", "A1", "A2", "T"], staff_map="1:1,2;2:3,4;3:5")
    assert staff_groups.proposal(root) == ([], "")


def test_the_cleans_record_of_the_printed_staves_decides():
    root = _score(["S1", "S2", "S3", "S4", "A1", "A2", "A3", "A4"],
                  staff_map="1:1,2;2:3,4;3:5,6;4:7,8")
    assert staff_groups.proposal(root) == (
        [("S1", "S2"), ("S3", "S4"), ("A1", "A2"), ("A3", "A4")], "page")


def test_every_printed_pair_is_merged_and_a_part_alone_on_the_page_stays_alone():
    root = _score(["S1", "S1b", "S2", "S2b", "Solo", "A1", "A2"],
                  staff_map="1:1,2;2:3,4;3:5;4:6;5:7")
    assert staff_groups.proposal(root) == ([("S1", "S1b"), ("S2", "S2b")], "page")


def test_a_printed_staff_of_three_is_cut_into_a_pair_and_a_part_alone():
    root = _score(["T1", "T2", "T3", "B1", "B2", "B3"], staff_map="1:1,2,3;2:4,5,6")
    assert staff_groups.proposal(root)[0] == [("T1", "T2"), ("B1", "B2")]


def test_staves_changing_role_pair_the_parts_that_share_a_staff_in_most_bars():
    names = ["S1", "S1b", "S2", "A1", "A1b", "A2", "A2b"]
    system_map = [
        # A1b shares with A2 for two bars, with A1 for six: A1 wins.
        {"start": 1, "end": 2, "map": {"1": [1, 2], "2": [3], "3": [4], "4": [5, 6]}},
        {"start": 3, "end": 8, "map": {"1": [1, 2], "2": [3], "3": [4, 5], "4": [6, 7]}},
    ]
    root = _score(names, system_map=system_map)
    assert staff_groups.proposal(root) == (
        [("S1", "S1b"), ("A1", "A1b"), ("A2", "A2b")], "page, per system")


def test_with_no_record_a_b_part_goes_with_its_main_part_then_numbered_parts_pair():
    root = _score(["Solo", "S1", "S1b", "S2", "A1", "A2"])
    assert staff_groups.proposal(root) == (
        [("S1", "S1b"), ("A1", "A2")], "part names")


def test_a_record_that_leaves_a_singing_part_out_is_not_used():
    root = _score(["S1", "S2", "S3", "S4", "A1", "A2"], staff_map="1:1,2;2:3,4")
    assert staff_groups.proposal(root)[1] == "part names"


@pytest.mark.parametrize("text, groups", [
    ("S1+S2, A1+A2", [("S1", "S2"), ("A1", "A2")]),
    ("S1+S2 A1+A2", [("S1", "S2"), ("A1", "A2")]),
    ("S1+S2;\nA1 + A2", [("S1", "S2"), ("A1", "A2")]),
    ("", []),
])
def test_the_panels_text_is_read(text, groups):
    assert staff_groups.parse_groups(text) == groups
    assert staff_groups.parse_groups(staff_groups.format_groups(groups)) == groups


@pytest.mark.parametrize("groups, message", [
    ([("S1", "S2", "S3")], "exactly two"),
    ([("S1", "X")], "No such part: X"),
    ([("S1", "S2"), ("S2", "A1")], "S2 is in more than one"),
])
def test_a_pairing_the_score_cannot_have_is_refused(groups, message):
    with pytest.raises(ValueError, match=message):
        staff_groups.validate_groups(groups, ["S1", "S2", "A1"])


def test_three_parts_joined_by_plus_are_refused():
    with pytest.raises(ValueError, match="at most two parts"):
        staff_groups.parse_groups("S1+S2+S3")
