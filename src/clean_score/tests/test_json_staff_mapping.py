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


# --------------------------------------------------------------------------- #
# a map written at Clean, bars added by hand in MuseScore afterwards (#482)
# --------------------------------------------------------------------------- #

from src.clean_score.utils import per_system  # noqa: E402

# Printed staff 1 is part 1 in systems 1 and 3 and part 2 in system 2.
THREE_SYSTEMS = [
    {"start": 1, "end": 2, "map": {"1": [1]}},
    {"start": 3, "end": 4, "map": {"1": [2]}},
    {"start": 5, "end": 6, "map": {"1": [1]}},
]
EIGHT = " ".join(f"s{i}" for i in range(8))


def _bars_with_words(root, sid):
    from src.clean_score.lyric_txt import export_lyrics
    bars, measure = [], None
    for line in export_lyrics(root).splitlines():
        line = line.strip()
        if line.startswith("# Measure"):
            measure = int(line.split()[-1])
        elif line and int(line.split("[")[0].split(":")[0]) == sid:
            if any(t != "_" for t in line.partition(":")[2].split()):
                bars.append(measure)
    return bars


def test_a_bar_added_in_system_1_moves_system_2s_words_with_it():
    # System 2 is one bar. One bar added by hand in system 1, its line breaks kept:
    # 6 bars, systems 1-3, 4, 5-6, while the map still says 1-2, 3, 4-5 — read as
    # stored, bar 4 is system 3's and the words would go to part 1.
    root = build_score(staff_ids=(1, 2), measures=6, system_map=[
        {"start": 1, "end": 2, "map": {"1": [1]}},
        {"start": 3, "end": 3, "map": {"1": [2]}},
        {"start": 4, "end": 5, "map": {"1": [1]}},
    ], line_breaks=(3, 4, 6))
    read = per_system.read_system_map(root)
    assert read.state == per_system.MAP_RECOUNTED
    assert [(e["start"], e["end"]) for e in read.entries] == [(1, 3), (4, 4), (5, 6)]

    result = place_lyrics(root, [{"measure_start": 4, "measure_end": 4, "lyrics": [
        {"staff_number": 1, "position": "below", "text": EIGHT}]}])
    assert _bars_with_words(root, 2) == [4]
    assert _bars_with_words(root, 1) == []
    assert not result.mismatches


def test_a_bar_added_and_a_break_taken_out_is_stale():
    root = build_score(staff_ids=(1, 2), measures=7, system_map=THREE_SYSTEMS,
                       line_breaks=(3, 7))
    read = per_system.read_system_map(root)
    assert read.state == per_system.MAP_STALE
    assert [(e["start"], e["end"]) for e in read.entries] == [(1, 2), (3, 4), (5, 6)]


def test_a_break_moved_with_no_bar_added_is_left_alone():
    root = build_score(staff_ids=(1, 2), measures=6, system_map=THREE_SYSTEMS,
                       line_breaks=(3, 4, 6))
    read = per_system.read_system_map(root)
    assert read.state == per_system.MAP_AS_RECORDED
    assert [(e["start"], e["end"]) for e in read.entries] == [(1, 2), (3, 4), (5, 6)]


def test_the_score_itself_is_not_rewritten():
    root = build_score(staff_ids=(1, 2), measures=7, system_map=THREE_SYSTEMS,
                       line_breaks=(3, 5, 7))
    per_system.read_system_map(root)
    tag = root.find(".//metaTag[@name='lyricsSystemMap']")
    assert '"start":3' in tag.text
