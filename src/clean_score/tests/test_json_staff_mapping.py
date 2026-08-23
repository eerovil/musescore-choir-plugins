"""
Routing tests: which output staff a PDF-derived lyric line lands on.

The JSON the LLM produces addresses printed staves (`staff_number` + `position`) or
part names; the score carries the maps that translate those to output staves. These
drive the real placement interface — build a score, place lyrics, read back where the
words ended up — rather than the conversion step inside it.
"""

from src.clean_score.lyric_txt import place_lyrics

from .scorebuilder import build_score, placed_lyrics

# Laulun aika layout: printed staff 1 -> output 1,2 (divisi); 2 -> 3; 3 -> 4,5 (divisi); 4 -> 6.
STAFF_MAP = "1:1,2;2:3;3:4,5;4:6"

# Per-system map: printed staff numbering shifts per system as parts are omitted.
SYSTEM_MAP = [
    {"start": 1, "end": 6, "map": {"1": [1, 2], "2": [4]}},
    {"start": 26, "end": 29, "map": {"1": [1], "2": [2], "3": [4]}},
]


def _place(root, lyrics, measure_start=1):
    place_lyrics(root, [{"measure_start": measure_start, "lyrics": lyrics}], replace=True)
    return placed_lyrics(root)


# --------------------------------------------------------------------------- #
# printed staff + position -> output staves
# --------------------------------------------------------------------------- #

def test_unison_single_position_maps_to_both_divisi_staves():
    """Printed divisi staff with only 'below' in the block -> both output voices (unison)."""
    root = build_score(staff_map=STAFF_MAP)
    placed = _place(root, [{"staff_number": 1, "position": "below", "text": "Nyt u-kot"}])
    assert placed == {1: "Nyt u-kot", 2: "Nyt u-kot"}


def test_true_divisi_both_positions_split_upper_lower():
    """Printed divisi staff with both 'above' and 'below' in the block -> upper/lower split."""
    root = build_score(staff_map=STAFF_MAP)
    placed = _place(root, [
        {"staff_number": 1, "position": "above", "text": "y-lä"},
        {"staff_number": 1, "position": "below", "text": "a-la"},
    ])
    assert placed == {1: "y-lä", 2: "a-la"}


def test_divisi_is_per_block_not_global():
    """A staff that splits in one block stays unison in a block that has only one position."""
    root = build_score(staff_map=STAFF_MAP, measures=4)
    place_lyrics(root, [
        {"measure_start": 1, "lyrics": [
            {"staff_number": 1, "position": "below", "text": "u-ni"}]},
        {"measure_start": 3, "lyrics": [
            {"staff_number": 1, "position": "above", "text": "hi"},
            {"staff_number": 1, "position": "below", "text": "lo"}]},
    ], replace=True)
    placed = placed_lyrics(root)
    # block 1 unison -> both voices; block 2 split -> 1=hi, 2=lo
    assert placed[1] == "u-ni hi"
    assert placed[2] == "u-ni lo"


def test_single_output_staff():
    root = build_score(staff_map=STAFF_MAP)
    assert _place(root, [{"staff_number": 2, "position": "below", "text": "x"}]) == {3: "x"}


def test_explicit_parts_override_the_positional_map():
    root = build_score(staff_map=STAFF_MAP)
    placed = _place(root, [
        {"staff_number": 1, "position": "below", "text": "y", "parts": [4, 5]}])
    assert placed == {4: "y", 5: "y"}


def test_verse_2_is_ignored():
    root = build_score(staff_map=STAFF_MAP)
    placed = _place(root, [
        {"staff_number": 2, "position": "below", "text": "v1"},
        {"staff_number": 2, "position": "below", "text": "v2", "verse": 2},
    ])
    assert placed == {3: "v1"}


def test_no_staff_map_falls_back_to_staff_number():
    """Without a map (unsplit score), staff_number is the output staff id directly."""
    root = build_score()  # no lyricsStaffMap metaTag
    assert _place(root, [{"staff_number": 2, "position": "below", "text": "z"}]) == {2: "z"}


# --------------------------------------------------------------------------- #
# per-system map: the printed numbering shifts as parts drop out
# --------------------------------------------------------------------------- #

def test_system_map_routes_each_block_by_its_measure_range():
    root = build_score(staff_ids=(1, 2, 3, 4), measures=30, system_map=SYSTEM_MAP)
    place_lyrics(root, [
        # m1-6: printed 1 (divisi, unison) -> output 1,2 ; printed 2 -> output 4 (B).
        {"measure_start": 1, "lyrics": [
            {"staff_number": 1, "position": "below", "text": "ten"},
            {"staff_number": 2, "position": "below", "text": "bass"}]},
        # m26-29: T3 omitted, so printed 3 -> output 4 (B), NOT output 3 (T3).
        {"measure_start": 26, "lyrics": [
            {"staff_number": 3, "position": "below", "text": "low-voice"}]},
    ], replace=True)
    placed = placed_lyrics(root)
    assert placed[1] == "ten" and placed[2] == "ten"
    assert placed[4] == "bass low-voice"
    assert 3 not in placed  # T3 sings nothing here


# --------------------------------------------------------------------------- #
# part names: immune to printed order
# --------------------------------------------------------------------------- #

NAMES = {1: "T1", 2: "T2", 3: "T3", 4: "B"}


def test_parts_by_name_resolve_to_output_staves():
    root = build_score(staff_ids=(1, 2, 3, 4), names=NAMES)
    placed = _place(root, [
        {"parts": ["T3"], "text": "kuol-leet-kin"},
        {"parts": ["T1", "T2"], "text": "kuol-leet-kin-me"},
    ])
    assert placed == {1: "kuol-leet-kin-me", 2: "kuol-leet-kin-me", 3: "kuol-leet-kin"}


def test_parts_mixes_names_and_ids_and_a_singular_part():
    root = build_score(staff_ids=(1, 2, 3, 4), names=NAMES)
    placed = _place(root, [
        {"part": "B", "text": "bass"},
        {"parts": ["T1", 4], "text": "both"},
    ])
    assert placed[1] == "both"
    assert placed[4] == "bass both"


def test_names_win_over_a_printed_map_that_disagrees():
    """An ossia printed on top breaks staff_number order; names bypass it."""
    root = build_score(staff_ids=(1, 2, 3, 4), names=NAMES, staff_map="1:3;2:1;3:2;4:4")
    placed = _place(root, [{"parts": ["T3"], "staff_number": 2, "text": "mine"}])
    assert placed == {3: "mine"}
