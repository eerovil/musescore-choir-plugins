"""Which staves one printed band is given, and which reading of the page wins.

A band gets its staves from three places, and until #161 they were asked in the
wrong order: a reading of the whole song beat everything, including the score's
own per-system map.  A whole-song reading cannot describe a page whose systems
differ, and `laulun-aika-3`'s do -- two staves for four systems, three for one,
four for the last -- so one reading was applied to five bands it does not
describe and produced three of the worst six references in the corpus.

What these pin is the ordering: **specificity, not authority.**  A reading of
this system, then the score's map, then a reading of the song.
"""

import json

import pytest

from scripts import implode_report, make_stem_fixture
from src.clean_score.tests.test_implode import score, with_meta

PARTS = [(1, "T1"), (2, "T2"), (3, "T3"), (4, "B")]
#: One system of two staves and one of three, the shape this all exists for.
SYSTEM_MAP = json.dumps(
    [
        {"start": 1, "end": 4, "map": {"1": [1, 2], "2": [4]}},
        {"start": 5, "end": 8, "map": {"1": [1, 2], "2": [3], "3": [4]}},
    ]
)


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    """A manifest this test owns, in the shape `fixtures/omr-songs.json` has."""

    def write(grouping: dict) -> None:
        path = tmp_path / "omr-songs.json"
        path.write_text(json.dumps({"songs": {"a-song": {"grouping": grouping}}}))
        monkeypatch.setattr(implode_report, "MANIFEST", path)

    return write


def test_a_reading_of_this_system_beats_the_score_and_the_song(manifest) -> None:
    manifest(
        {
            "override": {
                "printed": [["T3", "T1", "T2"], ["B"]],
                "systems": [{"start": 5, "end": 8, "printed": [["T3"], ["T1", "T2"], ["B"]]}],
            }
        }
    )
    root = with_meta(score(PARTS, bars=8), "lyricsSystemMap", SYSTEM_MAP)

    # The map has these three staves too, and puts T3 second: it numbers
    # positions by musical rank, and T3 ranks below the parts it is printed over.
    assert make_stem_fixture.system_override(root, 5) == [["T1", "T2"], ["T3"], ["B"]]
    assert make_stem_fixture.band_grouping("a-song", root, 5) == [["T3"], ["T1", "T2"], ["B"]]


def test_a_reading_covers_only_the_bars_it_names(manifest) -> None:
    manifest(
        {"override": {"systems": [{"start": 5, "end": 8, "printed": [["T3"], ["T1", "T2"], ["B"]]}]}}
    )
    root = with_meta(score(PARTS, bars=8), "lyricsSystemMap", SYSTEM_MAP)

    # Inclusive at both ends, and the other system falls through to the map.
    assert implode_report.system_override_for("a-song", 5) is not None
    assert implode_report.system_override_for("a-song", 8) is not None
    assert implode_report.system_override_for("a-song", 4) is None
    assert make_stem_fixture.band_grouping("a-song", root, 1) == [["T1", "T2"], ["B"]]


def test_the_score_s_own_map_beats_a_reading_of_the_whole_song(manifest) -> None:
    manifest({"override": {"printed": [["T3", "T1", "T2"], ["B"]]}})
    root = with_meta(score(PARTS, bars=8), "lyricsSystemMap", SYSTEM_MAP)

    # This is the correction. The whole-song reading says two staves for every
    # system of the piece; the map knows the second one prints three.
    assert make_stem_fixture.band_grouping("a-song", root, 5) == [["T1", "T2"], ["T3"], ["B"]]


def test_a_score_with_no_map_still_takes_the_reading_of_the_song(manifest) -> None:
    manifest({"override": {"printed": [["T3", "T1", "T2"], ["B"]]}})
    root = score(PARTS, bars=8)

    assert make_stem_fixture.band_grouping("a-song", root, 5) == [["T3", "T1", "T2"], ["B"]]


def test_a_song_nobody_has_read_is_left_to_its_map(manifest) -> None:
    manifest({})
    root = with_meta(score(PARTS, bars=8), "lyricsSystemMap", SYSTEM_MAP)

    assert implode_report.system_override_for("a-song", 5) is None
    assert make_stem_fixture.band_grouping("a-song", root, 5) == [["T1", "T2"], ["T3"], ["B"]]
