"""The inverse of the voice split, and how it decides what shared a staff."""

from lxml import etree

from src.clean_score.implode import grouping, implode, voice_of


def score(parts: list[tuple[int, str]], bars: int = 2, notes: bool = True) -> etree._Element:
    """A cleaned score: one part per staff, one voice each."""
    root = etree.Element("museScore")
    element = etree.SubElement(root, "Score")
    for staff_id, name in parts:
        part = etree.SubElement(element, "Part")
        etree.SubElement(part, "Staff", id=str(staff_id))
        track = etree.SubElement(part, "trackName")
        track.text = name
    for staff_id, _ in parts:
        staff = etree.SubElement(element, "Staff", id=str(staff_id))
        for _ in range(bars):
            measure = etree.SubElement(staff, "Measure")
            voice = etree.SubElement(measure, "voice")
            etree.SubElement(voice, "Chord" if notes else "Rest")
    return root


def with_meta(root: etree._Element, name: str, text: str) -> etree._Element:
    score_element = root.find("Score")
    tag = etree.Element("metaTag", name=name)
    tag.text = text
    score_element.insert(0, tag)
    return root


def test_a_recorded_staff_map_says_which_voices_shared_a_staff() -> None:
    root = with_meta(
        score([(1, "T1"), (2, "T2"), (3, "B1"), (4, "B2")]), "lyricsStaffMap", "1:1,2;2:3,4"
    )

    found = grouping(root)

    assert not found.inferred
    assert [printed.staves for printed in found.printed] == [[1, 2], [3, 4]]


def test_a_per_system_map_is_read_over_the_whole_score() -> None:
    root = with_meta(
        score([(1, "T1"), (2, "T2"), (3, "B1"), (4, "B2")]),
        "lyricsSystemMap",
        '[{"start":1,"end":4,"map":{"1":[1,2],"2":[3,4]}},'
        ' {"start":5,"end":8,"map":{"1":[1],"2":[3,4]}}]',
    )

    found = grouping(root)

    # T2 rests through the second system, so that system prints one staff for
    # the tenors -- but the two still shared a staff and still do here.
    assert not found.inferred
    assert [printed.staves for printed in found.printed] == [[1, 2], [3, 4]]


def test_without_a_map_the_grouping_is_guessed_and_says_so() -> None:
    root = score([(1, "S1"), (2, "S2"), (3, "A1"), (4, "A2")])

    found = grouping(root)

    assert found.inferred
    assert [printed.staves for printed in found.printed] == [[1, 2], [3, 4]]


def test_a_part_that_is_not_a_choir_voice_keeps_its_own_staff() -> None:
    root = score([(1, "Solo"), (2, "Soprano 1"), (3, "Soprano 2")])

    found = grouping(root)

    assert [printed.staves for printed in found.printed] == [[1], [2, 3]]


def test_four_of_a_voice_are_paired_in_order() -> None:
    root = score([(1, "Alto 1-1"), (2, "Alto 1-2"), (3, "Alto 2-1"), (4, "Alto 2-2")])

    assert [printed.staves for printed in grouping(root).printed] == [[1, 2], [3, 4]]


def test_the_click_staff_is_not_part_of_the_printed_page() -> None:
    root = score([(1, "S1"), (2, "S2")])
    silent = etree.SubElement(root.find("Score"), "Staff", id="3")
    etree.SubElement(etree.SubElement(silent, "Measure"), "voice")
    part = etree.SubElement(root.find("Score"), "Part")
    etree.SubElement(part, "Staff", id="3")
    etree.SubElement(part, "trackName").text = "Click"

    assert [printed.staves for printed in grouping(root).printed] == [[1, 2]]


def test_imploding_puts_two_voices_back_on_one_staff() -> None:
    root = with_meta(score([(1, "T1"), (2, "T2")]), "lyricsStaffMap", "1:1,2")

    implode(root)

    staves = root.find("Score").findall("Staff")
    assert len(staves) == 1
    assert [len(bar.findall("voice")) for bar in staves[0].findall("Measure")] == [2, 2]
    assert len(root.find("Score").findall("Part")) == 1


def test_only_the_first_voice_keeps_the_clef_and_key() -> None:
    root = with_meta(score([(1, "T1"), (2, "T2")]), "lyricsStaffMap", "1:1,2")
    for staff in root.find("Score").findall("Staff"):
        voice = staff.find("Measure/voice")
        voice.insert(0, etree.Element("Clef"))
        voice.insert(1, etree.Element("KeySig"))

    implode(root)

    first = root.find("Score/Staff/Measure")
    assert len(first.findall("voice/Clef")) == 1
    assert len(first.findall("voice/KeySig")) == 1


def test_the_lyric_routing_goes_with_the_staves_it_described() -> None:
    root = with_meta(score([(1, "T1"), (2, "T2")]), "lyricsStaffMap", "1:1,2")

    implode(root)

    assert not [t for t in root.iter("metaTag") if t.get("name") == "lyricsStaffMap"]


def test_a_name_is_only_a_voice_when_the_whole_name_is_one() -> None:
    assert voice_of("S1") == ("S", (1,))
    assert voice_of("Alto 1-2") == ("A", (1, 2))
    assert voice_of("Sopraano 2") == ("S", (2,))
    assert voice_of("Solo") is None
    assert voice_of("Click") is None
