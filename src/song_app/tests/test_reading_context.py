"""What a reading choice says about where it lands and what it would undo (#368).

On Annin laulu bar 8 a person picked reading "a" for both basses, and that gave B1
the line the page prints for B2 — the scan's own swap, which page-checked `bar`
fixes had already undone — and the clean failed on the clash. These pin the four
things the card asked for: a bar a recorded fix already wrote is not offered, an
option that is another part's current line says so, what fixes.json already says
about the bar is handed over with the choice, and the printed system crop can box
the staff and bar the choice is about.
"""
import json

from lxml import etree
from PIL import Image, ImageDraw

from src.clean_score.utils.score_fixes import FixError
from src.song_app import bar_readings, problems, system_finder
from src.song_app.tests.test_bar_readings import _printed, make_song  # noqa: F401

import pytest


def _write_fixes(song, entries):
    with open(song.path("fixes.json"), "w", encoding="utf-8") as fh:
        json.dump(entries, fh)


def _bar_fix(staff, measure=3, why="read against the page"):
    return {"kind": "bar", "staff": staff, "measure": measure,
            "from": ["quarter:48", "quarter:50"], "to": [], "why": why}


def test_a_bar_a_recorded_fix_wrote_is_not_offered(make_song):
    song = make_song()
    _write_fixes(song, [_bar_fix(1, why="m3: the page prints C D quarters")])
    [offer] = bar_readings.offers(song)
    assert offer["decision"] == {"answered": {
        "kind": "bar", "part": "B1", "why": "m3: the page prints C D quarters"}}
    with pytest.raises(FixError, match="already been decided"):
        bar_readings.record_pick(song, offer["id"], "b")


@pytest.mark.parametrize("kind", ["pitch", "rhythm", "duration", "dropnote", "addnote"])
def test_every_kind_that_writes_notes_answers_the_bar(make_song, kind):
    song = make_song()
    _write_fixes(song, [{"kind": kind, "staff": 1, "measure": 3, "why": "page"}])
    [offer] = bar_readings.offers(song)
    assert offer["decision"]["answered"]["kind"] == kind


def test_an_unmark_answers_only_a_doubt_about_the_notes(make_song):
    song = make_song()
    _write_fixes(song, [{"kind": "unmark", "staff": 1, "measure": 3, "text": "slur?"}])
    [offer] = bar_readings.offers(song)
    assert offer["decision"] is None
    _write_fixes(song, [{"kind": "unmark", "staff": 1, "measure": 3, "text": "notes?",
                         "why": "read against the page: correct"}])
    [offer] = bar_readings.offers(song)
    assert offer["decision"]["answered"]["why"] == "read against the page: correct"


def test_a_fix_on_another_bar_or_staff_does_not_answer_this_one(make_song):
    song = make_song(staves=((1, "B1", 0), (2, "B2", 7)))
    _write_fixes(song, [_bar_fix(1, measure=2), _bar_fix(2)])
    [offer] = bar_readings.offers(song)
    assert offer["part"] == "B1" and offer["decision"] is None


def test_a_bar_deleted_after_the_fix_moves_it(make_song):
    song = make_song()
    # Written for bar 4; a later delbar of bar 1 makes that bar 3.
    _write_fixes(song, [_bar_fix(1, measure=4),
                        {"kind": "delbar", "measure": 1, "from": ["measure:R"]}])
    root = etree.parse(song.cleaned_path()).getroot()
    staff = root.find(".//Score/Staff")
    staff.remove(staff.find("Measure"))
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")
    [offer] = bar_readings.offers(song)
    assert offer["measure"] == 2 and offer["decision"] is None


def test_a_bar_the_fix_rewrote_is_said_as_answered_on_that_part(make_song):
    """The Annin laulu case: after the fix, homr's reading matches no part, or the
    wrong one, so it is said as answered on the part the fix wrote."""
    song = make_song(staves=((1, "B1", 2), (2, "B2", 7)))  # neither holds C D now
    _printed(song, {"1": [1, 2]})
    assert bar_readings.offers(song) == []
    _write_fixes(song, [_bar_fix(2, why="voices swapped")])
    [offer] = bar_readings.offers(song)
    assert offer["part"] == "B2" and offer["decision"]["answered"]["why"] == "voices swapped"


def _set_bar(song, staff_id, chords):
    """Write bar 3 of a staff as (durationType, pitch) chords."""
    root = etree.parse(song.cleaned_path()).getroot()
    staff = root.find(f".//Score/Staff[@id='{staff_id}']")
    voice = staff.findall("Measure")[2].find("voice")
    for child in list(voice):
        voice.remove(child)
    for duration, pitch in chords:
        chord = etree.SubElement(voice, "Chord")
        if duration.endswith("."):
            etree.SubElement(chord, "dots").text = "1"
        etree.SubElement(chord, "durationType").text = duration.rstrip(".")
        note = etree.SubElement(chord, "Note")
        etree.SubElement(note, "pitch").text = str(pitch)
        etree.SubElement(note, "tpc").text = "14"
    etree.ElementTree(root).write(song.cleaned_path(), encoding="UTF-8")


def test_an_option_that_is_the_other_parts_line_says_so(make_song):
    song = make_song(staves=((1, "B1", 0), (2, "B2", 0)))
    _printed(song, {"1": [1, 2]})
    # B2 sings reading b's notes now: a dotted quarter and an eighth.
    _set_bar(song, 2, [("quarter.", 48), ("eighth", 50)])
    [offer] = bar_readings.offers(song)
    assert offer["part"] == "B1"
    assert [o["line_of"] for o in offer["options"]] == [None, "B2", None]

    [row] = [r for r in problems.problems(song) if r["choices"]]
    [choice] = row["choices"]
    assert choice["title"] == "Bar 3, B1: which notes does the page print for B1?"
    assert "the line B2 has now" in choice["options"][1]["label"]
    assert choice["options"][1]["line_of"] == "B2"
    assert row["staff_in_system"] == 1 and row["voice_on_staff"] == 1


def test_what_fixes_json_says_about_the_bar_comes_with_the_choice(make_song):
    song = make_song(staves=((1, "B1", 0), (2, "B2", 7)))
    _printed(song, {"1": [1, 2]})
    _write_fixes(song, [
        {"kind": "slur", "staff": 1, "measure": 3, "index": 0, "span": 1,
         "why": "the page prints a slur"},
        _bar_fix(2, why="B2 read against the page"),
        {"kind": "unmark", "staff": 1, "measure": 3, "text": "slur?"},
        {"kind": "slur", "staff": 1, "measure": 2, "index": 0, "span": 1, "why": "other bar"}])
    [offer] = bar_readings.offers(song)
    assert offer["decision"] is None
    assert offer["fixes"] == [
        {"kind": "slur", "part": "B1", "why": "the page prints a slur", "same_staff": True},
        {"kind": "bar", "part": "B2", "why": "B2 read against the page", "same_staff": False}]
    [row] = [r for r in problems.problems(song) if r["choices"]]
    assert row["choices"][0]["fixes"] == offer["fixes"]


# ---------------------------------------------------------------- the box on the crop


def _system(staves=2, bars=3, stems=True, width=900):
    """A drawn system: five-line staves, a systemic opening line, barlines, notes."""
    space, gap = 12, 70
    height = 40 + staves * (4 * space + gap)
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    left, right = 60, width - 30
    tops = [30 + k * (4 * space + gap) for k in range(staves)]
    for top in tops:
        for line in range(5):
            draw.line([(left, top + line * space), (right, top + line * space)], fill=0, width=1)
    draw.line([(left, tops[0]), (left, tops[-1] + 4 * space)], fill=0, width=3)
    xs = [left + 60 + (right - left - 60) * k // bars for k in range(1, bars + 1)]
    xs[-1] = right
    for top in tops:
        for x in xs:
            draw.line([(x, top), (x, top + 4 * space)], fill=0, width=2)
        if stems:
            # A chord spanning the staff with its stem up through all of it, in
            # the middle of each bar, the same on every staff: a full-height column
            # lined up across the system that is not a barline.
            starts = [left + 60] + xs[:-1]
            for a, b in zip(starts, xs):
                mid = (a + b) // 2
                for y in (top + space * 3 // 2, top + space * 7 // 2):
                    draw.ellipse([mid - 7, y - 5, mid + 7, y + 5], fill=0)
                draw.line([(mid + 6, top + 4 * space), (mid + 6, top - 2 * space)], fill=0, width=2)
    return image, tops, space, xs


def test_the_box_is_round_the_staff_and_bar_asked_for():
    image, tops, space, xs = _system()
    box = system_finder.bar_box(image, 2, 2, 2, 3)
    width, height = image.size
    assert box["bar"] is True
    assert box["top"] * height < tops[1] < tops[1] + 4 * space < box["bottom"] * height
    assert box["bottom"] * height < height
    assert abs(box["left"] * width - xs[0]) <= 3 and abs(box["right"] * width - xs[1]) <= 3


def test_the_first_bar_starts_where_the_staff_does():
    image, tops, space, xs = _system()
    box = system_finder.bar_box(image, 1, 2, 1, 3)
    assert box["bar"] and abs(box["left"] * image.size[0] - 60) <= 3
    assert box["bottom"] * image.size[1] < tops[1]


def test_a_bar_count_that_does_not_add_up_boxes_the_whole_staff():
    image, _, _, _ = _system()
    box = system_finder.bar_box(image, 1, 2, 2, 4)  # the score says four bars, three printed
    assert box["bar"] is False and (box["left"], box["right"]) == (0.0, 1.0)


def test_a_crop_showing_other_staves_than_the_score_says_boxes_nothing():
    image, _, _, _ = _system(staves=3)
    assert system_finder.bar_box(image, 1, 2, 1, 3) is None
