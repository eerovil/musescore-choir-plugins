"""A lyric line naming a part that rests through its system is reported, not placed (#482).

The AI prompt now asks for part names in every line. A name for a part with no notes
in that system means the name or the system is wrong; running the words on into the
next system would hide that, so the line is left out and said.
"""

from lxml import etree

from src.clean_score.lyric_txt import PART_RESTS, UNKNOWN_PART, _block_lines, place_lyrics

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


def _tie_whole_bar(root, staff_id, measure):
    """Every chord of one bar is held over from the bar before: no syllable, still notes."""
    for chord in root.findall(f".//Score/Staff[@id='{staff_id}']/Measure")[measure - 1].iter("Chord"):
        note = chord.find("Note")
        tie = etree.SubElement(note, "Spanner", type="Tie")
        etree.SubElement(tie, "prev")
    return root


def test_a_system_the_part_only_holds_over_into_is_not_resting():
    """A tied note is a note: the part sings there, it just takes no new syllable."""
    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names=NAMES, line_breaks=(1, 2))
    _tie_whole_bar(root, 2, 2)
    result = place_lyrics(root, [{"measure_start": 2, "lyrics": [
        {"staff_number": 2, "text": "oo", "parts": ["T2"]}]}], replace=True, systems=SYSTEMS)
    assert PART_RESTS not in [m.kind for m in result.mismatches]


def test_without_systems_the_check_stops_at_the_scores_own_break():
    """No printed systems given: the rest check reads the score's line breaks, so a
    part singing in a later system does not hide that it rests in this one."""
    root = _score()
    result = place_lyrics(root, [{"measure_start": 2, "lyrics": [
        {"staff_number": 2, "text": "la", "parts": ["T2"]}]}], replace=True)
    rests = [m for m in result.mismatches if m.kind == PART_RESTS]
    assert [(m.measure_start, m.measure_end) for m in rests] == [(2, 2)]
    assert placed_lyrics(root).get(2, "") == ""


def test_a_named_line_does_not_run_on_through_a_system_its_part_rests_in():
    root = _score()
    place_lyrics(root, [{"measure_start": 1, "lyrics": [
        {"staff_number": 2, "text": "a b c d", "parts": ["T2"]}]}], replace=True, systems=SYSTEMS)
    bars = {}
    from src.clean_score.lyric_txt import export_lyrics
    measure = None
    for line in export_lyrics(root).splitlines():
        if line.startswith("# Measure"):
            measure = int(line.split()[-1])
        elif line.strip().startswith("2"):
            bars[measure] = line.split(":", 1)[1].split()
    # The two extra syllables stay crammed in bar 1 rather than landing in system 3.
    assert all(t == "_" for t in bars.get(3, []))


def test_a_name_no_part_carries_falls_back_to_the_staff_number_and_says_so():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 3, "lyrics": [
        {"staff_number": 2, "text": "kol-me", "parts": ["Tenori 2"]}]}],
        replace=True, systems=SYSTEMS)
    unknown = [m for m in result.mismatches if m.kind == UNKNOWN_PART]
    assert len(unknown) == 1 and '"Tenori 2"' in unknown[0].message
    assert placed_lyrics(root) == {2: "kol-me"}


def test_a_bad_name_with_no_staff_number_says_the_line_was_not_placed():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 3, "lyrics": [
        {"text": "kol-me", "parts": ["Typo"]}]}], replace=True, systems=SYSTEMS)
    assert [m.kind for m in result.mismatches] == [UNKNOWN_PART]
    message = result.mismatches[0].message
    assert "not placed" in message and "placed by staff_number" not in message
    assert placed_lyrics(root) == {}


def test_a_name_two_parts_share_falls_back_to_the_staff_number():
    root = build_score(staff_ids=(1, 2), measures=3, chords=2, names={1: "T", 2: "T"},
                       line_breaks=(1, 2))
    result = place_lyrics(root, [{"measure_start": 3, "lyrics": [
        {"staff_number": 2, "text": "kol-me", "parts": ["T"]}]}], replace=True, systems=SYSTEMS)
    assert [m.kind for m in result.mismatches] == [UNKNOWN_PART]
    assert placed_lyrics(root) == {2: "kol-me"}


def test_a_follower_of_a_named_line_is_checked_as_named():
    """A b-part taking its base part's words is named by the same line."""
    system_map = [{"start": 1, "end": 3, "map": {}, "follow": {1: [2]}}]
    lines = _block_lines({"lyrics": [{"text": "la", "parts": ["T1"]}]}, 1, 3, {},
                         system_map, {"T1": 1})
    assert {(ln.staff, ln.named) for ln in lines} == {(1, True), (2, True)}


def test_without_systems_a_long_named_line_does_not_skip_a_resting_system():
    """No printed systems given and one line sent: the score's own breaks still end
    each system, so T2's words do not jump over bar 2 into bar 3 (#482)."""
    root = _score()
    place_lyrics(root, [{"measure_start": 1, "lyrics": [
        {"staff_number": 2, "text": "a b c d", "parts": ["T2"]}]}], replace=True)
    from src.clean_score.lyric_txt import export_lyrics
    bars, measure = {}, None
    for line in export_lyrics(root).splitlines():
        if line.startswith("# Measure"):
            measure = int(line.split()[-1])
        elif line.strip().startswith("2"):
            bars[measure] = line.split(":", 1)[1].split()
    assert all(t == "_" for t in bars.get(3, []))


def test_two_lines_naming_a_resting_part_warn_one_each():
    """Each source line has its own warning, so correcting one keeps the other's."""
    root = _score()
    result = place_lyrics(root, [{"measure_start": 2, "lyrics": [
        {"staff_number": 1, "position": "below", "text": "yk-si", "parts": ["T2"]},
        {"staff_number": 2, "position": "below", "text": "kak-si", "parts": ["T2"]}]}],
        replace=True, systems=SYSTEMS)
    rests = [m for m in result.mismatches if m.kind == PART_RESTS]
    assert len(rests) == 2
    assert len({m.lines for m in rests}) == 2 and all(len(m.lines) == 1 for m in rests)


def test_a_bad_name_whose_staff_number_is_no_staff_says_the_line_was_not_placed():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 3, "lyrics": [
        {"staff_number": 99, "text": "la", "parts": ["Typo"]}]}], replace=True, systems=SYSTEMS)
    assert [m.kind for m in result.mismatches] == [UNKNOWN_PART]
    message = result.mismatches[0].message
    assert "not placed" in message and "placed by staff_number" not in message
    assert placed_lyrics(root) == {}


def test_a_staff_id_the_score_has_not_is_said_as_unknown():
    root = _score()
    result = place_lyrics(root, [{"measure_start": 3, "lyrics": [
        {"text": "la", "parts": [99]}]}], replace=True, systems=SYSTEMS)
    assert [m.kind for m in result.mismatches] == [UNKNOWN_PART]
    assert '"99"' in result.mismatches[0].message
