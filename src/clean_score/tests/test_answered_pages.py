"""A frozen measurement's grid answer cannot be moved by the songs on this host.

`scripts/answered_vs_reference.py` scores an assembled page with the per-system
grid answered, and that answer is the input the whole finding turns on.  It used
to be read live -- the bands out of `.systems.json` and the grouping out of the
reviewed cleaned score -- both of which are host state that has already moved
under earlier measurements on this map.  So what is pinned here is that the
frozen entry is the *only* thing the run can be answered from: a page nobody
wrote down is refused rather than invented, a song that has moved stops the run
rather than being absorbed, and the grid the rebuild is handed comes off the
manifest whatever the song says now.
"""

import json
from types import SimpleNamespace

import pytest

from scripts import answered_pages, answered_vs_reference


def band(index, start, end, staves):
    return answered_pages.FrozenBand(
        index=index, measure_start=start, measure_end=end,
        grouping=[s.split("+") for s in staves],
    )


def page(bands=None, song="fixture", number=2):
    return answered_pages.FrozenPage(
        song=song, page=number,
        bands=bands or [
            band(1, 1, 4, ["T1+T2", "B1+B2"]),
            band(2, 5, 8, ["T1", "T2", "B1+B2"]),
        ],
        bounds_sha256="aaaa", cleaned_sha256="bbbb",
    )


def written(tmp_path, *pages):
    path = tmp_path / "answered-pages.json"
    answered_pages.save(list(pages), path)
    return path


def layouts(*staff_counts):
    """Stand-ins for `per_system.layout_for_file`, note-bearing staves only."""
    return [
        SimpleNamespace(
            index=n,
            staves=[SimpleNamespace(staff_id=k + 1) for k in range(count)],
        )
        for n, count in enumerate(staff_counts)
    ]


def test_the_manifest_round_trips_the_answer_it_was_given(tmp_path) -> None:
    path = written(tmp_path, page())

    back = answered_pages.frozen_page("fixture", 2, path)

    assert back.groupings == [[["T1", "T2"], ["B1", "B2"]],
                              [["T1"], ["T2"], ["B1", "B2"]]]
    assert [b.measure_start for b in back.bands] == [1, 5]
    assert json.loads(path.read_text())["pages"][0]["bands"][1]["staves"] == [
        "T1", "T2", "B1+B2"
    ]


def test_a_page_nobody_froze_is_refused_rather_than_read_off_the_song(
    tmp_path,
) -> None:
    path = written(tmp_path, page())

    with pytest.raises(answered_pages.NotFrozen) as refused:
        answered_pages.frozen_page("fixture", 3, path)

    assert "fixture-p3" in str(refused.value)
    assert "--record" in str(refused.value)


def test_a_live_grouping_edited_underneath_a_frozen_run_stops_it(tmp_path) -> None:
    """The regression this file exists for: the song moved, the run refuses."""
    path = written(tmp_path, page())
    frozen = answered_pages.frozen_page("fixture", 2, path)
    now = page(bands=[
        band(1, 1, 4, ["T1+T2", "B1+B2"]),
        band(2, 5, 8, ["T1+T2", "B1", "B2"]),      # the operator answer changed
    ])

    with pytest.raises(answered_pages.Drifted) as refused:
        answered_pages.check(frozen, now)

    said = str(refused.value)
    assert "fixture-p2 s2" in said
    assert "T1, T2, B1+B2" in said                  # what the run was frozen with
    assert "T1+T2, B1, B2" in said                  # what the song says now
    # And the frozen answer is untouched by the song having moved.
    assert answered_pages.frozen_page("fixture", 2, path).groupings[1] == [
        ["T1"], ["T2"], ["B1", "B2"]
    ]


def test_a_band_whose_bars_moved_stops_the_run_too(tmp_path) -> None:
    path = written(tmp_path, page())
    frozen = answered_pages.frozen_page("fixture", 2, path)
    now = page(bands=[
        band(1, 1, 4, ["T1+T2", "B1+B2"]),
        band(2, 5, 9, ["T1", "T2", "B1+B2"]),      # the band was dragged
    ])

    with pytest.raises(answered_pages.Drifted) as refused:
        answered_pages.check(frozen, now)

    assert "bars 5-8" in str(refused.value)
    assert "5-9" in str(refused.value)


def test_a_band_inserted_into_the_page_is_named_as_such(tmp_path) -> None:
    """Bands are positional, so a new one silently re-points every later answer."""
    path = written(tmp_path, page())
    frozen = answered_pages.frozen_page("fixture", 2, path)
    now = page(bands=[
        band(1, 1, 2, ["T1+T2", "B1+B2"]),
        band(3, 3, 4, ["T1+T2", "B1+B2"]),
        band(2, 5, 8, ["T1", "T2", "B1+B2"]),
    ])

    with pytest.raises(answered_pages.Drifted) as refused:
        answered_pages.check(frozen, now)

    assert "[1, 2]" in str(refused.value)
    assert "[1, 3, 2]" in str(refused.value)


def test_a_host_with_no_song_at_all_is_not_drift(tmp_path) -> None:
    """The frozen entry is the whole input, so a fresh clone scores the same run."""
    frozen = answered_pages.frozen_page("fixture", 2, written(tmp_path, page()))

    answered_pages.check(frozen, None)


def test_an_edit_that_left_this_page_alone_does_not_stop_the_run(tmp_path) -> None:
    """Only what the measurement consumed is compared, not the file's hash.

    A cleaned score can be edited in a bar this page does not cover. Stopping for
    that would teach somebody to re-record without reading, which is the habit
    the refusal exists to prevent.
    """
    frozen = answered_pages.frozen_page("fixture", 2, written(tmp_path, page()))
    now = page()
    object.__setattr__(now, "cleaned_sha256", "something else entirely")

    answered_pages.check(frozen, now)


def test_the_grid_is_filled_in_from_the_frozen_answer(tmp_path) -> None:
    """What the rebuild is handed, per system and per staff."""
    frozen = answered_pages.frozen_page("fixture", 2, written(tmp_path, page()))

    answers = answered_vs_reference.grid_answers(frozen, layouts(2, 3), "-")

    assert answers == {0: {1: "T1,T2", 2: "B1,B2"},
                       1: {1: "T1", 2: "T2", 3: "B1,B2"}}


def test_a_staff_the_frozen_answer_does_not_name_is_cleared_not_guessed(
    tmp_path,
) -> None:
    frozen = answered_pages.frozen_page("fixture", 2, written(tmp_path, page()))

    answers = answered_vs_reference.grid_answers(frozen, layouts(3, 3), "-")

    assert answers[0] == {1: "T1,T2", 2: "B1,B2", 3: "-"}


def test_a_file_with_more_systems_than_the_frozen_page_is_refused(tmp_path) -> None:
    """Answers are positional, so a mismatch would answer the wrong system."""
    frozen = answered_pages.frozen_page("fixture", 2, written(tmp_path, page()))

    with pytest.raises(RuntimeError, match="3 systems in the file against 2"):
        answered_vs_reference.grid_answers(frozen, layouts(2, 3, 2), "-")
