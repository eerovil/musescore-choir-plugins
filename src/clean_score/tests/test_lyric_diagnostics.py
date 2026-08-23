"""
What comes back from placing lyrics: structured mismatches, and the manual editor's
projection round-tripping through the same interface.

These are the two things a caller used to have to recover from printed text.
"""

from src.clean_score.lyric_txt import (
    BLOCK_COUNT,
    NO_SYSTEMS,
    TOO_FEW,
    TOO_MANY,
    blocks_from_cells,
    editor_grid,
    place_lyrics,
)

from .scorebuilder import build_score, placed_lyrics


def _line(text, measure_start=1, parts=("P1",)):
    return [{"measure_start": measure_start,
             "lyrics": [{"parts": list(parts), "text": text}]}]


# --------------------------------------------------------------------------- #
# Mismatches, in fields
# --------------------------------------------------------------------------- #

def test_a_line_that_fits_reports_nothing():
    root = build_score(staff_ids=(1,), measures=1, chords=3)
    result = place_lyrics(root, _line("yk-si kak"), replace=True)
    assert result.ok and result.mismatches == []


def test_too_few_syllables_reports_the_range_staff_and_counts():
    root = build_score(staff_ids=(1,), measures=1, chords=8)
    result = place_lyrics(root, _line("yk-si"), replace=True)
    assert len(result.mismatches) == 1
    m = result.mismatches[0]
    assert m.kind == TOO_FEW
    assert (m.measure_start, m.measure_end) == (1, 1)
    assert m.staff_ids == (1,)
    assert (m.syllables, m.slots) == (2, 8)
    assert "too few tokens" in m.message and not result.ok


def test_too_many_syllables_reports_counts_and_keeps_the_overflow():
    root = build_score(staff_ids=(1,), measures=1, chords=2)
    result = place_lyrics(root, _line("yk-si kak-si kol-me"), replace=True)
    m = result.mismatches[0]
    assert m.kind == TOO_MANY
    assert (m.syllables, m.slots) == (6, 2)
    assert "too many tokens" in m.message
    # Overflow stays visible in the score instead of being dropped.
    assert "kol" in placed_lyrics(root)[1]


def test_one_mismatch_lists_every_staff_it_affects():
    """Staves that share a range and counts are reported once, together."""
    root = build_score(staff_ids=(1, 2), measures=1, chords=8, names={1: "P1", 2: "P2"})
    result = place_lyrics(root, _line("yk-si", parts=("P1", "P2")), replace=True)
    assert len(result.mismatches) == 1
    assert result.mismatches[0].staff_ids == (1, 2)


def test_mismatch_survives_serialization_for_the_browser():
    root = build_score(staff_ids=(1,), measures=1, chords=8)
    d = place_lyrics(root, _line("yk-si"), replace=True).mismatches[0].to_dict()
    assert d == {
        "kind": TOO_FEW, "message": d["message"], "measure_start": 1, "measure_end": 1,
        "staff_ids": [1], "syllables": 2, "slots": 8,
    }


# --------------------------------------------------------------------------- #
# Inferring a missing measure_start from the printed systems
# --------------------------------------------------------------------------- #

def test_null_measure_start_is_filled_from_the_systems():
    root = build_score(staff_ids=(1,), measures=4, chords=3, line_breaks=(2,))
    blocks = [
        {"measure_start": None, "lyrics": [{"parts": ["P1"], "text": "en-sim mäi"}]},
        {"measure_start": None, "lyrics": [{"parts": ["P1"], "text": "toi-nen ri"}]},
    ]
    result = place_lyrics(root, blocks, replace=True)
    assert result.filled_measure_starts == [1, 3]  # the two systems start at m1 and m3
    placed = placed_lyrics(root)
    assert "en-sim" in placed[1] and "toi-nen" in placed[1]


def test_a_score_without_line_breaks_is_one_system():
    """No breaks means the whole score is a single printed system, so the fill still works."""
    root = build_score(staff_ids=(1,), measures=2, chords=3)
    result = place_lyrics(
        root, [{"measure_start": None, "lyrics": [{"parts": ["P1"], "text": "yk-si kak"}]}],
        replace=True,
    )
    assert result.filled_measure_starts == [1]
    assert NO_SYSTEMS not in [m.kind for m in result.mismatches]
    assert "yk-si" in placed_lyrics(root)[1]


def test_nothing_to_infer_from_is_reported_rather_than_guessed():
    root = build_score(staff_ids=(1,), measures=0)  # no measures at all
    result = place_lyrics(
        root, [{"measure_start": None, "lyrics": [{"parts": ["P1"], "text": "yk-si"}]}],
        replace=True,
    )
    assert [m.kind for m in result.mismatches] == [NO_SYSTEMS]
    assert result.filled_measure_starts == []


def test_line_count_not_matching_system_count_is_flagged():
    root = build_score(staff_ids=(1,), measures=4, chords=3, line_breaks=(2,))  # 2 systems
    blocks = [{"measure_start": None, "lyrics": [{"parts": ["P1"], "text": "yk-si"}]}]
    result = place_lyrics(root, blocks, replace=True)  # only 1 line for 2 systems
    assert BLOCK_COUNT in [m.kind for m in result.mismatches]


# --------------------------------------------------------------------------- #
# The manual editor's projection
# --------------------------------------------------------------------------- #

def test_editor_grid_lists_parts_and_systems_and_skips_the_click_staff():
    root = build_score(staff_ids=(1, 2, 3), measures=4, chords=3,
                       names={1: "S", 2: "A", 3: "Click"}, line_breaks=(2,))
    grid = editor_grid(root)
    assert [p.name for p in grid.parts] == ["S", "A"]  # the click staff carries no lyrics
    assert [(s.index, s.start, s.end) for s in grid.systems] == [(0, 1, 2), (1, 3, 4)]
    assert grid.cells == {}  # nothing typed yet


def test_editor_cells_round_trip_through_import_and_back():
    """What the editor shows must come back unchanged after it is imported."""
    root = build_score(staff_ids=(1, 2), measures=4, chords=3,
                       names={1: "S", 2: "A"}, line_breaks=(2,))
    typed = {0: {"S": "en-sim mäi", "A": "al-to yk"}, 1: {"S": "toi-nen ri"}}

    grid = editor_grid(root)
    blocks = blocks_from_cells(grid, typed)
    assert blocks == [
        {"measure_start": 1, "lyrics": [{"parts": ["S"], "text": "en-sim mäi"},
                                        {"parts": ["A"], "text": "al-to yk"}]},
        {"measure_start": 3, "lyrics": [{"parts": ["S"], "text": "toi-nen ri"}]},
    ]
    place_lyrics(root, blocks, replace=True)
    assert editor_grid(root).cells == typed


def test_blank_editor_cells_are_left_out_rather_than_clearing_a_part():
    root = build_score(staff_ids=(1, 2), measures=2, chords=3, names={1: "S", 2: "A"})
    grid = editor_grid(root)
    assert blocks_from_cells(grid, {0: {"S": "  ", "A": ""}}) == []
    assert blocks_from_cells(grid, {"0": {"S": "yk-si kak"}}) == [
        {"measure_start": 1, "lyrics": [{"parts": ["S"], "text": "yk-si kak"}]}
    ]
