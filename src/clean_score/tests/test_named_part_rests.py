"""A lyric line naming a part that rests through its system is reported, not placed (#482).

The AI prompt now asks for part names in every line. A name for a part with no notes
in that system means the name or the system is wrong; running the words on into the
next system would hide that, so the line is left out and said.
"""

from lxml import etree

from src.clean_score.lyric_txt import PART_RESTS, place_lyrics

from .scorebuilder import build_score, placed_lyrics

NAMES = {1: "T1", 2: "T2"}
# The app passes the printed systems (pipeline._printed_systems); a line fills its own.
SYSTEMS = [(1, 1), (2, 2), (3, 3)]


def _rest_through(root, staff_id, measure):
    """Make one staff rest through one bar (1-based)."""
    m = root.findall(f".//Score/Staff[@id='{staff_id}']/Measure")[measure - 1]
    voice = m.find("voice")
    for chord in voice.findall("Chord"):
        voice.remove(chord)
    rest = etree.SubElement(voice, "Rest")
    etree.SubElement(rest, "durationType").text = "measure"
    etree.SubElement(rest, "duration").text = "4/4"
    return root


def _score():
    # Three one-bar systems; T2 rests through the second.
    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names=NAMES, line_breaks=(1, 2))
    return _rest_through(root, 2, 2)


def test_a_line_naming_a_resting_part_warns_and_places_nothing():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 2, "lyrics": [
        {"staff_number": 1, "text": "la-la", "parts": ["T2"]}]}], replace=True, systems=SYSTEMS)
    kinds = [m.kind for m in result.mismatches]
    assert kinds == [PART_RESTS]
    warning = result.mismatches[0]
    assert (warning.measure_start, warning.measure_end, warning.staff_ids) == (2, 2, (2,))
    assert "T2" in warning.message and "no notes" in warning.message
    # Not run on into system 3, where T2 sings again.
    assert placed_lyrics(root).get(2, "") == ""


def test_the_other_parts_in_the_block_are_still_placed():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 2, "lyrics": [
        {"staff_number": 1, "text": "yk-si", "parts": ["T1"]},
        {"staff_number": 2, "text": "kak-si", "parts": ["T2"]}]}], replace=True, systems=SYSTEMS)
    assert [m.kind for m in result.mismatches] == [PART_RESTS]
    assert placed_lyrics(root) == {1: "yk-si"}


def test_a_named_part_that_sings_takes_its_words_without_a_warning():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 3, "lyrics": [
        {"staff_number": 2, "text": "kol-me", "parts": ["T2"]}]}], replace=True, systems=SYSTEMS)
    assert result.mismatches == []
    assert placed_lyrics(root) == {2: "kol-me"}


def test_a_line_placed_by_staff_number_keeps_running_on_as_before():
    """Only a named part is checked: the staff_number route is unchanged."""
    root = _score()
    result = place_lyrics(root, [{"measure_start": 2, "lyrics": [
        {"staff_number": 2, "text": "nel-jä"}]}], replace=True, systems=SYSTEMS)
    assert PART_RESTS not in [m.kind for m in result.mismatches]
    assert placed_lyrics(root) == {2: "nel-jä"}


def test_the_grid_keeps_a_system_where_every_staff_rests():
    """The clean grid shows it as a line, not a gap in the numbering (#482)."""
    from src.clean_score.utils import per_system

    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names=NAMES, line_breaks=(1, 2))
    _rest_through(root, 1, 2)
    _rest_through(root, 2, 2)
    layouts = per_system.system_layout(root)
    assert [(s.start, s.end) for s in layouts] == [(1, 1), (2, 2), (3, 3)]
    assert layouts[1].staves == []
    assert [len(s.staves) for s in (layouts[0], layouts[2])] == [2, 2]
