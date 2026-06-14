"""
Unit tests for mapping PDF-derived JSON lyrics (staff_number + position) to output
staff ids via the clean_score 'lyricsStaffMap' metaTag.
"""

from lxml import etree

from src.clean_score.lyric_txt import (
    convert_lyrics_format_to_legacy,
    read_lyrics_staff_map,
    read_lyrics_system_map,
)


# Laulun aika layout: printed staff 1 -> output 1,2 (divisi); 2 -> 3; 3 -> 4,5 (divisi); 4 -> 6.
STAFF_MAP = {1: [1, 2], 2: [3], 3: [4, 5], 4: [6]}


def test_read_lyrics_staff_map():
    score = etree.fromstring(
        b"<museScore><Score>"
        b'<metaTag name="composer"/>'
        b'<metaTag name="lyricsStaffMap">1:1,2;2:3;3:4,5;4:6</metaTag>'
        b"</Score></museScore>"
    )
    assert read_lyrics_staff_map(score) == STAFF_MAP


def test_read_lyrics_staff_map_absent():
    score = etree.fromstring(b"<museScore><Score/></museScore>")
    assert read_lyrics_staff_map(score) == {}


def test_unison_single_position_maps_to_both_divisi_staves():
    """Printed divisi staff with only 'below' in the block -> both output voices (unison)."""
    data = [
        {
            "measure_start": 1,
            "lyrics": [
                {"staff_number": 1, "position": "below", "text": "Nyt u-kot"},
            ],
        }
    ]
    legacy = convert_lyrics_format_to_legacy(data, staff_map=STAFF_MAP)
    assert legacy == [{"measure_start": 1, "1": "Nyt u-kot", "2": "Nyt u-kot"}]


def test_true_divisi_both_positions_split_upper_lower():
    """Printed divisi staff with both 'above' and 'below' in the block -> upper/lower split."""
    data = [
        {
            "measure_start": 20,
            "lyrics": [
                {"staff_number": 1, "position": "above", "text": "ylä"},
                {"staff_number": 1, "position": "below", "text": "ala"},
            ],
        }
    ]
    legacy = convert_lyrics_format_to_legacy(data, staff_map=STAFF_MAP)
    assert legacy == [{"measure_start": 20, "1": "ylä", "2": "ala"}]


def test_divisi_is_per_block_not_global():
    """A staff that splits in one block stays unison in a block that has only one position."""
    data = [
        {"measure_start": 1, "lyrics": [{"staff_number": 1, "position": "below", "text": "uni"}]},
        {
            "measure_start": 20,
            "lyrics": [
                {"staff_number": 1, "position": "above", "text": "hi"},
                {"staff_number": 1, "position": "below", "text": "lo"},
            ],
        },
    ]
    legacy = convert_lyrics_format_to_legacy(data, staff_map=STAFF_MAP)
    # block 1: unison -> both 1 and 2; block 20: split -> 1=hi, 2=lo
    assert legacy[0] == {"measure_start": 1, "1": "uni", "2": "uni"}
    assert legacy[1] == {"measure_start": 20, "1": "hi", "2": "lo"}


def test_single_output_staff():
    data = [{"measure_start": 1, "lyrics": [{"staff_number": 2, "position": "below", "text": "x"}]}]
    legacy = convert_lyrics_format_to_legacy(data, staff_map=STAFF_MAP)
    assert legacy == [{"measure_start": 1, "3": "x"}]


def test_explicit_parts_override_wins():
    """An explicit parts list overrides the staff_number/position derivation."""
    data = [
        {
            "measure_start": 1,
            "lyrics": [
                {"staff_number": 1, "position": "below", "text": "y", "parts": [4, 5]},
            ],
        }
    ]
    legacy = convert_lyrics_format_to_legacy(data, staff_map=STAFF_MAP)
    assert legacy == [{"measure_start": 1, "4": "y", "5": "y"}]


def test_verse_2_ignored():
    data = [
        {
            "measure_start": 1,
            "lyrics": [
                {"staff_number": 2, "position": "below", "text": "v1"},
                {"staff_number": 2, "position": "below", "text": "v2", "verse": 2},
            ],
        }
    ]
    legacy = convert_lyrics_format_to_legacy(data, staff_map=STAFF_MAP)
    assert legacy == [{"measure_start": 1, "3": "v1"}]


def test_no_staff_map_falls_back_to_staff_number():
    """Without a map (unsplit score), staff_number is used as the output staff id directly."""
    data = [{"measure_start": 1, "lyrics": [{"staff_number": 2, "position": "below", "text": "z"}]}]
    legacy = convert_lyrics_format_to_legacy(data, staff_map={})
    assert legacy == [{"measure_start": 1, "2": "z"}]


# Per-system map: printed staff numbering shifts per system as parts are omitted.
SYSTEM_MAP = [
    {"start": 1, "end": 6, "map": {1: [1, 2], 2: [4]}},
    {"start": 26, "end": 29, "map": {1: [1], 2: [2], 3: [4]}},
]


def test_read_lyrics_system_map():
    score = etree.fromstring(
        b"<museScore><Score>"
        b'<metaTag name="lyricsSystemMap">'
        b'[{"start":1,"end":6,"map":{"1":[1,2],"2":[4]}},'
        b'{"start":26,"end":29,"map":{"1":[1],"2":[2],"3":[4]}}]'
        b"</metaTag></Score></museScore>"
    )
    assert read_lyrics_system_map(score) == SYSTEM_MAP


def test_read_lyrics_system_map_absent():
    score = etree.fromstring(b"<museScore><Score/></museScore>")
    assert read_lyrics_system_map(score) is None


def test_system_map_routes_block_by_measure_range():
    """A lyric's block uses the map for the system covering its measure_start."""
    data = [
        # m1-6: printed 1 (divisi, unison) -> output 1,2 ; printed 2 -> output 4 (B).
        {"measure_start": 1, "lyrics": [
            {"staff_number": 1, "position": "below", "text": "ten"},
            {"staff_number": 2, "position": "below", "text": "bass"},
        ]},
        # m26-29: T3 omitted, so printed 3 -> output 4 (B), NOT output 3 (T3).
        {"measure_start": 26, "lyrics": [
            {"staff_number": 3, "position": "below", "text": "lowvoice"},
        ]},
    ]
    legacy = convert_lyrics_format_to_legacy(data, system_map=SYSTEM_MAP)
    assert legacy[0] == {"measure_start": 1, "1": "ten", "2": "ten", "4": "bass"}
    assert legacy[1] == {"measure_start": 26, "4": "lowvoice"}


# Part-name mapping: address voices by trackName (robust to printed-staff order).
def _named_score():
    parts = [("T1", 1), ("T2", 2), ("T3", 3), ("B", 4)]
    xml = b"<museScore><Score>"
    for name, sid in parts:
        xml += (f'<Part><trackName>{name}</trackName><Staff id="{sid}"/></Part>').encode()
    xml += b"</Score></museScore>"
    return etree.fromstring(xml)


def test_read_part_name_map():
    from src.clean_score.lyric_txt import read_part_name_map
    assert read_part_name_map(_named_score()) == {"T1": 1, "T2": 2, "T3": 3, "B": 4}


def test_parts_by_name_resolve_to_output_staves():
    name_map = {"T1": 1, "T2": 2, "T3": 3, "B": 4}
    data = [
        {"measure_start": 20, "lyrics": [
            {"parts": ["T3"], "text": "kuol-leet-kin"},
            {"parts": ["T1", "T2"], "text": "kuol-leet-kin-me"},
        ]},
    ]
    legacy = convert_lyrics_format_to_legacy(data, name_map=name_map)
    # T3 -> staff 3, T1/T2 -> staves 1 and 2 (no swap, regardless of printed order)
    assert legacy[0] == {"measure_start": 20, "1": "kuol-leet-kin-me",
                         "2": "kuol-leet-kin-me", "3": "kuol-leet-kin"}


def test_parts_mixes_names_and_ids_and_singular_part():
    name_map = {"T1": 1, "B": 4}
    data = [{"measure_start": 1, "lyrics": [
        {"part": "B", "text": "bass"},
        {"parts": ["T1", 4], "text": "both"},
    ]}]
    legacy = convert_lyrics_format_to_legacy(data, name_map=name_map)
    assert legacy[0] == {"measure_start": 1, "1": "both", "4": "bass both"}
